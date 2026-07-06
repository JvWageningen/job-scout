"""SQLite database operations for job deduplication and storage."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from job_scout.models import JobListing, JobStatus, TravelTime


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
        """Create the jobs table and indexes if they do not exist."""
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
            ]:
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON jobs(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dedup_key ON jobs(dedup_key)")
            self._backfill_dedup_keys(conn)

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
                      status=excluded.status,
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
                      status=excluded.status,
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
