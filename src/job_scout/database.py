"""SQLite database operations for job deduplication and storage."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_scout.models import (
    JobListing,
    JobStatus,
    RunHistoryEntry,
    RunStats,
    TravelTime,
)


def _dedup_key(title: str, company: str) -> str:
    """Build the indexed lookup key used for title+company deduplication.

    Args:
        title: Job title.
        company: Company name.

    Returns:
        Lowercased, whitespace-collapsed "title||company" key.
    """
    title_norm = " ".join(title.lower().split())
    company_norm = " ".join(company.lower().split())
    return f"{title_norm}||{company_norm}"


class Database:
    """SQLite-backed job storage with deduplication support."""

    def __init__(self, db_path: Path) -> None:
        """Initialize database and create schema if needed.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a database connection with auto-commit/rollback.

        Yields:
            sqlite3.Connection with row_factory set.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create the jobs and runs tables and indexes if they do not exist."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    url TEXT UNIQUE NOT NULL,
                    description TEXT,
                    source TEXT,
                    date_posted TEXT,
                    fit_score INTEGER,
                    fit_reasoning TEXT,
                    negative_match INTEGER DEFAULT 0,
                    negative_reasoning TEXT,
                    salary_min INTEGER,
                    salary_max INTEGER,
                    salary_period TEXT,
                    vacation_days INTEGER,
                    compensation_reasoning TEXT,
                    distance_km REAL,
                    travel_times_json TEXT DEFAULT '[]',
                    notified INTEGER DEFAULT 0,
                    notification_pending INTEGER DEFAULT 0,
                    seen_at TEXT NOT NULL,
                    status TEXT DEFAULT 'new',
                    location_unknown INTEGER DEFAULT 0
                )
            """)
            # Migrate existing databases that lack the new columns.
            for col, typedef in [
                ("salary_min", "INTEGER"),
                ("salary_max", "INTEGER"),
                ("salary_period", "TEXT"),
                ("vacation_days", "INTEGER"),
                ("compensation_reasoning", "TEXT"),
                ("distance_km", "REAL"),
                ("dedup_key", "TEXT"),
                ("approved_at", "TEXT"),
                ("approved_by", "TEXT"),
                ("approval_notes", "TEXT"),
                ("applied_at", "TEXT"),
                ("status_updated_at", "TEXT"),
                ("notes", "TEXT"),
            ]:
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON jobs(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dedup_key ON jobs(dedup_key)")
            self._backfill_dedup_keys(conn)
            # Create runs table for run history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    scraped INTEGER DEFAULT 0,
                    deduplicated INTEGER DEFAULT 0,
                    title_filtered INTEGER DEFAULT 0,
                    title_screened INTEGER DEFAULT 0,
                    quick_filtered INTEGER DEFAULT 0,
                    evaluated INTEGER DEFAULT 0,
                    matched INTEGER DEFAULT 0,
                    rejected INTEGER DEFAULT 0,
                    notified INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_started_at ON runs(started_at DESC)"
            )
            # Create geocode cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS geocode_cache (
                    normalized_address TEXT PRIMARY KEY,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            # Create travel time cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS travel_time_cache (
                    origin_key TEXT NOT NULL,
                    destination_key TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    minutes REAL NOT NULL,
                    cached_at TEXT NOT NULL,
                    PRIMARY KEY (origin_key, destination_key, mode)
                )
            """)
            # Create CV cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cv_cache (
                    cv_hash TEXT PRIMARY KEY,
                    cv_profile_json TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                )
            """)
            # Create tailored resumes table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tailored_resumes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    tailored_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_id_resume ON "
                "tailored_resumes(job_id)"
            )
            # Create cover letters table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cover_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL UNIQUE,
                    cover_letter_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_id_cover ON cover_letters(job_id)"
            )
            # Create screening questions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS screening_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_id_screening ON "
                "screening_questions(job_id)"
            )
            # Create company research table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS company_research (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    company_name TEXT NOT NULL,
                    research_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_id_research ON "
                "company_research(job_id)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS star_stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    situation TEXT NOT NULL,
                    task TEXT NOT NULL,
                    action TEXT NOT NULL,
                    result TEXT NOT NULL,
                    keywords TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def _backfill_dedup_keys(self, conn: sqlite3.Connection) -> None:
        """Populate dedup_key for rows written before the column existed.

        A one-time, per-row cost for legacy databases; new rows get their
        dedup_key set directly by save_job/save_jobs_batch.

        Args:
            conn: Open connection to use for the backfill.
        """
        rows = conn.execute(
            "SELECT id, title, company FROM jobs WHERE dedup_key IS NULL"
        ).fetchall()
        conn.executemany(
            "UPDATE jobs SET dedup_key = ? WHERE id = ?",
            [(_dedup_key(r[1], r[2]), r[0]) for r in rows],
        )

    def is_duplicate(self, job: JobListing) -> bool:
        """Check if a job already exists in the database.

        Checks by URL first, then by title+company combination.
        Uses aggressive normalization to catch cross-posted jobs with
        whitespace or formatting variations.

        Args:
            job: The job to check.

        Returns:
            True if the job is a duplicate.
        """
        with self._conn() as conn:
            if conn.execute("SELECT id FROM jobs WHERE url = ?", (job.url,)).fetchone():
                return True
            key = _dedup_key(job.title, job.company)
            row = conn.execute(
                "SELECT id FROM jobs WHERE dedup_key = ?", (key,)
            ).fetchone()
            return row is not None

    def get_cached_evaluation(
        self, job: JobListing
    ) -> tuple[int | None, dict[str, Any] | None]:
        """Retrieve cached evaluation results for a job with matching title+company.

        Uses the same normalization as is_duplicate to find jobs with identical
        normalized title+company. Returns the fit_score and full evaluation data
        if found and the job was already evaluated; otherwise returns (None, None).

        Args:
            job: The job to look up.

        Returns:
            (fit_score, evaluation_dict) if cached, or (None, None) if not found.
            evaluation_dict contains: fit_reasoning, negative_match, negative_reasoning,
            salary_min, salary_max, salary_period, vacation_days,
            compensation_reasoning.
        """
        key = _dedup_key(job.title, job.company)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT fit_score, fit_reasoning, negative_match,
                       negative_reasoning, salary_min, salary_max,
                       salary_period, vacation_days, compensation_reasoning
                FROM jobs
                WHERE dedup_key = ? AND fit_score IS NOT NULL
                ORDER BY seen_at DESC
                LIMIT 1
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None, None
        evaluation_dict = {
            "fit_reasoning": row[1],
            "negative_match": bool(row[2]),
            "negative_reasoning": row[3],
            "salary_min": row[4],
            "salary_max": row[5],
            "salary_period": row[6],
            "vacation_days": row[7],
            "compensation_reasoning": row[8],
        }
        return row[0], evaluation_dict

    def save_job(self, job: JobListing, *, update_existing: bool = False) -> int:
        """Persist a job to the database.

        Args:
            job: The job listing to save.
            update_existing: When True, upsert on URL conflict — overwrites
                evaluation columns but preserves ``seen_at``. When False,
                uses ``INSERT OR IGNORE`` (default, normal run behaviour).

        Returns:
            The row ID (new or existing), or 0 on conflict when not upserting.
        """
        travel_json = json.dumps([t.model_dump() for t in job.travel_times])
        date_str = job.date_posted.isoformat() if job.date_posted else None
        params = (
            job.title,
            job.company,
            job.location,
            job.url,
            job.description,
            job.source,
            date_str,
            job.fit_score,
            job.fit_reasoning,
            int(job.negative_match),
            job.negative_reasoning,
            job.salary_min,
            job.salary_max,
            job.salary_period,
            job.vacation_days,
            job.compensation_reasoning,
            job.distance_km,
            travel_json,
            int(job.notified),
            int(job.notification_pending),
            job.seen_at.isoformat(),
            job.status.value,
            int(job.location_unknown),
            _dedup_key(job.title, job.company),
        )
        with self._conn() as conn:
            if update_existing:
                cursor = conn.execute(
                    """
                    INSERT INTO jobs
                      (title, company, location, url, description, source, date_posted,
                       fit_score, fit_reasoning, negative_match, negative_reasoning,
                       salary_min, salary_max, salary_period, vacation_days,
                       compensation_reasoning,
                       distance_km, travel_times_json, notified, notification_pending,
                       seen_at, status, location_unknown, dedup_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET
                      fit_score=excluded.fit_score,
                      fit_reasoning=excluded.fit_reasoning,
                      negative_match=excluded.negative_match,
                      negative_reasoning=excluded.negative_reasoning,
                      salary_min=excluded.salary_min,
                      salary_max=excluded.salary_max,
                      salary_period=excluded.salary_period,
                      vacation_days=excluded.vacation_days,
                      compensation_reasoning=excluded.compensation_reasoning,
                      distance_km=excluded.distance_km,
                      travel_times_json=excluded.travel_times_json,
                      status=CASE WHEN jobs.status IN ('new', 'matched', 'rejected')
                               THEN excluded.status ELSE jobs.status END,
                      notified=excluded.notified,
                      notification_pending=excluded.notification_pending
                    RETURNING id
                    """,
                    params,
                )
                row = cursor.fetchone()
                return row[0] if row else 0
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO jobs
                  (title, company, location, url, description, source, date_posted,
                   fit_score, fit_reasoning, negative_match, negative_reasoning,
                   salary_min, salary_max, salary_period, vacation_days,
                   compensation_reasoning,
                   distance_km, travel_times_json, notified, notification_pending,
                   seen_at, status, location_unknown, dedup_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                params,
            )
            return cursor.lastrowid or 0

    def save_jobs_batch(
        self, jobs: list[JobListing], *, update_existing: bool = False
    ) -> list[int]:
        """Persist multiple jobs to the database in a single transaction.

        Batching reduces I/O overhead compared to saving jobs one-at-a-time.

        Args:
            jobs: List of job listings to save.
            update_existing: When True, upsert on URL conflict. When False,
                uses INSERT OR IGNORE.

        Returns:
            List of row IDs (new or existing), with 0 for conflicts when not upserting.
        """
        if not jobs:
            return []

        # Prepare parameters for all jobs in the batch
        all_params = []
        for job in jobs:
            travel_json = json.dumps([t.model_dump() for t in job.travel_times])
            date_str = job.date_posted.isoformat() if job.date_posted else None
            params = (
                job.title,
                job.company,
                job.location,
                job.url,
                job.description,
                job.source,
                date_str,
                job.fit_score,
                job.fit_reasoning,
                int(job.negative_match),
                job.negative_reasoning,
                job.salary_min,
                job.salary_max,
                job.salary_period,
                job.vacation_days,
                job.compensation_reasoning,
                job.distance_km,
                travel_json,
                int(job.notified),
                int(job.notification_pending),
                job.seen_at.isoformat(),
                job.status.value,
                int(job.location_unknown),
                _dedup_key(job.title, job.company),
            )
            all_params.append(params)

        row_ids = []
        with self._conn() as conn:
            if update_existing:
                sql = """
                    INSERT INTO jobs
                      (title, company, location, url, description, source, date_posted,
                       fit_score, fit_reasoning, negative_match, negative_reasoning,
                       salary_min, salary_max, salary_period, vacation_days,
                       compensation_reasoning,
                       distance_km, travel_times_json, notified, notification_pending,
                       seen_at, status, location_unknown, dedup_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(url) DO UPDATE SET
                      fit_score=excluded.fit_score,
                      fit_reasoning=excluded.fit_reasoning,
                      negative_match=excluded.negative_match,
                      negative_reasoning=excluded.negative_reasoning,
                      salary_min=excluded.salary_min,
                      salary_max=excluded.salary_max,
                      salary_period=excluded.salary_period,
                      vacation_days=excluded.vacation_days,
                      compensation_reasoning=excluded.compensation_reasoning,
                      distance_km=excluded.distance_km,
                      travel_times_json=excluded.travel_times_json,
                      status=CASE WHEN jobs.status IN ('new', 'matched', 'rejected')
                               THEN excluded.status ELSE jobs.status END,
                      notified=excluded.notified,
                      notification_pending=excluded.notification_pending
                    RETURNING id
                    """
                for params in all_params:
                    cursor = conn.execute(sql, params)
                    row = cursor.fetchone()
                    row_ids.append(row[0] if row else 0)
            else:
                sql = """
                    INSERT OR IGNORE INTO jobs
                      (title, company, location, url, description, source, date_posted,
                       fit_score, fit_reasoning, negative_match, negative_reasoning,
                       salary_min, salary_max, salary_period, vacation_days,
                       compensation_reasoning,
                       distance_km, travel_times_json, notified, notification_pending,
                       seen_at, status, location_unknown, dedup_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """
                for params in all_params:
                    cursor = conn.execute(sql, params)
                    row_ids.append(cursor.lastrowid or 0)
        return row_ids

    def mark_notified(self, job_id: int) -> None:
        """Mark a job as successfully notified.

        Args:
            job_id: Database row ID of the job.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET notified=1, notification_pending=0 WHERE id=?",
                (job_id,),
            )

    def mark_notification_pending(self, job_id: int) -> None:
        """Mark a job as pending notification (ntfy failed).

        Args:
            job_id: Database row ID of the job.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET notification_pending=1 WHERE id=?",
                (job_id,),
            )

    def get_pending_notifications(self) -> list[JobListing]:
        """Return jobs that are awaiting a retry notification.

        Returns:
            List of JobListing instances with pending notifications.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE notification_pending=1"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_recent_matches(
        self,
        limit: int = 20,
        min_score: int | None = None,
        source: str | None = None,
        sort: str = "date_desc",
    ) -> list[JobListing]:
        """Return the most recently matched jobs with optional filtering and sorting.

        Args:
            limit: Maximum number of results.
            min_score: Optional minimum fit score to filter by (inclusive).
            source: Optional source name to filter by (exact match).
            sort: Sort order - one of 'score_desc', 'score_asc',
                'date_desc' (default), 'date_asc'.

        Returns:
            List of matched JobListing instances.
        """
        # Map sort options to SQL ORDER BY clauses
        sort_map = {
            "score_desc": "fit_score DESC",
            "score_asc": "fit_score ASC",
            "date_desc": "seen_at DESC",
            "date_asc": "seen_at ASC",
        }
        order_clause = sort_map.get(sort, "seen_at DESC")

        # Build WHERE clause conditionally
        where_parts = ["status = ?"]
        params: list[int | str] = ["matched"]

        if min_score is not None:
            where_parts.append("fit_score >= ?")
            params.append(min_score)

        if source is not None:
            where_parts.append("source = ?")
            params.append(source)

        where_clause = " AND ".join(where_parts)
        params.append(limit)

        with self._conn() as conn:
            sql = f"SELECT * FROM jobs WHERE {where_clause} ORDER BY {order_clause} LIMIT ?"  # noqa: E501
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_rejected_jobs(
        self,
        limit: int = 20,
        min_score: int | None = None,
        source: str | None = None,
        sort: str = "date_desc",
    ) -> list[JobListing]:
        """Return the most recently rejected jobs with optional filtering and sorting.

        Args:
            limit: Maximum number of results.
            min_score: Optional minimum fit score to filter by (inclusive).
            source: Optional source name to filter by (exact match).
            sort: Sort order - one of 'score_desc', 'score_asc',
                'date_desc' (default), 'date_asc'.

        Returns:
            List of rejected JobListing instances.
        """
        # Map sort options to SQL ORDER BY clauses
        sort_map = {
            "score_desc": "fit_score DESC",
            "score_asc": "fit_score ASC",
            "date_desc": "seen_at DESC",
            "date_asc": "seen_at ASC",
        }
        order_clause = sort_map.get(sort, "seen_at DESC")

        # Build WHERE clause conditionally
        where_parts = ["status = ?"]
        params: list[int | str] = ["rejected"]

        if min_score is not None:
            where_parts.append("fit_score >= ?")
            params.append(min_score)

        if source is not None:
            where_parts.append("source = ?")
            params.append(source)

        where_clause = " AND ".join(where_parts)
        params.append(limit)

        with self._conn() as conn:
            sql = f"SELECT * FROM jobs WHERE {where_clause} ORDER BY {order_clause} LIMIT ?"  # noqa: E501
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_all_jobs(self) -> list[JobListing]:
        """Return all jobs in the database.

        Returns:
            List of all JobListing instances.
        """
        with self._conn() as conn:
            sql = "SELECT * FROM jobs ORDER BY seen_at DESC"
            rows = conn.execute(sql).fetchall()
        return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row: sqlite3.Row) -> JobListing:
        """Convert a database row to a JobListing.

        Args:
            row: sqlite3.Row from the jobs table.

        Returns:
            JobListing populated with row data.
        """
        raw: dict[str, Any] = dict(row)
        raw_travel = raw.get("travel_times_json") or "[]"
        travel_data: list[dict[str, Any]] = json.loads(raw_travel)
        travel_times = [TravelTime(**t) for t in travel_data]
        date_posted: datetime | None = None
        if raw.get("date_posted"):
            date_posted = datetime.fromisoformat(str(raw["date_posted"]))
        approved_at: datetime | None = None
        if raw.get("approved_at"):
            approved_at = datetime.fromisoformat(str(raw["approved_at"]))
        applied_at: datetime | None = None
        if raw.get("applied_at"):
            applied_at = datetime.fromisoformat(str(raw["applied_at"]))
        status_updated_at: datetime | None = None
        if raw.get("status_updated_at"):
            status_updated_at = datetime.fromisoformat(str(raw["status_updated_at"]))
        return JobListing(
            id=raw["id"],
            title=raw["title"],
            company=raw["company"],
            location=raw.get("location"),
            url=raw["url"],
            description=raw.get("description"),
            source=raw.get("source") or "",
            date_posted=date_posted,
            fit_score=raw.get("fit_score"),
            fit_reasoning=raw.get("fit_reasoning"),
            negative_match=bool(raw.get("negative_match", 0)),
            negative_reasoning=raw.get("negative_reasoning"),
            salary_min=raw.get("salary_min"),
            salary_max=raw.get("salary_max"),
            salary_period=raw.get("salary_period"),
            vacation_days=raw.get("vacation_days"),
            compensation_reasoning=raw.get("compensation_reasoning"),
            distance_km=raw.get("distance_km"),
            travel_times=travel_times,
            notified=bool(raw.get("notified", 0)),
            notification_pending=bool(raw.get("notification_pending", 0)),
            seen_at=datetime.fromisoformat(str(raw["seen_at"])),
            status=JobStatus(raw.get("status") or "new"),
            location_unknown=bool(raw.get("location_unknown", 0)),
            approved_at=approved_at,
            approved_by=raw.get("approved_by"),
            approval_notes=raw.get("approval_notes"),
            applied_at=applied_at,
            status_updated_at=status_updated_at,
            notes=raw.get("notes"),
        )

    def log_stats(self) -> dict[str, int]:
        """Return counts per status for logging.

        Returns:
            Dict mapping status names to counts.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
            ).fetchall()
        return {str(r["status"]): int(r["cnt"]) for r in rows}

    def save_run_stats(
        self, stats: RunStats, started_at: datetime, duration_seconds: float
    ) -> None:
        """Persist run statistics to the runs table.

        Args:
            stats: RunStats object containing pipeline statistics.
            started_at: When the run started.
            duration_seconds: How long the run took.
        """
        errors_count = len(stats.errors) if stats.errors else 0
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO runs
                  (started_at, duration_seconds, scraped, deduplicated,
                   title_filtered, title_screened, quick_filtered, evaluated,
                   matched, rejected, notified, errors)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at.isoformat(),
                    duration_seconds,
                    stats.scraped,
                    stats.deduplicated,
                    stats.title_filtered,
                    stats.title_screened,
                    stats.quick_filtered,
                    stats.evaluated,
                    stats.matched,
                    stats.rejected,
                    stats.notified,
                    errors_count,
                ),
            )

    def get_run_history(self, limit: int = 30) -> list[RunHistoryEntry]:
        """Retrieve recent run history, most recent first.

        Args:
            limit: Maximum number of runs to return (default 30).

        Returns:
            List of RunHistoryEntry objects, most recent first.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT started_at, duration_seconds, scraped, deduplicated,
                       title_filtered, title_screened, quick_filtered, evaluated,
                       matched, rejected, notified, errors
                FROM runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        entries = []
        for row in rows:
            entries.append(
                RunHistoryEntry(
                    started_at=datetime.fromisoformat(row[0]),
                    duration_seconds=row[1],
                    scraped=row[2],
                    deduplicated=row[3],
                    title_filtered=row[4],
                    title_screened=row[5],
                    quick_filtered=row[6],
                    evaluated=row[7],
                    matched=row[8],
                    rejected=row[9],
                    notified=row[10],
                    errors=row[11],
                )
            )
        return entries

    def get_cached_geocode(
        self, address: str, cache_days: int
    ) -> tuple[float, float] | None:
        """Retrieve cached geocode for an address if still valid.

        Args:
            address: Address string to look up.
            cache_days: Cache validity period in days.

        Returns:
            (lon, lat) tuple if cached and valid, None otherwise.
        """
        norm_addr = self._normalize_address(address)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT lat, lon, cached_at FROM geocode_cache
                WHERE normalized_address = ?
                """,
                (norm_addr,),
            ).fetchone()

        if not row:
            return None

        cached_at = datetime.fromisoformat(row[2])
        age_days = (datetime.now(UTC) - cached_at).days
        if age_days >= cache_days:
            return None

        return float(row[1]), float(row[0])  # (lon, lat)

    def save_geocode_cache(self, address: str, lat: float, lon: float) -> None:
        """Cache a geocoded address.

        Args:
            address: Original address string.
            lat: Latitude.
            lon: Longitude.
        """
        norm_addr = self._normalize_address(address)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO geocode_cache
                  (normalized_address, lat, lon, cached_at)
                VALUES (?, ?, ?, ?)
                """,
                (norm_addr, lat, lon, datetime.now(UTC).isoformat()),
            )

    def get_cached_travel_time(
        self, origin_key: str, destination_key: str, mode: str, cache_days: int
    ) -> float | None:
        """Retrieve cached travel time if still valid.

        Args:
            origin_key: Origin location key.
            destination_key: Destination location key.
            mode: Travel mode (e.g. 'car', 'bike', 'public_transport').
            cache_days: Cache validity period in days.

        Returns:
            Travel time in minutes if cached and valid, None otherwise.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT minutes, cached_at FROM travel_time_cache
                WHERE origin_key = ? AND destination_key = ? AND mode = ?
                """,
                (origin_key, destination_key, mode),
            ).fetchone()

        if not row:
            return None

        cached_at = datetime.fromisoformat(row[1])
        age_days = (datetime.now(UTC) - cached_at).days
        if age_days >= cache_days:
            return None

        return float(row[0])

    def save_travel_time_cache(
        self,
        origin_key: str,
        destination_key: str,
        mode: str,
        minutes: float,
    ) -> None:
        """Cache a travel time result.

        Args:
            origin_key: Origin location key.
            destination_key: Destination location key.
            mode: Travel mode (e.g. 'car', 'bike', 'public_transport').
            minutes: Travel time in minutes.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO travel_time_cache
                  (origin_key, destination_key, mode, minutes, cached_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    origin_key,
                    destination_key,
                    mode,
                    minutes,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_cached_cv_profile(self, cv_hash: str) -> str | None:
        """Retrieve a cached CV profile JSON if it exists.

        Args:
            cv_hash: SHA256 hash of the raw CV text.

        Returns:
            JSON string of CvProfile if cached, None otherwise.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cv_profile_json FROM cv_cache WHERE cv_hash = ?",
                (cv_hash,),
            ).fetchone()
        return row[0] if row else None

    def save_cv_profile_cache(self, cv_hash: str, cv_profile_json: str) -> None:
        """Cache a parsed CV profile.

        Args:
            cv_hash: SHA256 hash of the raw CV text.
            cv_profile_json: JSON string representation of CvProfile.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cv_cache
                  (cv_hash, cv_profile_json, cached_at)
                VALUES (?, ?, ?)
                """,
                (cv_hash, cv_profile_json, datetime.now(UTC).isoformat()),
            )

    def get_tailored_resume(self, job_id: int) -> str | None:
        """Retrieve a previously tailored resume for a job.

        Args:
            job_id: ID of the job.

        Returns:
            Tailored resume text if it exists, None otherwise.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT tailored_text FROM tailored_resumes WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return row[0] if row else None

    def save_tailored_resume(self, job_id: int, tailored_text: str) -> None:
        """Save a tailored resume for a job.

        Args:
            job_id: ID of the job.
            tailored_text: The tailored resume content.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tailored_resumes
                  (job_id, tailored_text, created_at)
                VALUES (?, ?, ?)
                """,
                (job_id, tailored_text, datetime.now(UTC).isoformat()),
            )

    def get_cover_letter(self, job_id: int) -> str | None:
        """Retrieve a previously generated cover letter for a job.

        Args:
            job_id: ID of the job.

        Returns:
            Cover letter text if it exists, None otherwise.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT cover_letter_text FROM cover_letters WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return row[0] if row else None

    def save_cover_letter(self, job_id: int, cover_letter_text: str) -> None:
        """Save a generated cover letter for a job.

        Args:
            job_id: ID of the job.
            cover_letter_text: The cover letter content.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cover_letters
                  (job_id, cover_letter_text, created_at)
                VALUES (?, ?, ?)
                """,
                (job_id, cover_letter_text, datetime.now(UTC).isoformat()),
            )

    def get_screening_questions(self, job_id: int) -> list[tuple[str, str | None]]:
        """Retrieve screening questions and answers for a job.

        Args:
            job_id: ID of the job.

        Returns:
            List of (question, answer) tuples. If no questions exist,
            returns an empty list.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT question, answer FROM screening_questions
                WHERE job_id = ? ORDER BY id
                """,
                (job_id,),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def save_screening_questions(
        self,
        job_id: int,
        questions: list[str],
        answers: dict[str, str] | None = None,
    ) -> None:
        """Save screening questions and answers for a job.

        Args:
            job_id: ID of the job.
            questions: List of screening questions.
            answers: Optional dict mapping questions to answers.
        """
        answers = answers or {}
        with self._conn() as conn:
            # Delete existing questions for this job
            conn.execute("DELETE FROM screening_questions WHERE job_id = ?", (job_id,))
            # Insert new questions and answers
            now = datetime.now(UTC).isoformat()
            for question in questions:
                answer = answers.get(question)
                conn.execute(
                    """
                    INSERT INTO screening_questions
                      (job_id, question, answer, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (job_id, question, answer, now),
                )

    def approve_job(
        self, job_id: int, approved_by: str, notes: str | None = None
    ) -> None:
        """Approve a job and transition it to APPROVED status.

        Args:
            job_id: ID of the job to approve.
            approved_by: Name of the approver (user).
            notes: Optional approval notes.
        """
        from job_scout.models import JobStatus

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, approved_at = ?, approved_by = ?,
                    approval_notes = ?
                WHERE id = ?
                """,
                (
                    JobStatus.APPROVED.value,
                    datetime.now(UTC).isoformat(),
                    approved_by,
                    notes,
                    job_id,
                ),
            )

    def update_job_status(
        self, job_id: int, new_status: JobStatus, notes: str | None = None
    ) -> bool:
        """Update a job's status with validation.

        Args:
            job_id: ID of the job to update.
            new_status: New status.
            notes: Optional notes to attach to the status update.

        Returns:
            True if status was updated, False if transition is invalid.
        """
        from job_scout.models import ApplicationTracker, JobStatus

        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT status, applied_at FROM jobs WHERE id = ?", (job_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False
            current_status = JobStatus(row[0])
            if not ApplicationTracker.can_transition(current_status, new_status):
                return False
            now = datetime.now(UTC).isoformat()
            applied_at = row["applied_at"]
            if new_status == JobStatus.SUBMITTED and applied_at is None:
                applied_at = now
            update_params = (new_status.value, now, applied_at, notes, job_id)
            conn.execute(
                """UPDATE jobs SET status = ?, status_updated_at = ?,
                   applied_at = ?, notes = ? WHERE id = ?""",
                update_params,
            )
            return True

    def get_jobs_by_status(self, status: JobStatus) -> list[JobListing]:
        """Get all jobs with a specific status.

        Args:
            status: Status to filter by.

        Returns:
            List of jobs with the given status.
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY seen_at DESC",
                (status.value,),
            )
            return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_approval_queue(self) -> list[JobListing]:
        """Get jobs awaiting approval (NEW or VIEWED status).

        Returns:
            List of jobs needing approval, ordered by seen_at.
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status IN (?, ?)
                ORDER BY seen_at DESC
                """,
                ("new", "viewed"),
            )
            return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_job(self, job_id: int) -> JobListing | None:
        """Get a single job by ID.

        Args:
            job_id: ID of the job to retrieve.

        Returns:
            JobListing if found, None otherwise.
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            return self._row_to_job(row) if row else None

    @staticmethod
    def _normalize_address(address: str) -> str:
        """Normalize an address string for caching.

        Lowercases and collapses whitespace to create a consistent key.

        Args:
            address: Address string to normalize.

        Returns:
            Normalized address key.
        """
        return " ".join(address.lower().split())

    def save_company_research(self, job_id: int, research_json: str) -> None:
        """Save or update company research for a job.

        Args:
            job_id: ID of the job.
            research_json: JSON string of CompanyResearch object.
        """
        from datetime import UTC, datetime

        now_iso = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            # Check if research already exists
            cursor = conn.execute(
                "SELECT id FROM company_research WHERE job_id = ?",
                (job_id,),
            )
            existing = cursor.fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE company_research
                    SET research_json = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (research_json, now_iso, job_id),
                )
            else:
                # Get company name from job
                cursor = conn.execute(
                    "SELECT company FROM jobs WHERE id = ?", (job_id,)
                )
                row = cursor.fetchone()
                company_name = row[0] if row else "Unknown"

                conn.execute(
                    """
                    INSERT INTO company_research
                    (job_id, company_name, research_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, company_name, research_json, now_iso, now_iso),
                )
            conn.commit()

    def get_company_research(self, job_id: int) -> str | None:
        """Get company research JSON for a job.

        Args:
            job_id: ID of the job.

        Returns:
            JSON string of CompanyResearch, or None if not found.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT research_json FROM company_research WHERE job_id = ?",
                (job_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def save_star_story(
        self, situation: str, task: str, action: str, result: str, keywords: list[str]
    ) -> int:
        """Save a STAR (Situation, Task, Action, Result) story.

        Args:
            situation: The situation or context.
            task: The task or challenge faced.
            action: The specific action taken.
            result: The measurable result achieved.
            keywords: Keywords for matching to interview questions.

        Returns:
            ID of the saved story.
        """
        from datetime import UTC  # noqa: F811

        now_iso = datetime.now(UTC).isoformat()
        keywords_json = json.dumps(keywords)

        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO star_stories
                (situation, task, action, result, keywords, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (situation, task, action, result, keywords_json, now_iso, now_iso),
            )
            return cursor.lastrowid or 0

    def get_star_stories(self) -> list[dict[str, Any]]:
        """Get all STAR stories.

        Returns:
            List of STAR stories as dictionaries.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, situation, task, action, result, keywords,
                       created_at, updated_at
                FROM star_stories
                ORDER BY created_at DESC
                """
            )
            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row[0],
                        "situation": row[1],
                        "task": row[2],
                        "action": row[3],
                        "result": row[4],
                        "keywords": json.loads(row[5]),
                        "created_at": row[6],
                        "updated_at": row[7],
                    }
                )
            return results

    def get_star_story(self, story_id: int) -> dict[str, Any] | None:
        """Get a single STAR story by ID.

        Args:
            story_id: ID of the story.

        Returns:
            Story as dictionary, or None if not found.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, situation, task, action, result, keywords,
                       created_at, updated_at
                FROM star_stories
                WHERE id = ?
                """,
                (story_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "situation": row[1],
                "task": row[2],
                "action": row[3],
                "result": row[4],
                "keywords": json.loads(row[5]),
                "created_at": row[6],
                "updated_at": row[7],
            }

    def update_star_story(
        self,
        story_id: int,
        situation: str,
        task: str,
        action: str,
        result: str,
        keywords: list[str],
    ) -> bool:
        """Update a STAR story.

        Args:
            story_id: ID of the story to update.
            situation: Updated situation.
            task: Updated task.
            action: Updated action.
            result: Updated result.
            keywords: Updated keywords.

        Returns:
            True if a row was updated, False otherwise.
        """
        from datetime import UTC  # noqa: F811

        now_iso = datetime.now(UTC).isoformat()
        keywords_json = json.dumps(keywords)

        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE star_stories
                SET situation = ?, task = ?, action = ?, result = ?,
                    keywords = ?, updated_at = ?
                WHERE id = ?
                """,
                (situation, task, action, result, keywords_json, now_iso, story_id),
            )
            return cursor.rowcount > 0

    def delete_star_story(self, story_id: int) -> bool:
        """Delete a STAR story.

        Args:
            story_id: ID of the story to delete.

        Returns:
            True if a row was deleted, False otherwise.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM star_stories WHERE id = ?",
                (story_id,),
            )
            return cursor.rowcount > 0
