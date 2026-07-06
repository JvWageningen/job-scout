"""Export job listings to various formats (CSV, JSON)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Literal

from job_scout.models import JobListing


class JobExporter:
    """Export job listings to various formats."""

    @staticmethod
    def to_csv(jobs: list[JobListing]) -> str:
        """Export job listings to CSV format.

        Args:
            jobs: List of job listings to export.

        Returns:
            CSV-formatted string with all job data.
        """
        output = io.StringIO()
        if not jobs:
            return ""

        # Define CSV columns
        fieldnames = [
            "id",
            "title",
            "company",
            "location",
            "url",
            "source",
            "date_posted",
            "fit_score",
            "fit_reasoning",
            "salary_min",
            "salary_max",
            "salary_period",
            "vacation_days",
            "compensation_reasoning",
            "distance_km",
            "status",
            "approved_at",
            "applied_at",
            "status_updated_at",
            "notes",
        ]

        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for job in jobs:
            row = {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location or "",
                "url": job.url,
                "source": job.source,
                "date_posted": (job.date_posted.isoformat() if job.date_posted else ""),
                "fit_score": job.fit_score or "",
                "fit_reasoning": job.fit_reasoning or "",
                "salary_min": job.salary_min or "",
                "salary_max": job.salary_max or "",
                "salary_period": job.salary_period or "",
                "vacation_days": job.vacation_days or "",
                "compensation_reasoning": (job.compensation_reasoning or ""),
                "distance_km": job.distance_km or "",
                "status": job.status.value,
                "approved_at": (job.approved_at.isoformat() if job.approved_at else ""),
                "applied_at": (job.applied_at.isoformat() if job.applied_at else ""),
                "status_updated_at": (
                    job.status_updated_at.isoformat() if job.status_updated_at else ""
                ),
                "notes": job.notes or "",
            }
            writer.writerow(row)

        return output.getvalue()

    @staticmethod
    def to_json(jobs: list[JobListing]) -> str:
        """Export job listings to JSON format.

        Args:
            jobs: List of job listings to export.

        Returns:
            JSON-formatted string with all job data.
        """
        # Convert jobs to dictionaries, handling datetime serialization
        jobs_data = []
        for job in jobs:
            job_dict = job.model_dump()
            # Convert datetime objects to ISO format strings
            for key in [
                "date_posted",
                "seen_at",
                "approved_at",
                "applied_at",
                "status_updated_at",
            ]:
                if key in job_dict and isinstance(job_dict[key], datetime):
                    job_dict[key] = job_dict[key].isoformat()
            # Convert status enum to string
            if "status" in job_dict:
                job_dict["status"] = job_dict["status"].value
            jobs_data.append(job_dict)

        return json.dumps(jobs_data, indent=2, default=str)

    @staticmethod
    def export(
        jobs: list[JobListing],
        format: Literal["csv", "json"] = "json",
    ) -> str:
        """Export jobs to the specified format.

        Args:
            jobs: List of job listings to export.
            format: Export format ('csv' or 'json').

        Returns:
            Formatted export string.

        Raises:
            ValueError: If format is not supported.
        """
        if format == "csv":
            return JobExporter.to_csv(jobs)
        elif format == "json":
            return JobExporter.to_json(jobs)
        else:
            msg = f"Unsupported export format: {format}"
            raise ValueError(msg)
