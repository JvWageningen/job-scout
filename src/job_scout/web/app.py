"""FastAPI application for the job-scout web dashboard."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from loguru import logger
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response

from job_scout import feedback, ntfy_topic, progress
from job_scout.config import (
    GLOBAL_FIELDS,
    apply_user_init,
    build_effective_config,
    list_users,
    load_config,
    load_secrets,
    load_user_config,
    save_config,
    save_user_config,
    set_config_value,
    update_secrets,
    user_db_path,
    user_logs_dir,
)
from job_scout.database import Database
from job_scout.llm.factory import build_raw_client_for_test, get_llm_client
from job_scout.models import (
    Config,
    CvProfile,
    JobListing,
    JobStatus,
    PersonSearchResult,
)
from job_scout.notify.factory import build_raw_notifier_for_test
from job_scout.scheduler import check_schedule_status, install_schedule, remove_schedule
from job_scout.weekly_schedule import next_run_after, parse_slots
from job_scout.wol import normalise_mac, wake_and_wait

# How many upcoming runs the schedule panel previews, so a change can be
# sanity-checked before it fires.
_SCHEDULE_PREVIEW_COUNT = 3

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient

# Global registry for tracking run status
# Keyed by user name (or None for global)
_run_registry: dict[str | None, dict[str, Any]] = {}
_registry_lock = threading.Lock()


def _attach_company_reviews(db: Database, jobs: list[JobListing]) -> None:
    """Attach cached company work-quality reviews to jobs for display.

    Reviews are cached per company (not per job), so look each up by company.

    Args:
        db: Database holding the company-review cache.
        jobs: Jobs to enrich in place.
    """
    from job_scout.models import CompanyReview  # noqa: PLC0415

    cache: dict[str, CompanyReview | None] = {}
    for job in jobs:
        key = job.company.lower()
        if key not in cache:
            raw = db.get_company_review(job.company, max_age_days=365)
            cache[key] = CompanyReview.model_validate_json(raw) if raw else None
        job.company_review = cache[key]


def _require_user(user: object) -> str:
    """Validate that a request names an existing user.

    Args:
        user: The raw 'user' value from the request.

    Returns:
        The validated user name.

    Raises:
        HTTPException: If the user is missing or unknown.
    """
    if not user or not isinstance(user, str):
        raise HTTPException(status_code=400, detail="User is required")
    if user not in list_users():
        raise HTTPException(status_code=404, detail=f"User '{user}' not found")
    return user


def _load_cv_profile_quietly(user: str) -> CvProfile | None:
    """Load a user's parsed CV profile, returning None instead of raising.

    The coach and PDF import both work better with CV context but must not
    fail when no CV is configured or parsing is unavailable.

    Args:
        user: User name.

    Returns:
        The parsed CvProfile, or None when unavailable.
    """
    from job_scout.cv_parser import parse_cv  # noqa: PLC0415
    from job_scout.cv_profile import get_or_parse_cv_profile  # noqa: PLC0415
    from job_scout.llm.base import LLMError  # noqa: PLC0415

    config = build_effective_config(user)
    if not config.cv_path:
        return None
    try:
        raw = parse_cv(config.cv_path)
        if not raw:
            return None
        client = get_llm_client(config)
        return get_or_parse_cv_profile(raw, client, Database(user_db_path(user)))
    except (FileNotFoundError, LLMError, ValueError) as exc:
        logger.debug(f"CV profile unavailable for {user}: {exc}")
        return None


def _load_cv_text(user: str) -> str:
    """Return the raw extracted text of a user's CV.

    Args:
        user: User name.

    Returns:
        The extracted CV text.

    Raises:
        HTTPException: If no CV is configured or it cannot be read.
    """
    from job_scout.cv_parser import parse_cv  # noqa: PLC0415

    config = build_effective_config(user)
    if not config.cv_path:
        raise HTTPException(
            status_code=400,
            detail="No CV configured for this user. Set a CV path under Profile.",
        )
    try:
        text = parse_cv(config.cv_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not text or not text.strip():
        raise HTTPException(
            status_code=400, detail=f"No readable text in {config.cv_path}"
        )
    return text


def _require_job(user: str, job_id: object) -> JobListing:
    """Load a job by id for a user.

    Args:
        user: User name.
        job_id: The job's database id.

    Returns:
        The job listing.

    Raises:
        HTTPException: If the id is missing or no such job exists.
    """
    if job_id in (None, ""):
        raise HTTPException(status_code=400, detail="A job must be selected.")
    try:
        ident = int(cast(int, job_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid job id.") from exc
    job = Database(user_db_path(user)).get_job(ident)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job with id {ident}")
    return job


def _resolve_linkedin_data(
    method: str | None, body: dict[str, Any], config: Config
) -> dict[str, list[Any]]:
    """Extract raw LinkedIn data per import method.

    Args:
        method: One of "paste", "file", "url".
        body: Request body holding text/file_path/profile_url/allow_fetch.
        config: Effective user config, for the saved URL and opt-in flag.

    Returns:
        Dictionary with extracted profile data (skills, education, roles).

    Raises:
        HTTPException: If the method is invalid or required input is missing.
        FileNotFoundError: If a "file" export path does not exist.
        ValueError: If a "file" export is not a valid ZIP.
        RuntimeError: If a "url" fetch fails.
    """
    from job_scout.linkedin_import import LinkedInProfileImporter  # noqa: PLC0415

    if method == "paste":
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")
        return LinkedInProfileImporter.parse_pasted_text(text)
    if method == "file":
        return LinkedInProfileImporter.parse_export(body.get("file_path") or "")
    if method == "url":
        profile_url = body.get("profile_url") or config.linkedin_profile_url
        if not profile_url:
            raise HTTPException(status_code=400, detail="No profile URL given")
        allow = bool(body.get("allow_fetch")) and config.linkedin_import_allow_url_fetch
        if not allow:
            raise HTTPException(
                status_code=400,
                detail=(
                    "URL fetch requires confirming the ToS risk here and enabling "
                    "'Allow automated LinkedIn URL fetch' in settings."
                ),
            )
        return LinkedInProfileImporter.fetch_profile_url(profile_url, allow_fetch=True)
    raise HTTPException(
        status_code=400, detail="method must be 'paste', 'file', or 'url'"
    )


def _preview_or_apply_merge(
    config: Config,
    db: Database,
    client: LLMClient,
    candidate_data: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    """Merge candidate profile data against the CV, optionally persisting it.

    Args:
        config: Effective user config (needs cv_path).
        db: The user's database, for CV cache read/write.
        client: LLM client used to parse the current CV.
        candidate_data: {skills, education, past_roles} to merge in.
        apply: If True and anything is new, persist the merged profile.

    Returns:
        Dictionary with 'diff' and 'applied'.

    Raises:
        HTTPException: If the configured CV file cannot be read.
    """
    from job_scout.cv_parser import compute_cv_hash, parse_cv  # noqa: PLC0415
    from job_scout.cv_profile import get_or_parse_cv_profile  # noqa: PLC0415
    from job_scout.linkedin_import import (  # noqa: PLC0415
        merge_linkedin_into_profile,
        reconcile_current_role,
    )

    empty_diff: dict[str, list[Any]] = {
        "added_skills": [],
        "added_education": [],
        "added_roles": [],
        "updated_roles": [],
    }
    if not any(candidate_data.values()) or not config.cv_path:
        return {"diff": empty_diff, "applied": False}

    try:
        raw_cv_text = parse_cv(config.cv_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not raw_cv_text:
        return {"diff": empty_diff, "applied": False}

    current_profile = get_or_parse_cv_profile(raw_cv_text, client, db)
    merged_profile, diff = merge_linkedin_into_profile(current_profile, candidate_data)
    # Merging alone cannot close a stale role whose title differs slightly from
    # the imported one, which would leave two employers looking current.
    merged_profile = reconcile_current_role(
        merged_profile, candidate_data.get("current_company")
    )

    applied = False
    if apply and any(diff.values()):
        cv_hash = compute_cv_hash(raw_cv_text)
        db.save_cv_profile_cache(cv_hash, json.dumps(merged_profile.model_dump()))
        applied = True

    return {"diff": diff, "applied": applied}


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check dashboard token on /api/* requests."""

    def __init__(self, app: Any, dashboard_token: str | None) -> None:
        """Initialize the middleware with the dashboard token.

        Args:
            app: FastAPI application.
            dashboard_token: Optional shared token for authentication.
        """
        super().__init__(app)
        self.dashboard_token = dashboard_token

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Check token on /api/* requests.

        Args:
            request: Incoming request.
            call_next: Next middleware/handler.

        Returns:
            Response from next handler, or 401 if auth fails.
        """
        # Only check auth for /api/* routes
        if request.url.path.startswith("/api/"):
            # If no token is configured, allow all requests
            if not self.dashboard_token:
                return await call_next(request)

            # Extract token from Authorization header
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid Authorization header"},
                )

            token = auth_header[7:]  # Remove "Bearer " prefix
            # Use constant-time comparison to prevent timing attacks
            if not secrets.compare_digest(token, self.dashboard_token):
                return JSONResponse(
                    status_code=401, content={"detail": "Invalid token"}
                )

        return await call_next(request)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application with all routes mounted.
    """
    app = FastAPI(title="job-scout", description="Automated job search dashboard")

    # Load dashboard token from secrets
    token_config = load_secrets()
    dashboard_token: str | None = token_config.get("dashboard_token")

    # Add token authentication middleware
    app.add_middleware(TokenAuthMiddleware, dashboard_token=dashboard_token)

    # Force the browser to revalidate static assets so UI updates are picked up
    # immediately instead of serving a stale cached copy.
    no_cache_headers = {"Cache-Control": "no-cache, must-revalidate"}

    # --- Static file serving ---
    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        """Serve the main dashboard HTML file."""
        static_dir = Path(__file__).parent / "static"
        return FileResponse(static_dir / "index.html", headers=no_cache_headers)

    @app.get("/app.js", include_in_schema=False)
    def serve_app_js() -> FileResponse:
        """Serve the main application JavaScript file."""
        static_dir = Path(__file__).parent / "static"
        return FileResponse(static_dir / "app.js", headers=no_cache_headers)

    @app.get("/style.css", include_in_schema=False)
    def serve_style_css() -> FileResponse:
        """Serve the stylesheet."""
        static_dir = Path(__file__).parent / "static"
        return FileResponse(static_dir / "style.css", headers=no_cache_headers)

    # --- API Endpoints ---

    @app.get("/api/users")
    def get_users() -> list[str]:
        """List all configured users.

        Returns:
            List of user names.
        """
        return list_users()

    @app.get("/api/config")
    def get_config(user: str | None = None) -> dict[str, Any]:
        """Get the effective configuration for a user.

        Args:
            user: User name (optional, uses global config if not provided).

        Returns:
            Configuration dictionary with secret fields masked.

        Raises:
            HTTPException: If user does not exist.
        """
        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            if user:
                config = build_effective_config(user)
            else:
                # Return the global config with defaults filled in
                from job_scout.config import load_global_config, load_llm_config

                config = load_llm_config()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

        # Mask secret fields (show only last 4 chars)
        config_dict = config.model_dump()
        for key in config_dict:
            if "key" in key.lower() and config_dict[key]:
                config_dict[key] = f"***{str(config_dict[key])[-4:]}"

        if not user:
            # Config fields always carry Pydantic defaults, so presence of a
            # field like llm_provider can't distinguish "never initialized"
            # from "using defaults". Check the raw on-disk global config
            # instead -- it's only ever non-empty once /api/global-init (or
            # a subsequent global settings save) has actually written to it.
            config_dict["global_initialized"] = bool(load_global_config())

        return config_dict

    @app.get("/api/jobs/matched")
    def get_matched_jobs(
        user: str | None = None,
        limit: int = Query(20, ge=1, le=100),
        min_score: int | None = Query(None, ge=0, le=100),
        source: str | None = None,
        sort: str = Query("date_desc"),
    ) -> list[JobListing]:
        """Get recently matched jobs for a user with optional filtering and sorting.

        Args:
            user: User name (required).
            limit: Maximum number of jobs to return (default 20, max 100).
            min_score: Optional minimum fit score filter (0-100).
            source: Optional source name filter (exact match).
            sort: Sort order - 'score_desc', 'score_asc', 'date_desc'
                (default), or 'date_asc'.

        Returns:
            List of matched job listings.

        Raises:
            HTTPException: If user is not provided or not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        # Validate sort parameter
        valid_sorts = {"score_desc", "score_asc", "date_desc", "date_asc"}
        if sort not in valid_sorts:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort value. Must be one of: {', '.join(valid_sorts)}",
            )

        try:
            db_path = user_db_path(user)
            if not db_path.exists():
                return []
            db = Database(db_path)
            matches = db.get_recent_matches(
                limit=limit, min_score=min_score, source=source, sort=sort
            )
            _attach_company_reviews(db, matches)
            return matches
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/jobs/rejected")
    def get_rejected_jobs(
        user: str | None = None,
        limit: int = Query(20, ge=1, le=100),
        min_score: int | None = Query(None, ge=0, le=100),
        source: str | None = None,
        sort: str = Query("date_desc"),
    ) -> list[JobListing]:
        """Get recently rejected jobs for a user with optional filtering and sorting.

        Args:
            user: User name (required).
            limit: Maximum number of jobs to return (default 20, max 100).
            min_score: Optional minimum fit score filter (0-100).
            source: Optional source name filter (exact match).
            sort: Sort order - 'score_desc', 'score_asc', 'date_desc'
                (default), or 'date_asc'.

        Returns:
            List of rejected job listings.

        Raises:
            HTTPException: If user is not provided or not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        # Validate sort parameter
        valid_sorts = {"score_desc", "score_asc", "date_desc", "date_asc"}
        if sort not in valid_sorts:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort value. Must be one of: {', '.join(valid_sorts)}",
            )

        try:
            db_path = user_db_path(user)
            if not db_path.exists():
                return []
            db = Database(db_path)
            return db.get_rejected_jobs(
                limit=limit, min_score=min_score, source=source, sort=sort
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/jobs/export")
    def export_jobs(
        user: str | None = None,
        format: str = Query("json", pattern="^(csv|json)$"),
        status: str = Query("all", pattern="^(all|matched|rejected)$"),
    ) -> str | list[dict[str, Any]]:
        """Export jobs in CSV or JSON format.

        Args:
            user: User name (required).
            format: Export format ('csv' or 'json', default 'json').
            status: Job status filter ('all', 'matched', or 'rejected',
                default 'all').

        Returns:
            CSV string or JSON-formatted job list.

        Raises:
            HTTPException: If user is not provided or not found.
        """
        from job_scout.exporter import JobExporter

        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            db_path = user_db_path(user)
            if not db_path.exists():
                return []

            db = Database(db_path)

            # Get jobs based on status filter
            if status == "matched":
                jobs = db.get_recent_matches(limit=10000)
            elif status == "rejected":
                jobs = db.get_rejected_jobs(limit=10000)
            else:  # all
                jobs = db.get_all_jobs()

            # Export to requested format
            format_literal = cast(Literal["csv", "json"], format)
            content = JobExporter.export(jobs, format=format_literal)

            if format == "csv":
                # Return CSV as plain text
                return content
            else:
                # Return JSON as parsed list
                import json

                return cast(list[dict[str, Any]], json.loads(content))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/runs/history")
    def get_runs_history(
        user: str | None = None, limit: int = Query(30, ge=1, le=100)
    ) -> list[dict[str, Any]]:
        """Get run history for a user.

        Args:
            user: User name (required).
            limit: Maximum number of runs to return (default 30, max 100).

        Returns:
            List of run history entries as dictionaries.

        Raises:
            HTTPException: If user is not provided or not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            db_path = user_db_path(user)
            if not db_path.exists():
                return []
            db = Database(db_path)
            history = db.get_run_history(limit)
            return [
                {
                    "started_at": e.started_at.isoformat(),
                    "duration_seconds": e.duration_seconds,
                    "scraped": e.scraped,
                    "deduplicated": e.deduplicated,
                    "title_filtered": e.title_filtered,
                    "title_screened": e.title_screened,
                    "quick_filtered": e.quick_filtered,
                    "evaluated": e.evaluated,
                    "matched": e.matched,
                    "rejected": e.rejected,
                    "notified": e.notified,
                    "errors": e.errors,
                }
                for e in history
            ]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/schedule/status")
    def get_schedule_status(user: str | None = None) -> dict[str, str]:
        """Get the current schedule status for a user or globally.

        Args:
            user: User name, or None for global schedule.

        Returns:
            Dictionary with schedule status message.
        """
        try:
            status = check_schedule_status(user=user)
            return {"status": status}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/logs")
    def list_logs(user: str | None = None) -> list[dict[str, Any]]:
        """List available log files for a user.

        Args:
            user: User name (required).

        Returns:
            List of log file info (name, mtime, size).

        Raises:
            HTTPException: If user is not provided or not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            logs_dir = user_logs_dir(user)
            if not logs_dir.exists():
                return []

            log_files = []
            for log_file in sorted(
                logs_dir.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                stat = log_file.stat()
                log_files.append(
                    {
                        "name": log_file.name,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                )
            return log_files
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/logs/{filename}")
    def get_log_file(
        filename: str,
        user: str | None = None,
        lines: int = Query(200, ge=1, le=1000),
    ) -> dict[str, Any]:
        """Get the last N lines from a specific log file.

        Args:
            filename: Log file name (must be an exact match from /api/logs).
            user: User name (required).
            lines: Number of lines to return (default 200, max 1000).

        Returns:
            Dictionary with file name and content (last N lines).

        Raises:
            HTTPException: If user not provided, file not found, or
                path traversal attempted.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        # Security: reject path traversal attempts
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        try:
            logs_dir = user_logs_dir(user)
            if not logs_dir.exists():
                raise HTTPException(status_code=404, detail="Log directory not found")

            # Verify the file exists and is actually in the logs directory
            log_file = logs_dir / filename
            if not log_file.exists() or not log_file.is_file():
                raise HTTPException(status_code=404, detail="Log file not found")
            if not log_file.resolve().is_relative_to(logs_dir.resolve()):
                raise HTTPException(status_code=400, detail="Invalid filename")

            # Read and return the last N lines
            with open(log_file) as f:
                all_lines = f.readlines()
            content_lines = all_lines[-lines:]
            return {
                "filename": filename,
                "lines": lines,
                "content": "".join(content_lines),
                "total_lines": len(all_lines),
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    # --- POST Endpoints for Configuration ---

    @app.post("/api/global-init")
    def initialize_global(body: dict[str, Any]) -> dict[str, str]:
        """Initialize the global configuration (first-time setup).

        Args:
            body: Request body with optional global config field values.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If init fails.
        """
        from job_scout.config import load_global_config, write_global_config

        try:
            # Filter to only global-allowed fields
            global_fields = {
                k: v for k, v in body.items() if k in GLOBAL_FIELDS and v is not None
            }
            # Load and merge with existing config
            existing_data = load_global_config()
            existing_data.update(global_fields)
            write_global_config(existing_data)
            return {"status": "Global configuration initialized successfully"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/users")
    def create_user(body: dict[str, Any]) -> dict[str, str]:
        """Create a new user with initial configuration.

        Args:
            body: Request body with 'name' and optional config fields.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If user already exists or name is invalid.
        """
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="User name is required")
        if name == "all":
            raise HTTPException(
                status_code=400, detail="Cannot create user named 'all'"
            )
        if name in list_users():
            raise HTTPException(status_code=409, detail=f"User '{name}' already exists")

        try:
            # Build config from provided fields
            config_fields = {
                k: v for k, v in body.items() if k != "name" and v is not None
            }
            apply_user_init(name, config_fields)
            return {"status": f"User '{name}' created successfully"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/config")
    def update_config(body: dict[str, Any]) -> dict[str, Any]:
        """Update configuration values.

        Args:
            body: Request body with 'user' (optional) and 'values' dict
                of key/value pairs.

        Returns:
            Dictionary with status and any per-key errors.

        Raises:
            HTTPException: If user does not exist or config update fails.
        """
        user = body.get("user")
        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        values = body.get("values", {})
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail="'values' must be a dict")

        errors = {}
        try:
            for key, value in values.items():
                try:
                    # For list values from JSON, use json.dumps to preserve proper
                    # formatting (double quotes) instead of Python repr (single quotes).
                    if isinstance(value, list):
                        str_value = json.dumps(value)
                    else:
                        str_value = str(value) if value is not None else ""
                    set_config_value(key, str_value, user=user)
                except ValueError as e:
                    errors[key] = str(e)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

        if errors:
            return {"status": "partial", "errors": errors}
        return {"status": "success"}

    def _schedule_preview(cfg: Config) -> dict[str, Any]:
        """Validate the stored schedule and project the next few run times.

        Args:
            cfg: Configuration holding the slot spec and timezone.

        Returns:
            Validity, an error message when invalid, and upcoming run times.
        """
        try:
            slots = parse_slots(cfg.schedule_slots)
        except ValueError as exc:
            return {"valid": False, "error": str(exc), "next_runs": []}
        try:
            tz = ZoneInfo(cfg.schedule_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            return {
                "valid": False,
                "error": f"Unknown timezone: {exc}",
                "next_runs": [],
            }

        moment = datetime.now(tz)
        upcoming: list[str] = []
        for _ in range(_SCHEDULE_PREVIEW_COUNT):
            moment = next_run_after(moment, slots)
            upcoming.append(moment.isoformat())
        return {"valid": True, "error": None, "next_runs": upcoming}

    @app.get("/api/auto-schedule")
    def get_schedule() -> dict[str, Any]:
        """Return the container schedule, wake settings and upcoming runs.

        Returns:
            The stored settings plus a validated preview of the next runs.
        """
        cfg = load_config()
        payload: dict[str, Any] = {
            "slots": cfg.schedule_slots,
            "timezone": cfg.schedule_timezone,
            "enabled": cfg.schedule_enabled,
            "wake_mac": cfg.wake_mac or "",
            "wake_broadcast": cfg.wake_broadcast,
            "llm_health_url": cfg.llm_health_url or "",
            "wake_timeout_seconds": cfg.wake_timeout_seconds,
        }
        payload.update(_schedule_preview(cfg))
        return payload

    @app.post("/api/auto-schedule")
    def update_schedule(body: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist the schedule and wake settings.

        Validation happens before anything is written: an invalid slot spec
        would pause the scheduler, so it is rejected here instead.

        Args:
            body: Any of slots, timezone, paused, wake_mac, wake_broadcast,
                llm_health_url, wake_timeout_seconds.

        Returns:
            The saved settings with a refreshed next-run preview.

        Raises:
            HTTPException: If a supplied value is invalid.
        """
        if "slots" in body:
            try:
                parse_slots(str(body["slots"]))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid schedule: {exc}"
                ) from exc
        if body.get("timezone"):
            try:
                ZoneInfo(str(body["timezone"]))
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail=f"Unknown timezone: {exc}"
                ) from exc
        if body.get("wake_mac"):
            try:
                normalise_mac(str(body["wake_mac"]))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        allowed = (
            "slots",
            "timezone",
            "enabled",
            "wake_mac",
            "wake_broadcast",
            "llm_health_url",
            "wake_timeout_seconds",
        )
        # 'slots'/'timezone'/'paused' are exposed under friendlier names than
        # the config fields they map to.
        field_names = {
            "slots": "schedule_slots",
            "timezone": "schedule_timezone",
            "enabled": "schedule_enabled",
        }
        for key in allowed:
            if key not in body:
                continue
            value = body[key]
            set_config_value(
                field_names.get(key, key),
                "" if value is None else str(value),
                user=None,
            )

        cfg = load_config()
        result: dict[str, Any] = {"status": "success"}
        result.update(_schedule_preview(cfg))
        return result

    @app.post("/api/auto-schedule/test-wake")
    def test_wake() -> dict[str, Any]:
        """Send a magic packet now and report whether the model server answers.

        Lets the wake settings be verified from the dashboard rather than by
        waiting for the next scheduled run to fail.

        Returns:
            Whether the model server is reachable, and a message to display.

        Raises:
            HTTPException: If the wake settings are incomplete.
        """
        cfg = load_config()
        if not cfg.wake_mac:
            raise HTTPException(status_code=400, detail="No MAC address configured.")
        if not cfg.llm_health_url:
            raise HTTPException(
                status_code=400, detail="No model server readiness URL configured."
            )
        reachable = wake_and_wait(
            cfg.wake_mac,
            cfg.llm_health_url,
            broadcast=cfg.wake_broadcast,
            timeout=cfg.wake_timeout_seconds,
        )
        message = (
            f"Model server is reachable at {cfg.llm_health_url}."
            if reachable
            else (
                f"Sent the packet, but {cfg.llm_health_url} did not answer within "
                f"{cfg.wake_timeout_seconds:.0f}s."
            )
        )
        return {"reachable": reachable, "message": message}

    def _topic_payload(user: str) -> dict[str, Any]:
        """Describe a user's ntfy topic and how to subscribe to it.

        Args:
            user: User name.

        Returns:
            The topic, the URLs to reach it, and whether it looks guessable.
        """
        cfg = build_effective_config(user)
        topic = cfg.ntfy_topic or ""
        server = cfg.ntfy_server or "https://ntfy.sh"
        url = ntfy_topic.subscribe_url(topic, server) if topic else ""
        return {
            "topic": topic,
            "server": server,
            "subscribe_url": url,
            # ntfy documents its own scheme for this: an https link opens the
            # browser rather than the app, because Android's http deep
            # linking is unreliable.
            "app_url": ntfy_topic.app_subscribe_url(topic, server) if topic else "",
            "secure": ntfy_topic.is_secure_topic(topic),
        }

    @app.get("/api/ntfy/topic")
    def get_ntfy_topic(user: str | None = None) -> dict[str, Any]:
        """Return the user's ntfy topic and subscription links.

        Args:
            user: User name.

        Returns:
            Topic details for the notifications panel.
        """
        return _topic_payload(_require_user(user))

    @app.post("/api/ntfy/topic/generate")
    def generate_ntfy_topic(body: dict[str, Any]) -> dict[str, Any]:
        """Generate and save a new hard-to-guess topic for the user.

        Args:
            body: Request body with 'user'.

        Returns:
            The new topic details.
        """
        name = _require_user(body.get("user"))
        topic = ntfy_topic.generate_topic(name)
        set_config_value("ntfy_topic", topic, user=name)
        logger.info(f"Generated a new ntfy topic for {name}")
        return _topic_payload(name)

    @app.get("/api/ntfy/qr")
    def get_ntfy_qr(user: str | None = None, kind: str = "app") -> Response:
        """Return the subscription link as a scannable SVG QR code.

        Args:
            user: User name.
            kind: 'app' encodes the ntfy:// deep link, which opens the app
                straight at the topic. 'web' encodes the https URL, which any
                scanner will follow but which lands in the browser.

        Returns:
            An SVG response.

        Raises:
            HTTPException: If no topic is configured yet.
        """
        payload = _topic_payload(_require_user(user))
        if not payload["subscribe_url"]:
            raise HTTPException(status_code=400, detail="No ntfy topic configured.")
        key = "subscribe_url" if kind == "web" else "app_url"
        svg = ntfy_topic.qr_svg(cast(str, payload[key]))
        # Never cached: regenerating the topic must not leave a stale code on
        # screen that subscribes someone to the previous topic.
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/feedback/cv")
    def feedback_cv(body: dict[str, Any]) -> dict[str, Any]:
        """Review the user's CV, generally or against one vacancy.

        Args:
            body: Request body with 'user' and an optional 'job_id'.

        Returns:
            The review as a dictionary.

        Raises:
            HTTPException: If the CV is unavailable or the review fails.
        """
        user = _require_user(body.get("user"))
        job = _require_job(user, body["job_id"]) if body.get("job_id") else None
        cv_text = _load_cv_text(user)
        client = get_llm_client(build_effective_config(user))
        try:
            review = feedback.review_cv(
                cv_text,
                profile=_load_cv_profile_quietly(user),
                job=job,
                client=client,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return review.model_dump()

    @app.post("/api/feedback/cover-letter")
    def feedback_cover_letter(body: dict[str, Any]) -> dict[str, Any]:
        """Review a motivational letter against the vacancy it targets.

        Args:
            body: Request body with 'user', 'job_id' and 'text'.

        Returns:
            The review as a dictionary.

        Raises:
            HTTPException: If the job or letter is missing, or review fails.
        """
        user = _require_user(body.get("user"))
        job = _require_job(user, body.get("job_id"))
        client = get_llm_client(build_effective_config(user))
        try:
            review = feedback.review_cover_letter(
                str(body.get("text") or ""),
                job,
                profile=_load_cv_profile_quietly(user),
                client=client,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return review.model_dump()

    @app.get("/api/feedback/jobs")
    def feedback_jobs(user: str | None = None) -> list[dict[str, Any]]:
        """List the user's jobs, for the 'which vacancy' pickers.

        Args:
            user: User name.

        Returns:
            Id, title and company for each job still under consideration,
            newest first.
        """
        name = _require_user(user)
        db = Database(user_db_path(name))
        seen: set[int] = set()
        options: list[dict[str, Any]] = []
        for status in (
            JobStatus.MATCHED,
            JobStatus.VIEWED,
            JobStatus.APPROVED,
            JobStatus.READY,
            JobStatus.SUBMITTED,
            JobStatus.INTERVIEWING,
        ):
            for job in db.get_jobs_by_status(status):
                if job.id is None or job.id in seen:
                    continue
                seen.add(job.id)
                options.append(
                    {
                        "id": job.id,
                        "title": job.title,
                        "company": job.company,
                        "status": job.status.value,
                    }
                )
        options.sort(key=lambda o: cast(int, o["id"]), reverse=True)
        return options

    @app.post("/api/secrets")
    def update_secrets_endpoint(body: dict[str, Any]) -> dict[str, str]:
        """Update secret API keys.

        Args:
            body: Request body with optional secret field values.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If secret update fails.
        """
        from job_scout.config import SECRET_FIELDS

        # Filter to only known secret fields
        secret_data = {k: str(v) for k, v in body.items() if k in SECRET_FIELDS and v}

        if not secret_data:
            return {"status": "no changes"}

        try:
            update_secrets(secret_data)
            return {"status": "secrets updated"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/sites")
    def get_sites(user: str | None = None) -> list[dict[str, Any]]:
        """Get custom sites for a user.

        Args:
            user: User name (required).

        Returns:
            List of custom site dictionaries.

        Raises:
            HTTPException: If user not provided or not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            cfg = load_user_config(user)
            sites: list[dict[str, Any]] = cfg.get("custom_sites", [])
            return sites
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/profile/cv-summary")
    def get_cv_profile(user: str | None = None) -> dict[str, Any]:
        """Get the structured CV profile for a user.

        Parses the user's CV file using LLM and returns structured data:
        skills, years of experience, education, and past roles.
        Results are cached by CV content hash.

        Args:
            user: User name (required).

        Returns:
            Dictionary with cv_profile (skills, years_experience, education, past_roles)
            or error message if CV not configured or parsing fails.

        Raises:
            HTTPException: If user not provided or not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.cv_parser import parse_cv  # noqa: PLC0415
            from job_scout.cv_profile import get_or_parse_cv_profile  # noqa: PLC0415
            from job_scout.llm.base import LLMError  # noqa: PLC0415

            config = build_effective_config(user)

            if not config.cv_path:
                return {"error": "CV path not configured"}

            # Parse raw CV text
            try:
                raw_cv_text = parse_cv(config.cv_path)
            except FileNotFoundError as _:
                return {"error": f"CV file not found at {config.cv_path}"}

            if not raw_cv_text:
                return {"error": "Failed to extract text from CV"}

            # Get LLM client
            try:
                client = get_llm_client(config)
            except LLMError as e:
                return {"error": f"LLM configuration error: {str(e)}"}

            # Check LLM availability
            ok, err = client.check_available()
            if not ok:
                return {"error": f"LLM not available: {err}"}

            # Load or parse CV profile with caching
            db = Database(user_db_path(user))
            profile = get_or_parse_cv_profile(raw_cv_text, client, db)

            return {
                "cv_profile": profile.model_dump(),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/profile/import-linkedin")
    def import_linkedin_profile(body: dict[str, Any]) -> dict[str, Any]:
        """Import LinkedIn profile data and preview or apply a CV profile merge.

        Args:
            body: Request body with 'user', 'method' ("paste"|"file"|"url"), the
                matching input field (text/file_path/profile_url), an optional
                'allow_fetch' flag for the url method, and 'apply' (bool) to
                persist the merge instead of only previewing it.

        Returns:
            Dictionary with the proposed diff and whether it was applied.

        Raises:
            HTTPException: On invalid input, missing CV, or LLM errors.
        """
        from job_scout.llm.base import LLMError  # noqa: PLC0415

        user = body.get("user")
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        config = build_effective_config(user)
        try:
            linkedin_data = _resolve_linkedin_data(body.get("method"), body, config)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not any(linkedin_data.get(k) for k in ("skills", "education", "past_roles")):
            empty: dict[str, list[Any]] = {
                "added_skills": [],
                "added_education": [],
                "added_roles": [],
                "updated_roles": [],
            }
            return {"diff": empty, "applied": False, "warning": "No data extracted."}

        if not config.cv_path:
            raise HTTPException(status_code=400, detail="CV path not configured")

        try:
            client = get_llm_client(config)
        except LLMError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        db = Database(user_db_path(user))
        return _preview_or_apply_merge(
            config, db, client, linkedin_data, bool(body.get("apply"))
        )

    @app.get("/api/coach/questions")
    def coach_questions(user: str | None = None) -> dict[str, Any]:
        """Return the coach's intake questions, grounded in the user's CV.

        Args:
            user: User name (required).

        Returns:
            Dictionary with the question list.

        Raises:
            HTTPException: If the user is missing or unknown.
        """
        from job_scout.coach import baseline_questions  # noqa: PLC0415

        _require_user(user)
        cv = _load_cv_profile_quietly(cast(str, user))
        return {"questions": [q.model_dump() for q in baseline_questions(cv)]}

    @app.post("/api/coach/propose")
    def coach_propose(body: dict[str, Any]) -> dict[str, Any]:
        """Turn intake answers into proposed career tracks for review.

        Nothing is saved here -- the proposal is returned for the user to
        confirm, edit, or discard via /api/coach/apply.

        Args:
            body: Request body with 'user' and 'answers' (a list of
                {id, answer} objects).

        Returns:
            Dictionary with the proposal (tracks, summary, follow-up).

        Raises:
            HTTPException: On invalid input or LLM configuration errors.
        """
        from job_scout.coach import CoachAnswer, propose_tracks  # noqa: PLC0415
        from job_scout.llm.base import LLMError  # noqa: PLC0415

        user = _require_user(body.get("user"))
        raw = body.get("answers")
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="'answers' must be a list")
        answers = [
            CoachAnswer(id=str(a.get("id") or ""), answer=str(a.get("answer") or ""))
            for a in raw
            if isinstance(a, dict)
        ]
        if not any(a.answer.strip() for a in answers):
            raise HTTPException(status_code=400, detail="Answer at least one question")

        try:
            client = get_llm_client(build_effective_config(user))
        except LLMError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        proposal = propose_tracks(
            answers, cv=_load_cv_profile_quietly(user), client=client
        )
        return proposal.model_dump()

    @app.post("/api/coach/apply")
    def coach_apply(body: dict[str, Any]) -> dict[str, Any]:
        """Save confirmed career tracks to the user's configuration.

        Args:
            body: Request body with 'user', 'tracks' (list of track objects),
                and an optional 'negative_description'.

        Returns:
            Dictionary with the saved track count.

        Raises:
            HTTPException: On invalid input or a failed save.
        """
        from job_scout.models import CareerTrack  # noqa: PLC0415

        user = _require_user(body.get("user"))
        raw = body.get("tracks")
        if not isinstance(raw, list) or not raw:
            raise HTTPException(
                status_code=400, detail="At least one track is required"
            )
        try:
            tracks = [CareerTrack(**t) for t in raw if isinstance(t, dict)]
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not any(t.mode == "standalone" and t.enabled for t in tracks):
            raise HTTPException(
                status_code=400,
                detail=(
                    "At least one track must be a standalone search -- a blend "
                    "only colours other tracks and cannot be searched on its own."
                ),
            )

        user_data = load_user_config(user)
        user_data["career_tracks"] = [t.model_dump() for t in tracks]
        negative = body.get("negative_description")
        if isinstance(negative, str) and negative.strip():
            user_data["negative_description"] = negative.strip()
        save_user_config(user, user_data)
        logger.info(f"Saved {len(tracks)} career tracks for {user}")
        return {"status": "success", "saved": len(tracks)}

    @app.post("/api/profile/import-linkedin-pdf")
    def import_linkedin_pdf(body: dict[str, Any]) -> dict[str, Any]:
        """Import work history from a LinkedIn "Save to PDF" profile export.

        This is the richest safe import path: the PDF the account holder
        downloads of their own profile carries real job titles and dates,
        so it can also correct a CV whose current role is out of date.

        Args:
            body: Request body with 'user', 'pdf_path', and 'apply' (bool).

        Returns:
            Dictionary with the proposed diff, any current-role conflict, and
            whether the merge was applied.

        Raises:
            HTTPException: On invalid input or LLM configuration errors.
        """
        from job_scout.linkedin_import import (  # noqa: PLC0415
            detect_stale_current_role,
            parse_linkedin_pdf,
        )
        from job_scout.llm.base import LLMError  # noqa: PLC0415

        user = _require_user(body.get("user"))
        pdf_path = str(body.get("pdf_path") or "").strip()
        if not pdf_path:
            raise HTTPException(status_code=400, detail="pdf_path is required")

        config = build_effective_config(user)
        try:
            client = get_llm_client(config)
        except LLMError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            data = parse_linkedin_pdf(pdf_path, client=client)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        db = Database(user_db_path(user))
        result = _preview_or_apply_merge(
            config, db, client, data, bool(body.get("apply"))
        )
        current_profile = _load_cv_profile_quietly(user)
        conflict = (
            detect_stale_current_role(current_profile, data.get("current_company"))
            if current_profile
            else None
        )
        result["current_company"] = data.get("current_company")
        result["conflict"] = conflict.model_dump() if conflict else None
        return result

    @app.post("/api/profile/search-person")
    def search_person_endpoint(body: dict[str, Any]) -> dict[str, Any]:
        """Search the public web for a person; preview or apply CV additions.

        Args:
            body: Request body with 'user', optional 'full_name' (defaults to the
                configured profile name), optional 'known_context' for
                disambiguation, 'refresh' (bool) to bypass cache, and 'apply'
                (bool) to merge the result into the CV profile.

        Returns:
            Dictionary with the search result and the proposed/applied CV diff.

        Raises:
            HTTPException: On invalid input, missing name, or LLM errors.
        """
        from job_scout.llm.base import LLMError  # noqa: PLC0415
        from job_scout.person_search import search_person  # noqa: PLC0415

        user = body.get("user")
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        config = build_effective_config(user)
        full_name = body.get("full_name") or config.name
        if not full_name:
            raise HTTPException(status_code=400, detail="No name to search for")

        db = Database(user_db_path(user))
        try:
            client = get_llm_client(config)
        except LLMError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cached = None if body.get("refresh") else db.get_person_search(full_name)
        if cached:
            result = PersonSearchResult.model_validate_json(cached)
        else:
            result = search_person(
                full_name,
                known_context=body.get("known_context"),
                client=client,
                searxng_url=config.searxng_url,
                api_key=config.brave_api_key,
            )
            db.save_person_search(full_name, result.model_dump_json())

        candidate_data: dict[str, list[Any]] = {
            "skills": result.skills,
            "education": result.education,
            "past_roles": [r.model_dump() for r in result.past_roles],
        }
        response = _preview_or_apply_merge(
            config, db, client, candidate_data, bool(body.get("apply"))
        )
        response["result"] = result.model_dump(mode="json")
        return response

    @app.post("/api/sites")
    def add_site(body: dict[str, Any]) -> dict[str, str]:
        """Add a custom site for a user.

        Args:
            body: Request body with 'user', 'url', optional 'name', and 'render_js'.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If user not found or site already exists.
        """
        from urllib.parse import urlparse

        user = body.get("user")
        url = body.get("url", "").strip()
        name = body.get("name", "").strip()
        render_js = body.get("render_js", False)

        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if not url:
            raise HTTPException(status_code=400, detail="URL is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            resolved_name = name or urlparse(url).hostname or url
            cfg = load_user_config(user)
            sites_list = cfg.get("custom_sites", [])

            if any(s.get("url") == url for s in sites_list):
                raise HTTPException(
                    status_code=409, detail=f"URL already tracked: {url}"
                )

            sites_list.append(
                {
                    "name": resolved_name,
                    "url": url,
                    "enabled": True,
                    "render_js": render_js,
                }
            )
            cfg["custom_sites"] = sites_list
            save_user_config(user, cfg)
            return {"status": f"Added site '{resolved_name}'"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.delete("/api/sites")
    def remove_site(
        user: str | None = None, identifier: str | None = None
    ) -> dict[str, str]:
        """Remove a custom site for a user.

        Args:
            user: User name (required).
            identifier: Site URL or name to remove (required).

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If user not found or site not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if not identifier or not identifier.strip():
            raise HTTPException(status_code=400, detail="Identifier is required")
        identifier = identifier.strip()
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            cfg = load_user_config(user)
            sites_list = cfg.get("custom_sites", [])
            before = len(sites_list)
            sites_list = [
                s
                for s in sites_list
                if s.get("url") != identifier and s.get("name") != identifier
            ]

            if len(sites_list) == before:
                raise HTTPException(
                    status_code=404, detail=f"No site matching '{identifier}' found"
                )

            cfg["custom_sites"] = sites_list
            save_user_config(user, cfg)
            return {"status": f"Removed site '{identifier}'"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/schedule")
    def set_schedule(body: dict[str, Any]) -> dict[str, str]:
        """Install a daily schedule for job-scout runs.

        Args:
            body: Request body with 'hour', 'minute', 'days', and optional 'user'.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If schedule installation fails.
        """
        try:
            hour = int(body.get("hour", 8))
            minute = int(body.get("minute", 0))
            days = body.get("days", "1-5")
            user = body.get("user")
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise HTTPException(status_code=400, detail="Invalid hour or minute")
            install_schedule(hour=hour, minute=minute, days=days, user=user)
            subject = user or "global"
            return {
                "status": f"Schedule installed for {subject} "
                f"at {hour:02d}:{minute:02d} on days {days}"
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.delete("/api/schedule")
    def unset_schedule(user: str | None = None) -> dict[str, str]:
        """Remove the daily schedule for a user or globally.

        Args:
            user: User name, or None for global schedule.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If schedule removal fails.
        """
        try:
            remove_schedule(user=user)
            subject = user or "global"
            return {"status": f"Schedule removed for {subject}"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/llm/test-connection")
    def test_llm_connection(body: dict[str, Any]) -> dict[str, Any]:
        """Test a candidate LLM provider configuration.

        Args:
            body: Request body with 'provider', 'base_url' (optional),
                'api_key' (optional), 'model', and optionally 'purpose'.

        Returns:
            Dictionary with 'ok' (bool) and 'message' (str).

        Raises:
            HTTPException: If parameters are invalid.
        """
        from job_scout.llm.base import LLMError

        provider = body.get("provider", "").strip()
        base_url = body.get("base_url", "").strip()
        api_key = body.get("api_key", "").strip()
        model = body.get("model", "").strip()

        if not provider:
            raise HTTPException(status_code=400, detail="Provider is required")
        if provider not in ("claude_cli", "zai", "kilo_cli", "local"):
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
        if not model and provider != "claude_cli":
            raise HTTPException(status_code=400, detail="Model is required")

        try:
            kwargs: dict[str, object] = {"model": model}
            if base_url:
                kwargs["base_url"] = base_url
            if api_key:
                kwargs["api_key"] = api_key

            client = build_raw_client_for_test(provider, **kwargs)
            available, err = client.check_available()
            if not available:
                return {"ok": False, "message": err or "Provider not available"}
            return {"ok": True, "message": "Connection successful"}
        except LLMError as e:
            return {"ok": False, "message": str(e)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    @app.post("/api/llm/detect-models")
    def detect_local_models(body: dict[str, Any]) -> dict[str, Any]:
        """Detect available models from an OpenAI-compatible local LLM server.

        Args:
            body: Request body with 'base_url' (required) and optional 'api_key'.

        Returns:
            Dict with 'ok' (bool), 'models' (list of model ids), 'message' (str).
        """
        base_url = body.get("base_url", "").strip()

        if not base_url:
            return {"ok": False, "models": [], "message": "base_url is required"}

        try:
            import openai

            api_key = body.get("api_key", "").strip() or "not-needed"

            client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
            models_response = client.models.list()

            model_ids = [model.id for model in models_response.data]
            return {
                "ok": True,
                "models": model_ids,
                "message": f"Found {len(model_ids)} model(s)",
            }
        except openai.APIConnectionError as e:
            return {
                "ok": False,
                "models": [],
                "message": f"Connection failed: {str(e)}",
            }
        except openai.AuthenticationError as e:
            return {
                "ok": False,
                "models": [],
                "message": f"Authentication error: {str(e)}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "models": [],
                "message": f"Error: {str(exc)}",
            }

    @app.post("/api/notification/test-channel")
    def test_notification_channel(body: dict[str, Any]) -> dict[str, Any]:
        """Test a candidate notification channel configuration.

        Args:
            body: Request body with 'channel' and channel-specific settings.
                For ntfy: ntfy_topic, ntfy_server (optional).
                For email: smtp_host, smtp_port, smtp_from, smtp_to,
                    smtp_username (optional), smtp_password (optional).
                For slack: slack_webhook_url.
                For discord: discord_webhook_url.

        Returns:
            Dictionary with 'ok' (bool) and 'message' (str).

        Raises:
            HTTPException: If parameters are invalid.
        """
        from job_scout.notify.base import NotificationError

        channel = body.get("channel", "").strip()

        if not channel:
            raise HTTPException(status_code=400, detail="Channel is required")
        if channel not in ("ntfy", "email", "slack", "discord"):
            raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

        try:
            kwargs: dict[str, object] = {}
            if channel == "ntfy":
                kwargs["ntfy_topic"] = body.get("ntfy_topic", "")
                kwargs["ntfy_server"] = body.get("ntfy_server", "https://ntfy.sh")
            elif channel == "email":
                kwargs["smtp_host"] = body.get("smtp_host", "")
                kwargs["smtp_port"] = body.get("smtp_port", 587)
                kwargs["smtp_from"] = body.get("smtp_from", "")
                kwargs["smtp_to"] = body.get("smtp_to", "")
                kwargs["smtp_username"] = body.get("smtp_username", "")
                kwargs["smtp_password"] = body.get("smtp_password", "")
            elif channel == "slack":
                kwargs["slack_webhook_url"] = body.get("slack_webhook_url", "")
            elif channel == "discord":
                kwargs["discord_webhook_url"] = body.get("discord_webhook_url", "")

            notifier = build_raw_notifier_for_test(channel, **kwargs)
            available, err = notifier.check_available()
            if not available:
                return {"ok": False, "message": err or "Channel not available"}
            return {"ok": True, "message": "Channel configuration valid"}
        except NotificationError as e:
            return {"ok": False, "message": str(e)}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    @app.post("/api/run")
    def start_run(body: dict[str, Any]) -> dict[str, Any]:
        """Start a background pipeline run for a user or all users.

        Args:
            body: Request body with 'user', 'all', 'dry_run', and 'full' flags.
                  'user' selects one user, 'all' runs all users sequentially.
                  'dry_run' and 'full' control pipeline behavior.

        Returns:
            Dictionary with 'status' and 'message'.

        Raises:
            HTTPException: If parameters are invalid or a run is already in progress.
        """
        from job_scout.cli import _execute_run
        from job_scout.evaluator import check_llm_available

        user = body.get("user")
        all_users = body.get("all", False)
        dry_run = body.get("dry_run", True)
        full = body.get("full", False)

        # Validate parameters
        if user and all_users:
            raise HTTPException(
                status_code=400,
                detail="Cannot specify both 'user' and 'all'",
            )

        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        # Determine target users
        if all_users:
            users_list = list_users()
            if not users_list:
                raise HTTPException(
                    status_code=400,
                    detail="No users configured. Create a user first.",
                )
            target_users: list[str | None] = cast(list[str | None], users_list)
        else:
            target_users = [user] if user else [None]

        # Check if any run is already in progress
        with _registry_lock:
            for target_user in target_users:
                if (
                    target_user in _run_registry
                    and _run_registry[target_user]["status"] == "running"
                ):
                    user_label = target_user or "global"
                    raise HTTPException(
                        status_code=409,
                        detail=f"Run already in progress for user '{user_label}'",
                    )

            # Initialize registry entries for all target users
            for target_user in target_users:
                _run_registry[target_user] = {
                    "status": "running",
                    "start_time": datetime.now(),
                    "end_time": None,
                    "result": None,
                    "error": None,
                }

        def _run_task() -> None:
            """Background task to run the pipeline for target user(s)."""
            for target_user in target_users:
                try:
                    if target_user:
                        config = build_effective_config(target_user)
                    else:
                        config = None
                    if config and not config.profile_description:
                        with _registry_lock:
                            _run_registry[target_user]["status"] = "error"
                            _run_registry[target_user]["error"] = (
                                f"No profile configured for user '{target_user}'"
                            )
                        continue

                    if target_user:
                        # Check LLM availability
                        config = build_effective_config(target_user)
                        ok, err = check_llm_available(config)
                        if not ok:
                            with _registry_lock:
                                _run_registry[target_user]["status"] = "error"
                                _run_registry[target_user]["error"] = err
                            continue

                        # Execute the run
                        _execute_run(target_user, dry_run=dry_run, full=full)
                    else:
                        # Global run (no user specified)
                        from job_scout.cli import _execute_run_global

                        _execute_run_global(dry_run=dry_run, full=full)

                    with _registry_lock:
                        _run_registry[target_user]["status"] = "done"
                        _run_registry[target_user]["end_time"] = datetime.now()
                except (Exception, SystemExit) as e:
                    logger.exception(
                        f"Error during pipeline run for {target_user or 'global'}"
                    )
                    with _registry_lock:
                        _run_registry[target_user]["status"] = "error"
                        _run_registry[target_user]["error"] = str(e)
                        _run_registry[target_user]["end_time"] = datetime.now()

        # Start the background thread
        thread = threading.Thread(target=_run_task, daemon=True)
        thread.start()

        if all_users:
            return {
                "status": "running",
                "message": f"Pipeline run started for all {len(target_users)} users",
            }
        return {
            "status": "running",
            "message": f"Pipeline run started for user '{user or 'global'}'",
        }

    @app.get("/api/run/status")
    def get_run_status(user: str | None = None) -> dict[str, Any]:
        """Get the status of the current or last pipeline run for a user.

        Args:
            user: User name (optional).

        Returns:
            Dictionary with 'status', 'message', and optional 'error'.

        Raises:
            HTTPException: If user does not exist.
        """
        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        with _registry_lock:
            entry = _run_registry.get(user)

        if not entry:
            return {
                "status": "idle",
                "message": f"No run in progress for user '{user or 'global'}'",
            }

        result: dict[str, Any] = {
            "status": entry["status"],
            "message": (
                f"Pipeline run is {entry['status']} for user '{user or 'global'}'"
            ),
        }
        if entry.get("error"):
            result["error"] = entry["error"]
        if entry.get("start_time"):
            result["start_time"] = entry["start_time"].isoformat()
        if entry.get("end_time"):
            result["end_time"] = entry["end_time"].isoformat()
        if entry["status"] == "running":
            # A run takes minutes to over an hour; without this the dashboard
            # cannot tell slow progress apart from a hang.
            live = progress.get(user)
            if live:
                result["progress"] = live

        return result

    @app.get("/api/approval/queue")
    def get_approval_queue(user: str | None = None) -> dict[str, Any]:
        """Get the approval queue for jobs awaiting approval.

        Args:
            user: User name. Required; jobs are stored per-user.

        Returns:
            Dictionary with approval queue status and job listings.

        Raises:
            HTTPException: If user is missing or does not exist.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            db_path = user_db_path(user)
            if not db_path.exists():
                return {"count": 0, "jobs": []}
            db = Database(db_path)
            queue = db.get_approval_queue()
            return {
                "count": len(queue),
                "jobs": [job.model_dump() for job in queue],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/approval/approve")
    def approve_job_endpoint(
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Approve a job for application.

        Args:
            body: Request body with 'job_id' (int) and optional 'notes' (str).

        Returns:
            Dictionary with success message.

        Raises:
            HTTPException: If job ID is invalid or approval fails.
        """
        try:
            job_id = body.get("job_id")
            notes = body.get("notes")
            user = body.get("user")

            if not job_id:
                raise HTTPException(status_code=400, detail="job_id is required")
            if not user:
                raise HTTPException(status_code=400, detail="user is required")
            if user not in list_users():
                raise HTTPException(status_code=404, detail=f"User '{user}' not found")

            db_path = user_db_path(user)
            db = Database(db_path)
            if not db.update_job_status(job_id, JobStatus.APPROVED):
                raise HTTPException(status_code=400, detail="Invalid status transition")

            db.approve_job(job_id, user, notes)
            return {"message": f"Job {job_id} approved"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/jobs/{job_id}/status")
    def update_job_status_endpoint(
        job_id: int,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a job's lifecycle status.

        Args:
            job_id: ID of the job to update.
            body: Request body with 'status', 'user', and optional 'notes'.

        Returns:
            Dictionary with success message.

        Raises:
            HTTPException: If job ID is invalid or status update fails.
        """
        try:
            status = body.get("status")
            notes = body.get("notes")
            user = body.get("user")

            if not status:
                raise HTTPException(status_code=400, detail="status is required")
            if not user:
                raise HTTPException(status_code=400, detail="user is required")
            if user not in list_users():
                raise HTTPException(status_code=404, detail=f"User '{user}' not found")

            try:
                new_status = JobStatus(status)
            except ValueError:
                raise HTTPException(  # noqa: B904
                    status_code=400,
                    detail=f"Invalid status: {status}",
                ) from None

            db_path = user_db_path(user)
            db = Database(db_path)
            if not db.update_job_status(job_id, new_status, notes=notes):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid status transition or job not found",
                )

            return {"message": f"Job {job_id} status updated to {status}"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/keywords")
    def get_keywords(user: str | None = None) -> dict[str, Any]:
        """Get the current keywords for a user.

        Args:
            user: User name (optional, uses global config if not provided).

        Returns:
            Dictionary with keyword lists.

        Raises:
            HTTPException: If user does not exist.
        """
        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            config = build_effective_config(user) if user else Config()
            return {
                "dutch": config.keywords_dutch or [],
                "english": config.keywords_english or [],
                "title_include": config.title_include_keywords or [],
                "title_exclude": config.title_exclude_keywords or [],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/keywords/refresh")
    def refresh_keywords(body: dict[str, Any]) -> dict[str, Any]:
        """Refresh (regenerate) keywords for a user.

        Args:
            body: Request body with 'user' (optional).

        Returns:
            Dictionary with keyword lists and success message.

        Raises:
            HTTPException: If user does not exist or keyword generation fails.
        """
        from job_scout.cli import _load_cv_text
        from job_scout.evaluator import generate_keywords

        user = body.get("user")
        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            config = build_effective_config(user) if user else Config()
            if not config.profile_description:
                raise HTTPException(
                    status_code=400,
                    detail="No profile configured; cannot generate keywords",
                )

            cv_text = _load_cv_text(config)
            llm_client = get_llm_client(config)
            result = generate_keywords(
                config.profile_description, cv_text, client=llm_client
            )
            return {
                "dutch": result.dutch,
                "english": result.english,
                "title_include": result.title_include,
                "title_exclude": result.title_exclude,
                "message": "Keywords refreshed successfully",
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/profile/cover-letter/{job_id}")
    def generate_cover_letter_endpoint(
        job_id: int, user: str | None = None
    ) -> dict[str, Any]:
        """Generate a cover letter for a job.

        Args:
            job_id: ID of the job to generate a cover letter for.
            user: User name (required).

        Returns:
            Dictionary with cover_letter text or error message.

        Raises:
            HTTPException: If user not provided, job not found, or generation fails.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.config import (  # noqa: PLC0415
                build_effective_config,
                user_db_path,
            )
            from job_scout.cover_letter_generator import (
                generate_cover_letter,  # noqa: PLC0415
            )
            from job_scout.cv_parser import parse_cv  # noqa: PLC0415
            from job_scout.cv_profile import get_or_parse_cv_profile  # noqa: PLC0415
            from job_scout.database import Database  # noqa: PLC0415
            from job_scout.llm.base import LLMError  # noqa: PLC0415
            from job_scout.llm.factory import get_llm_client  # noqa: PLC0415
            from job_scout.models import JobStatus  # noqa: PLC0415

            config = build_effective_config(user)
            db = Database(user_db_path(user))

            # Get the job
            job = db.get_job(job_id)
            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            # Check status
            if job.status not in [
                JobStatus.APPROVED,
                JobStatus.READY,
                JobStatus.SUBMITTED,
                JobStatus.INTERVIEWING,
                JobStatus.OFFER,
            ]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Job has status {job.status.value}; must be APPROVED or later"
                    ),
                )

            if not job.description:
                raise HTTPException(
                    status_code=400,
                    detail="Job has no description",
                )

            # Get CV and profile
            if not config.cv_path:
                raise HTTPException(
                    status_code=400,
                    detail="CV path not configured",
                )

            try:
                raw_cv_text = parse_cv(config.cv_path)
            except FileNotFoundError as _:
                raise HTTPException(  # noqa: B904
                    status_code=400,
                    detail=f"CV file not found at {config.cv_path}",
                )

            if not raw_cv_text:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to extract text from CV",
                )

            try:
                client = get_llm_client(config)
            except LLMError as e:
                raise HTTPException(  # noqa: B904
                    status_code=400,
                    detail=f"LLM configuration error: {str(e)}",
                )

            ok, err = client.check_available()
            if not ok:
                raise HTTPException(status_code=400, detail=f"LLM not available: {err}")

            cv_profile = get_or_parse_cv_profile(raw_cv_text, client, db)

            cover_letter = generate_cover_letter(
                cv_profile,
                job.description,
                job.title,
                job.company,
                client=client,
            )

            if not cover_letter:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to generate cover letter",
                )

            db.save_cover_letter(job_id, cover_letter)

            return {
                "cover_letter": cover_letter,
                "message": "Cover letter generated successfully",
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/profile/cover-letter/{job_id}")
    def get_cover_letter_endpoint(
        job_id: int, user: str | None = None
    ) -> dict[str, Any]:
        """Get a previously generated cover letter.

        Args:
            job_id: ID of the job.
            user: User name (required).

        Returns:
            Dictionary with cover_letter text or error message.

        Raises:
            HTTPException: If user not provided or cover letter not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.config import user_db_path  # noqa: PLC0415
            from job_scout.database import Database  # noqa: PLC0415

            db = Database(user_db_path(user))
            cover_letter = db.get_cover_letter(job_id)

            if not cover_letter:
                raise HTTPException(
                    status_code=404,
                    detail=f"No cover letter found for job {job_id}",
                )

            return {"cover_letter": cover_letter}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/profile/screening-answers/{job_id}")
    def answer_screening_questions_endpoint(
        job_id: int, user: str | None = None
    ) -> dict[str, Any]:
        """Extract and answer screening questions for a job.

        Args:
            job_id: ID of the job.
            user: User name (required).

        Returns:
            Dictionary with questions and answers lists or error message.

        Raises:
            HTTPException: If user not provided, job not found, or generation fails.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.config import (  # noqa: PLC0415
                build_effective_config,
                user_db_path,
            )
            from job_scout.cover_letter_generator import (  # noqa: PLC0415
                answer_screening_questions,
                extract_screening_questions,
            )
            from job_scout.cv_parser import parse_cv  # noqa: PLC0415
            from job_scout.cv_profile import get_or_parse_cv_profile  # noqa: PLC0415
            from job_scout.database import Database  # noqa: PLC0415
            from job_scout.llm.base import LLMError  # noqa: PLC0415
            from job_scout.llm.factory import get_llm_client  # noqa: PLC0415
            from job_scout.models import JobStatus  # noqa: PLC0415

            config = build_effective_config(user)
            db = Database(user_db_path(user))

            # Get the job
            job = db.get_job(job_id)
            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            # Check status
            if job.status not in [
                JobStatus.APPROVED,
                JobStatus.READY,
                JobStatus.SUBMITTED,
                JobStatus.INTERVIEWING,
                JobStatus.OFFER,
            ]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Job has status {job.status.value}; must be APPROVED or later"
                    ),
                )

            if not job.description:
                raise HTTPException(
                    status_code=400,
                    detail="Job has no description",
                )

            # Get CV and profile
            if not config.cv_path:
                raise HTTPException(
                    status_code=400,
                    detail="CV path not configured",
                )

            try:
                raw_cv_text = parse_cv(config.cv_path)
            except FileNotFoundError as _:
                raise HTTPException(  # noqa: B904
                    status_code=400,
                    detail=f"CV file not found at {config.cv_path}",
                )

            if not raw_cv_text:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to extract text from CV",
                )

            try:
                client = get_llm_client(config)
            except LLMError as e:
                raise HTTPException(  # noqa: B904
                    status_code=400,
                    detail=f"LLM configuration error: {str(e)}",
                )

            ok, err = client.check_available()
            if not ok:
                raise HTTPException(status_code=400, detail=f"LLM not available: {err}")

            cv_profile = get_or_parse_cv_profile(raw_cv_text, client, db)

            # Extract and answer questions
            questions = extract_screening_questions(job.description, client=client)
            if not questions:
                raise HTTPException(
                    status_code=500,
                    detail="No screening questions could be extracted",
                )

            answers = answer_screening_questions(
                questions,
                cv_profile,
                job.description,
                client=client,
            )

            # Save to database
            db.save_screening_questions(job_id, questions, answers)

            # Build response with Q&A pairs
            qa_pairs = []
            for question in questions:
                qa_pairs.append(
                    {
                        "question": question,
                        "answer": answers.get(question, ""),
                    }
                )

            return {
                "qa_pairs": qa_pairs,
                "message": "Screening questions answered successfully",
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/profile/screening-answers/{job_id}")
    def get_screening_answers_endpoint(
        job_id: int, user: str | None = None
    ) -> dict[str, Any]:
        """Get previously generated screening question answers.

        Args:
            job_id: ID of the job.
            user: User name (required).

        Returns:
            Dictionary with qa_pairs list or error message.

        Raises:
            HTTPException: If user not provided or answers not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.config import user_db_path  # noqa: PLC0415
            from job_scout.database import Database  # noqa: PLC0415

            db = Database(user_db_path(user))
            qa_list = db.get_screening_questions(job_id)

            if not qa_list:
                raise HTTPException(
                    status_code=404,
                    detail=f"No screening answers found for job {job_id}",
                )

            qa_pairs = [{"question": q, "answer": a} for q, a in qa_list]

            return {"qa_pairs": qa_pairs}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/company/research/{job_id}")
    def research_company_endpoint(
        job_id: int, user: str | None = None
    ) -> dict[str, Any]:
        """Research a company and discover hiring managers.

        Args:
            job_id: ID of the job to research.
            user: User name (required).

        Returns:
            Dictionary with company research data.

        Raises:
            HTTPException: If user not provided or research fails.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.company_research import research_company  # noqa: PLC0415
            from job_scout.config import (  # noqa: PLC0415
                build_effective_config,
                user_db_path,
            )
            from job_scout.database import Database  # noqa: PLC0415

            config = build_effective_config(user)
            db = Database(user_db_path(user))

            job = db.get_job(job_id)
            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            research = research_company(job, config)
            if not research:
                raise HTTPException(
                    status_code=500,
                    detail="Company research failed or timed out",
                )

            # Save to database
            import json

            research_json = json.dumps(research.model_dump())
            db.save_company_research(job_id, research_json)

            return research.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/company/research/{job_id}")
    def get_company_research_endpoint(
        job_id: int, user: str | None = None
    ) -> dict[str, Any]:
        """Get saved company research for a job.

        Args:
            job_id: ID of the job.
            user: User name (required).

        Returns:
            Dictionary with company research data.

        Raises:
            HTTPException: If user not provided or research not found.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            import json

            from job_scout.config import user_db_path  # noqa: PLC0415
            from job_scout.database import Database  # noqa: PLC0415
            from job_scout.models import CompanyResearch  # noqa: PLC0415

            db = Database(user_db_path(user))

            job = db.get_job(job_id)
            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            research_json = db.get_company_research(job_id)
            if not research_json:
                raise HTTPException(
                    status_code=404,
                    detail=f"No research found for job {job_id}",
                )

            research = CompanyResearch.model_validate(json.loads(research_json))
            return research.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/profile/star-stories")
    def get_star_stories(
        user: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get all STAR stories for a user."""
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.database import Database  # noqa: PLC0415

            db = Database(user_db_path(user))
            stories = db.get_star_stories()
            return {"stories": stories}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/profile/star-stories")
    def create_star_story(
        story_data: dict[str, Any],
        user: str | None = None,
    ) -> dict[str, int]:
        """Create a new STAR story."""
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.database import Database  # noqa: PLC0415

            situation = story_data.get("situation", "")
            task = story_data.get("task", "")
            action = story_data.get("action", "")
            result = story_data.get("result", "")
            keywords = story_data.get("keywords", [])

            if not all([situation, task, action, result]):
                raise HTTPException(
                    status_code=400,
                    detail="Missing required fields",
                )

            if not isinstance(keywords, list):
                keywords = []

            # Type narrowing
            assert isinstance(situation, str)
            assert isinstance(task, str)
            assert isinstance(action, str)
            assert isinstance(result, str)

            db = Database(user_db_path(user))
            story_id = db.save_star_story(situation, task, action, result, keywords)
            return {"id": story_id}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.delete("/api/profile/star-stories/{story_id}")
    def delete_star_story(story_id: int, user: str | None = None) -> dict[str, str]:
        """Delete a STAR story."""
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.database import Database  # noqa: PLC0415

            db = Database(user_db_path(user))
            if not db.delete_star_story(story_id):
                raise HTTPException(
                    status_code=404,
                    detail=f"Story {story_id} not found",
                )
            return {"status": "deleted"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/interview-prep/{job_id}")
    def get_interview_prep(job_id: int, user: str | None = None) -> dict[str, Any]:
        """Generate interview preparation for a job.

        Extracts behavioral questions from the job description and matches them
        to the user's STAR stories for suggested interview answers.
        """
        if not user:
            raise HTTPException(status_code=400, detail="User is required")
        if user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        try:
            from job_scout.database import Database  # noqa: PLC0415
            from job_scout.interview_prep import (  # noqa: PLC0415
                generate_interview_prep,
            )
            from job_scout.models import StarStory  # noqa: PLC0415

            db = Database(user_db_path(user))

            # Get the job
            job = db.get_job(job_id)
            if not job:
                raise HTTPException(
                    status_code=404,
                    detail=f"Job {job_id} not found",
                )

            if not job.description:
                raise HTTPException(
                    status_code=400,
                    detail=f"Job {job_id} has no description",
                )

            # Get all STAR stories
            story_rows = db.get_star_stories()
            stories = [
                StarStory(
                    id=story["id"],
                    situation=story["situation"],
                    task=story["task"],
                    action=story["action"],
                    result=story["result"],
                    keywords=story["keywords"],
                    created_at=story["created_at"],
                    updated_at=story["updated_at"],
                )
                for story in story_rows
            ]

            # Generate interview prep
            prep = generate_interview_prep(job.description, stories, job_id=job_id)

            return prep.model_dump()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.get("/api/mcp/status")
    def get_mcp_status() -> dict[str, Any]:
        """Get MCP server status and configuration.

        Returns:
            Dictionary with MCP enabled status and port configuration.
        """
        try:
            config = load_config()
            return {
                "mcp_enabled": config.mcp_enabled,
                "mcp_port": config.mcp_port,
                "status": "configured" if config.mcp_enabled else "disabled",
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    @app.post("/api/mcp/config")
    def update_mcp_config(
        enabled: bool | None = None, port: int | None = None
    ) -> dict[str, Any]:
        """Update MCP server configuration.

        Args:
            enabled: Whether to enable the MCP server.
            port: Port number for the MCP server.

        Returns:
            Updated MCP configuration.
        """
        try:
            config = load_config()

            if enabled is not None:
                config.mcp_enabled = enabled
            if port is not None:
                if port < 1024 or port > 65535:
                    raise ValueError("Port must be between 1024 and 65535")
                config.mcp_port = port

            save_config(config)

            return {
                "mcp_enabled": config.mcp_enabled,
                "mcp_port": config.mcp_port,
                "status": "updated",
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc  # noqa: B904
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc  # noqa: B904

    return app


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the job-scout web dashboard server.

    Args:
        host: Host address to bind to (default 0.0.0.0).
        port: Port number to bind to (default 8000).
    """
    # Print security warning
    dashboard_token = load_secrets().get("dashboard_token")
    auth_status = (
        "token authentication ENABLED"
        if dashboard_token
        else "NO authentication (token not configured)"
    )
    click_warning = (
        f"\n{'=' * 70}\n"
        f"Dashboard is running at http://{host}:{port}\n"
        f"Authentication: {auth_status}\n"
        f"Use firewall rules or VPN to restrict access.\n"
        f"{'=' * 70}\n"
    )
    print(click_warning)  # noqa: T201

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
