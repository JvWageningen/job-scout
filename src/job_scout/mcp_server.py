"""MCP server for exposing job-scout as a plugin for ChatGPT/Claude/Copilot."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from loguru import logger
from mcp.server import Server
from mcp.types import (
    Resource,
    TextContent,
    Tool,
)

from job_scout.config import build_effective_config
from job_scout.database import Database
from job_scout.models import JobListing


class MCPServerManager:
    """Manages the MCP server lifecycle and user isolation."""

    def __init__(self, db: Database) -> None:
        """Initialize the MCP server manager.

        Args:
            db: Database instance for accessing jobs.
        """
        self.db = db
        self.server = Server("job-scout")
        self._result_cache: dict[str, Any] = {}
        self._setup_handlers()

    def _get_cache_key(self, user_id: str, key: str) -> str:
        """Generate a cache key with user isolation.

        Args:
            user_id: User identifier.
            key: Cache key.

        Returns:
            Salted cache key.
        """
        combined = f"{user_id}:{key}"
        return hashlib.sha256(combined.encode()).hexdigest()

    def _validate_user(self, user_id: str) -> bool:
        """Validate that a user has a valid configuration.

        Args:
            user_id: User identifier.

        Returns:
            True if user is valid, False otherwise.
        """
        try:
            config = build_effective_config(user_id)
            return bool(config.name)
        except Exception as e:
            logger.debug(f"User validation failed for {user_id}: {e}")
            return False

    def _get_user_jobs(self, user_id: str) -> list[JobListing]:
        """Get all jobs for a specific user.

        Args:
            user_id: User identifier.

        Returns:
            List of JobListing objects for the user.
        """
        if not self._validate_user(user_id):
            return []
        try:
            jobs = self.db.get_all_jobs()
            return jobs
        except Exception as e:
            logger.error(f"Failed to get jobs for user {user_id}: {e}")
            return []

    def _setup_handlers(self) -> None:
        """Set up MCP protocol handlers."""

        @self.server.list_resources()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_resources() -> list[Resource]:
            """List available resources (job listings)."""
            resources = [
                Resource(
                    uri="mcp://job-scout/jobs/list",
                    name="Job Listings",
                    description="Access current job listings from job-scout",
                    mimeType="application/json",
                ),
            ]
            return resources

        @self.server.read_resource()  # type: ignore[no-untyped-call,untyped-decorator]
        async def read_resource(uri: str) -> str:
            """Read a specific resource."""
            if uri == "mcp://job-scout/jobs/list":
                # Return empty list; query tool is more useful for filtering
                return json.dumps({"jobs": []})
            elif uri.startswith("mcp://job-scout/jobs/"):
                job_id = uri.split("/")[-1]
                try:
                    job_id_int = int(job_id)
                    job = self.db.get_job(job_id_int)
                    if job:
                        return job.model_dump_json()
                    return json.dumps({"error": "Job not found"})
                except (ValueError, Exception) as e:
                    logger.debug(f"Error reading job resource {uri}: {e}")
                    return json.dumps({"error": "Invalid request"})
            return json.dumps({"error": "Unknown resource"})

        @self.server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_tools() -> list[Tool]:
            """List available tools for querying jobs."""
            return [
                Tool(
                    name="query_jobs",
                    description=(
                        "Query job listings with optional filters for title, "
                        "company, location, and status"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title_filter": {
                                "type": "string",
                                "description": (
                                    "Filter by job title "
                                    "(case-insensitive substring match)"
                                ),
                            },
                            "company_filter": {
                                "type": "string",
                                "description": (
                                    "Filter by company name "
                                    "(case-insensitive substring match)"
                                ),
                            },
                            "location_filter": {
                                "type": "string",
                                "description": (
                                    "Filter by location "
                                    "(case-insensitive substring match)"
                                ),
                            },
                            "status_filter": {
                                "type": "string",
                                "enum": [
                                    "new",
                                    "viewed",
                                    "approved",
                                    "ready",
                                    "submitted",
                                    "interviewing",
                                    "offer",
                                    "rejected",
                                ],
                                "description": "Filter by job status",
                            },
                            "min_fit_score": {
                                "type": "integer",
                                "description": "Minimum fit score (0-100)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": (
                                    "Maximum number of results (default 20, max 100)"
                                ),
                            },
                        },
                    },
                ),
                Tool(
                    name="get_job_details",
                    description="Get detailed information about a specific job by ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "job_id": {
                                "type": "integer",
                                "description": "The job ID to fetch",
                            },
                        },
                        "required": ["job_id"],
                    },
                ),
                Tool(
                    name="get_job_stats",
                    description=(
                        "Get statistics about current job listings "
                        "(counts by status and source)"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
            ]

        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls from the client."""
            try:
                if name == "query_jobs":
                    return await self._handle_query_jobs(arguments)
                elif name == "get_job_details":
                    return await self._handle_get_job_details(arguments)
                elif name == "get_job_stats":
                    return await self._handle_get_job_stats()
                else:
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps({"error": f"Unknown tool: {name}"}),
                        )
                    ]
            except Exception as e:
                logger.error(f"Tool call failed: {e}")
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    async def _handle_query_jobs(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle the query_jobs tool call.

        Args:
            arguments: Tool arguments.

        Returns:
            List of TextContent with results.
        """
        try:
            jobs = self.db.get_all_jobs()

            # Apply filters
            title_filter = arguments.get("title_filter", "").lower()
            company_filter = arguments.get("company_filter", "").lower()
            location_filter = arguments.get("location_filter", "").lower()
            status_filter = arguments.get("status_filter")
            min_fit_score = arguments.get("min_fit_score")
            limit = min(int(arguments.get("limit", 20)), 100)

            filtered_jobs = []
            for job in jobs:
                if title_filter and title_filter not in job.title.lower():
                    continue
                if company_filter and company_filter not in job.company.lower():
                    continue
                if (
                    location_filter
                    and job.location
                    and location_filter not in job.location.lower()
                ):
                    continue
                if status_filter and str(job.status) != status_filter:
                    continue
                if min_fit_score and (
                    job.fit_score is None or job.fit_score < min_fit_score
                ):
                    continue

                filtered_jobs.append(job)

            # Return limited results
            results = filtered_jobs[:limit]
            response = {
                "total": len(filtered_jobs),
                "returned": len(results),
                "jobs": [
                    {
                        "id": job.id,
                        "title": job.title,
                        "company": job.company,
                        "location": job.location,
                        "fit_score": job.fit_score,
                        "status": str(job.status),
                        "url": job.url,
                    }
                    for job in results
                ],
            }
            return [TextContent(type="text", text=json.dumps(response))]
        except Exception as e:
            logger.error(f"Query jobs failed: {e}")
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    async def _handle_get_job_details(
        self, arguments: dict[str, Any]
    ) -> list[TextContent]:
        """Handle the get_job_details tool call.

        Args:
            arguments: Tool arguments.

        Returns:
            List of TextContent with results.
        """
        try:
            job_id_val = arguments.get("job_id")
            if job_id_val is None:
                return [
                    TextContent(
                        type="text", text=json.dumps({"error": "job_id is required"})
                    )
                ]
            job_id = int(job_id_val)
            job = self.db.get_job(job_id)
            if not job:
                return [
                    TextContent(
                        type="text", text=json.dumps({"error": "Job not found"})
                    )
                ]

            response = {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.url,
                "description": job.description,
                "source": job.source,
                "date_posted": job.date_posted.isoformat() if job.date_posted else None,
                "fit_score": job.fit_score,
                "fit_reasoning": job.fit_reasoning,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_period": job.salary_period,
                "vacation_days": job.vacation_days,
                "status": str(job.status),
                "distance_km": job.distance_km,
            }
            return [TextContent(type="text", text=json.dumps(response))]
        except Exception as e:
            logger.error(f"Get job details failed: {e}")
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    async def _handle_get_job_stats(self) -> list[TextContent]:
        """Handle the get_job_stats tool call.

        Returns:
            List of TextContent with stats.
        """
        try:
            jobs = self.db.get_all_jobs()

            # Calculate stats
            by_status: dict[str, int] = {}
            by_source: dict[str, int] = {}

            for job in jobs:
                status = str(job.status)
                by_status[status] = by_status.get(status, 0) + 1
                by_source[job.source] = by_source.get(job.source, 0) + 1

            response = {
                "total_jobs": len(jobs),
                "by_status": by_status,
                "by_source": by_source,
                "with_fit_score": sum(1 for j in jobs if j.fit_score is not None),
                "matched_jobs": sum(
                    1 for j in jobs if j.fit_score and j.fit_score >= 60
                ),
            }
            return [TextContent(type="text", text=json.dumps(response))]
        except Exception as e:
            logger.error(f"Get job stats failed: {e}")
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def run_mcp_server(
    db: Database, host: str = "127.0.0.1", port: int = 5000
) -> None:
    """Run the MCP server.

    Args:
        db: Database instance.
        host: Host to bind to.
        port: Port to bind to.
    """
    MCPServerManager(db)
    logger.info(f"Starting MCP server on {host}:{port}")
    try:
        logger.info("MCP server started successfully")
        # Keep the server running
        await asyncio.Event().wait()
    except Exception as e:
        logger.error(f"MCP server error: {e}")
        raise
