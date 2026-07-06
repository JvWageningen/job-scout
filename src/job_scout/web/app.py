"""FastAPI application for the job-scout web dashboard."""

from __future__ import annotations

import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse

from job_scout.config import (
    apply_user_init,
    build_effective_config,
    list_users,
    load_secrets,
    load_user_config,
    save_user_config,
    set_config_value,
    update_secrets,
    user_db_path,
    user_logs_dir,
)
from job_scout.database import Database
from job_scout.llm.factory import build_raw_client_for_test, get_llm_client
from job_scout.models import Config, JobListing
from job_scout.scheduler import check_schedule_status, install_schedule, remove_schedule

# Global registry for tracking run status
# Keyed by user name (or None for global)
_run_registry: dict[str | None, dict[str, Any]] = {}
_registry_lock = threading.Lock()


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

    # --- Static file serving ---
    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        """Serve the main dashboard HTML file."""
        static_dir = Path(__file__).parent / "static"
        return FileResponse(static_dir / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def serve_app_js() -> FileResponse:
        """Serve the main application JavaScript file."""
        static_dir = Path(__file__).parent / "static"
        return FileResponse(static_dir / "app.js")

    @app.get("/style.css", include_in_schema=False)
    def serve_style_css() -> FileResponse:
        """Serve the stylesheet."""
        static_dir = Path(__file__).parent / "static"
        return FileResponse(static_dir / "style.css")

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
            config = build_effective_config(user) if user else Config()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # Mask secret fields (show only last 4 chars)
        config_dict = config.model_dump()
        for key in config_dict:
            if "key" in key.lower() and config_dict[key]:
                config_dict[key] = f"***{str(config_dict[key])[-4:]}"
        return config_dict

    @app.get("/api/jobs/matched")
    def get_matched_jobs(
        user: str | None = None, limit: int = Query(20, ge=1, le=100)
    ) -> list[JobListing]:
        """Get recently matched jobs for a user.

        Args:
            user: User name (required).
            limit: Maximum number of jobs to return (default 20, max 100).

        Returns:
            List of matched job listings.

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
            return db.get_recent_matches(limit)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/jobs/rejected")
    def get_rejected_jobs(
        user: str | None = None, limit: int = Query(20, ge=1, le=100)
    ) -> list[JobListing]:
        """Get recently rejected jobs for a user.

        Args:
            user: User name (required).
            limit: Maximum number of jobs to return (default 20, max 100).

        Returns:
            List of rejected job listings.

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
            return db.get_rejected_jobs(limit)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/schedule/status")
    def get_schedule_status() -> dict[str, str]:
        """Get the current schedule status.

        Returns:
            Dictionary with schedule status message.
        """
        try:
            status = check_schedule_status()
            return {"status": status}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # --- POST Endpoints for Configuration ---

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
                    set_config_value(key, str(value), user=user)
                except ValueError as e:
                    errors[key] = str(e)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        if errors:
            return {"status": "partial", "errors": errors}
        return {"status": "success"}

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/sites")
    def add_site(body: dict[str, Any]) -> dict[str, str]:
        """Add a custom site for a user.

        Args:
            body: Request body with 'user', 'url', and optional 'name'.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If user not found or site already exists.
        """
        from urllib.parse import urlparse

        user = body.get("user")
        url = body.get("url", "").strip()
        name = body.get("name", "").strip()

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

            sites_list.append({"name": resolved_name, "url": url, "enabled": True})
            cfg["custom_sites"] = sites_list
            save_user_config(user, cfg)
            return {"status": f"Added site '{resolved_name}'"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/schedule")
    def set_schedule(body: dict[str, Any]) -> dict[str, str]:
        """Install a daily schedule for job-scout runs.

        Args:
            body: Request body with 'hour' and 'minute'.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If schedule installation fails.
        """
        try:
            hour = int(body.get("hour", 8))
            minute = int(body.get("minute", 0))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise HTTPException(status_code=400, detail="Invalid hour or minute")
            install_schedule(hour=hour, minute=minute)
            return {"status": f"Schedule installed for {hour:02d}:{minute:02d}"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.delete("/api/schedule")
    def unset_schedule() -> dict[str, str]:
        """Remove the daily schedule.

        Returns:
            Dictionary with status message.

        Raises:
            HTTPException: If schedule removal fails.
        """
        try:
            remove_schedule()
            return {"status": "Schedule removed"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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

    @app.post("/api/run")
    def start_run(body: dict[str, Any]) -> dict[str, Any]:
        """Start a background pipeline run for a user.

        Args:
            body: Request body with 'user' (optional), 'dry_run' (bool, default True),
                  and 'full' (bool, default False).

        Returns:
            Dictionary with 'status' and 'message'.

        Raises:
            HTTPException: If parameters are invalid or a run is already in progress.
        """
        from job_scout.cli import _execute_run
        from job_scout.evaluator import check_llm_available

        user = body.get("user")
        dry_run = body.get("dry_run", True)
        full = body.get("full", False)

        if user and user not in list_users():
            raise HTTPException(status_code=404, detail=f"User '{user}' not found")

        # Check if a run is already in progress for this user
        with _registry_lock:
            if user in _run_registry and _run_registry[user]["status"] == "running":
                raise HTTPException(
                    status_code=409,
                    detail=f"Run already in progress for user '{user or 'global'}'",
                )

            # Initialize the registry entry
            _run_registry[user] = {
                "status": "running",
                "start_time": datetime.now(),
                "end_time": None,
                "result": None,
                "error": None,
            }

        def _run_task() -> None:
            """Background task to run the pipeline."""
            try:
                config = build_effective_config(user) if user else None
                if config and not config.profile_description:
                    with _registry_lock:
                        _run_registry[user]["status"] = "error"
                        _run_registry[user]["error"] = (
                            f"No profile configured for user '{user}'"
                        )
                    return

                if user:
                    # Check LLM availability
                    config = build_effective_config(user)
                    ok, err = check_llm_available(config)
                    if not ok:
                        with _registry_lock:
                            _run_registry[user]["status"] = "error"
                            _run_registry[user]["error"] = err
                        return

                    # Execute the run
                    _execute_run(user, dry_run=dry_run, full=full)
                else:
                    # Global run (no user specified)
                    from job_scout.cli import _execute_run_global

                    _execute_run_global(dry_run=dry_run, full=full)

                with _registry_lock:
                    _run_registry[user]["status"] = "done"
                    _run_registry[user]["end_time"] = datetime.now()
            except (Exception, SystemExit) as e:
                logger.exception(f"Error during pipeline run for {user or 'global'}")
                with _registry_lock:
                    _run_registry[user]["status"] = "error"
                    _run_registry[user]["error"] = str(e)
                    _run_registry[user]["end_time"] = datetime.now()

        # Start the background thread
        thread = threading.Thread(target=_run_task, daemon=True)
        thread.start()

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

        return result

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
