"""Job scraping from Indeed.nl, LinkedIn, Nationalevacaturebank.nl, and custom sites."""

from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import requests
from loguru import logger

from job_scout.models import Config, ExtractedJob, ExtractedJobs, JobListing

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient


def scrape_all_jobs(
    config: Config, client: LLMClient | None = None
) -> list[JobListing]:
    """Scrape job listings from all configured sources in parallel.

    Args:
        config: Application configuration with keywords and limits.
        client: LLM client for custom-site extraction (optional).

    Returns:
        Deduplicated list of JobListing instances.
    """
    keywords = config.keywords_dutch + config.keywords_english
    if not keywords:
        logger.warning("No keywords configured. Run 'job-scout keywords refresh'.")
        keywords = ["software developer", "data analyst"]

    all_jobs: list[JobListing] = []

    # Prepare scraping tasks: (scraper_func, args_tuple)
    tasks: list[tuple[Any, Any]] = []

    # Add jobspy scraping tasks (limit based on jobspy_keyword_limit)
    for kw in keywords[: config.jobspy_keyword_limit]:
        tasks.append((_scrape_jobspy_with_rate_limit, (kw, config)))

    # Add nvb scraping tasks (limit based on nvb_keyword_limit)
    for kw in config.keywords_dutch[: config.nvb_keyword_limit]:
        tasks.append((_scrape_nvb_with_rate_limit, (kw, config.max_jobs_per_source)))

    # Add custom sites scraping tasks
    if client is not None:
        for site in config.custom_sites:
            if site.enabled:
                tasks.append(
                    (_scrape_custom_site_with_rate_limit, (site, config, client))
                )

    # Run all scraping tasks in parallel with bounded thread pool
    max_workers = min(len(tasks), config.max_parallel_evaluations)
    if max_workers > 0:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all scraping tasks
            futures = [executor.submit(func, *args) for func, args in tasks]
            # Process results as they complete
            for future in as_completed(futures):
                jobs = future.result()
                all_jobs.extend(jobs)

    return _deduplicate(all_jobs)


def _scrape_jobspy_with_rate_limit(keyword: str, config: Config) -> list[JobListing]:
    """Scrape jobspy with rate limiting applied afterward.

    Args:
        keyword: Search term.
        config: Application configuration.

    Returns:
        List of JobListing instances.
    """
    jobs = _scrape_jobspy(keyword, config)
    time.sleep(random.uniform(2, 5))
    return jobs


def _scrape_nvb_with_rate_limit(keyword: str, max_results: int) -> list[JobListing]:
    """Scrape NVB with rate limiting applied afterward.

    Args:
        keyword: Dutch search term.
        max_results: Maximum number of listings to return.

    Returns:
        List of JobListing instances.
    """
    jobs = _scrape_nvb(keyword, max_results)
    time.sleep(random.uniform(2, 5))
    return jobs


def _scrape_custom_site_with_rate_limit(
    site: Any, config: Config, client: LLMClient
) -> list[JobListing]:
    """Scrape custom site with rate limiting applied afterward.

    Args:
        site: CustomSite instance with name and url.
        config: Application configuration.
        client: LLM client to use for extraction.

    Returns:
        List of JobListing instances.
    """
    jobs = _scrape_custom_site(site, config, client)
    time.sleep(random.uniform(1, 3))
    return jobs


def _normalize_text(text: str) -> str:
    """Normalize text for deduplication.

    Handles whitespace, case, and special character variations to catch
    subtle differences that represent the same job/company.

    Args:
        text: Text to normalize.

    Returns:
        Normalized text (lowercase, trimmed, whitespace collapsed).
    """
    # Convert to lowercase and strip leading/trailing whitespace
    normalized = text.lower().strip()
    # Collapse multiple spaces into single space
    normalized = " ".join(normalized.split())
    return normalized


def _deduplicate(jobs: list[JobListing]) -> list[JobListing]:
    """Remove duplicate jobs within the current batch by URL and title+company.

    Args:
        jobs: Possibly-duplicate list of job listings.

    Returns:
        List with duplicate URLs or normalized title+company removed (first seen wins).
    """
    seen_urls: set[str] = set()
    seen_title_company: set[tuple[str, str]] = set()
    unique: list[JobListing] = []
    for job in jobs:
        # Check if we've seen this URL before
        if job.url in seen_urls:
            continue
        # Check if we've seen this normalized title+company combination before
        title_norm = _normalize_text(job.title)
        company_norm = _normalize_text(job.company)
        title_company_key = (title_norm, company_norm)
        if title_company_key in seen_title_company:
            continue
        # First occurrence of this job - keep it
        seen_urls.add(job.url)
        seen_title_company.add(title_company_key)
        unique.append(job)
    logger.info(f"Scraped {len(jobs)} total, {len(unique)} unique after dedup")
    return unique


class _TextExtractor(HTMLParser):
    """Minimal HTML parser that collects visible text and anchor hrefs."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_tags = {"script", "style", "noscript", "head"}
        self._current_skip = 0
        self.text_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._current_skip += 1
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val:
                    self.links.append(val)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._current_skip:
            self._current_skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._current_skip:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)


def _html_to_text_and_links(html: str) -> tuple[str, list[str]]:
    """Extract visible text and link hrefs from HTML using stdlib parser.

    Args:
        html: Raw HTML content.

    Returns:
        Tuple of (visible_text, list_of_href_strings).
    """
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.text_parts), parser.links


def _scrape_custom_site(
    site: Any,
    config: Config,
    client: LLMClient,
) -> list[JobListing]:
    """Fetch a custom site URL and extract job listings via LLM.

    Args:
        site: CustomSite instance with name and url.
        config: Application configuration (unused, reserved for future options).
        client: LLM client to use for extraction.

    Returns:
        List of JobListing instances, or empty list on any error.
    """
    logger.info(f"[custom:{site.name}] Fetching {site.url}")
    try:
        resp = requests.get(
            site.url,
            headers={"User-Agent": "job-scout/0.1 (job search tool)"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"[custom:{site.name}] Fetch failed: {exc}")
        return []

    text, links = _html_to_text_and_links(resp.text)
    text_budget = text[:4000]
    links_sample = links[:100]

    prompt = (
        f"The following is the visible text of a company careers page at {site.url}.\n"
        "Extract all job postings you can find. Respond ONLY with valid JSON.\n\n"
        f"PAGE TEXT:\n{text_budget}\n\n"
        f"LINKS ON PAGE (sample):\n{json.dumps(links_sample)}\n\n"
        'Respond with: {"jobs": [{"title": "...", "company": "", '
        '"location": null, "url": "...", "description": null}, ...]}\n'
        "Use absolute URLs where possible; relative URLs are also accepted.\n"
        'Return {"jobs": []} if no job listings are found.'
    )

    try:
        output = client.complete(prompt, purpose="evaluation", timeout=60)
        data = _parse_extraction_json(output)
        extracted = ExtractedJobs.model_validate(data)
    except Exception as exc:
        logger.warning(f"[custom:{site.name}] Extraction failed: {exc}")
        return []

    jobs = [_extracted_to_job_listing(ej, site.name, site.url) for ej in extracted.jobs]
    logger.info(f"[custom:{site.name}] Extracted {len(jobs)} jobs")
    return jobs


def _parse_extraction_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output, stripping markdown fences.

    Args:
        text: Raw LLM response text.

    Returns:
        Parsed dictionary.
    """
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start != -1 and brace_end > brace_start:
        return json.loads(text[brace_start:brace_end])  # type: ignore[no-any-return]
    return json.loads(text)  # type: ignore[no-any-return]


def _extracted_to_job_listing(
    ej: ExtractedJob, source: str, base_url: str
) -> JobListing:
    """Convert an ExtractedJob to a JobListing, resolving relative URLs.

    Args:
        ej: Extracted job from LLM output.
        source: Source name for the job listing.
        base_url: Base URL of the custom site for resolving relative links.

    Returns:
        JobListing instance.
    """
    parsed = urlparse(ej.url)
    url = ej.url if parsed.scheme else urljoin(base_url, ej.url)
    return JobListing(
        title=ej.title,
        company=ej.company or _host_from_url(base_url),
        location=ej.location,
        url=url,
        description=ej.description,
        source=source,
        seen_at=datetime.now(UTC),
    )


def _host_from_url(url: str) -> str:
    """Return the hostname portion of a URL as a fallback company name.

    Args:
        url: Any URL string.

    Returns:
        Hostname or the original string on parse failure.
    """
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def _normalize_date(val: Any) -> datetime | None:
    """Convert various date representations to datetime.

    Args:
        val: Raw date value from python-jobspy (Timestamp, date, datetime, or None).

    Returns:
        datetime instance, or None if conversion fails.
    """
    if val is None:
        return None
    try:
        import pandas as pd

        if pd.isna(val):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if hasattr(val, "to_pydatetime"):
        result = val.to_pydatetime()
        return result if isinstance(result, datetime) else None
    return None


def _scrape_jobspy(keyword: str, config: Config) -> list[JobListing]:
    """Scrape job sites via python-jobspy using configured sources.

    Args:
        keyword: Search term.
        config: Application configuration.

    Returns:
        List of JobListing instances.
    """
    try:
        import pandas as pd
        from jobspy import scrape_jobs

        logger.info(f"[jobspy] Scraping '{keyword}' from {config.jobspy_sites}")
        df = scrape_jobs(
            site_name=config.jobspy_sites,
            search_term=keyword,
            location="Netherlands",
            results_wanted=min(config.max_jobs_per_source, 50),
            country_indeed="Netherlands",
        )

        jobs: list[JobListing] = []
        for _, row in df.iterrows():
            url = str(row.get("job_url") or "")
            if not url:
                continue
            location = str(row["location"]) if pd.notna(row.get("location")) else None
            desc = str(row["description"]) if pd.notna(row.get("description")) else None
            jobs.append(
                JobListing(
                    title=str(row.get("title") or "Unknown"),
                    company=str(row.get("company") or "Unknown"),
                    location=location,
                    url=url,
                    description=desc,
                    source=str(row.get("site") or "jobspy"),
                    date_posted=_normalize_date(row.get("date_posted")),
                    seen_at=datetime.now(UTC),
                )
            )
        logger.info(f"[jobspy] '{keyword}' → {len(jobs)} jobs")
        return jobs
    except ImportError:
        logger.error("python-jobspy is not installed")
        return []
    except Exception as e:
        logger.warning(f"[jobspy] Scraping failed for '{keyword}': {e}")
        return []


NVB_API_BASE = (
    "https://api.nationalevacaturebank.nl/api/jobs/v3/sites/nationalevacaturebank.nl"
)


def _scrape_nvb(keyword: str, max_results: int = 50) -> list[JobListing]:
    """Fetch jobs from the Nationalevacaturebank.nl JSON API.

    Args:
        keyword: Dutch search term.
        max_results: Maximum number of listings to return.

    Returns:
        List of JobListing instances.
    """
    jobs: list[JobListing] = []
    try:
        resp = requests.get(
            f"{NVB_API_BASE}/jobs",
            params={"query": keyword, "page": "1", "limit": str(max_results)},
            headers={
                "User-Agent": "job-scout/0.1 (job search tool)",
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        for item in data.get("_embedded", {}).get("jobs", []):
            job = _parse_nvb_job(item)
            if job:
                jobs.append(job)
        logger.info(f"[NVB] '{keyword}' → {len(jobs)} jobs")
    except requests.RequestException as e:
        logger.warning(f"[NVB] Request failed for '{keyword}': {e}")
    return jobs


def _parse_nvb_job(item: dict[str, Any]) -> JobListing | None:
    """Parse a single job object from the NVB API response.

    Args:
        item: Job dict from the API's _embedded.jobs array.

    Returns:
        JobListing or None if essential data is missing.
    """
    try:
        detail_url = item.get("_links", {}).get("detail", {}).get("href", "")
        if not detail_url:
            return None

        company_data = item.get("company", {})
        location_data = item.get("workLocation", {})
        location = location_data.get("city") or location_data.get("displayName")

        return JobListing(
            title=item.get("title") or item.get("functionTitle") or "Unknown",
            company=company_data.get("name", "Unknown"),
            location=location,
            url=detail_url,
            description=item.get("description"),
            source="nationalevacaturebank",
            seen_at=datetime.now(UTC),
        )
    except Exception as e:
        logger.debug(f"[NVB] Failed to parse job: {e}")
        return None
