"""LinkedIn profile import and merge into CvProfile."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from io import StringIO
from pathlib import Path
from time import sleep
from typing import Any, cast

from loguru import logger

from job_scout.models import CvProfile, CvRole

_LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
# LinkedIn's non-standard "request blocked" status; outside the 4xx/5xx range
# that requests' raise_for_status() reacts to, so it must be checked by hand.
_LINKEDIN_BLOCKED_STATUS = 999
_FETCH_MAX_ATTEMPTS = 4
_FETCH_RETRY_DELAY = 1.5


class LinkedInProfileImporter:
    """Import LinkedIn profile data and merge into CvProfile."""

    @staticmethod
    def parse_export(export_zip_path: str | Path) -> dict[str, list[Any]]:
        """Parse LinkedIn data export ZIP file.

        LinkedIn's "Get a copy of your data" export provides CSV files.
        Extract profile data from the relevant CSVs (profile, experience).

        Args:
            export_zip_path: Path to the LinkedIn data export ZIP file.

        Returns:
            Dictionary with extracted profile data (skills, education, roles).

        Raises:
            FileNotFoundError: If the ZIP file does not exist.
            ValueError: If required CSV files are not found in the ZIP.
        """
        path = Path(export_zip_path)
        if not path.exists():
            raise FileNotFoundError(f"Export file not found: {export_zip_path}")

        if not zipfile.is_zipfile(path):
            raise ValueError(f"File is not a valid ZIP: {export_zip_path}")

        data: dict[str, list[Any]] = {
            "skills": [],
            "education": [],
            "past_roles": [],
        }

        with zipfile.ZipFile(path, "r") as zf:
            # Try to extract profile info (contains education and skills)
            profile_files = [
                f for f in zf.namelist() if "Profile" in f and f.endswith(".csv")
            ]
            if profile_files:
                _parse_profile_csv(zf.read(profile_files[0]), data)

            # Try to extract experience (contains job history)
            experience_files = [
                f for f in zf.namelist() if "Experience" in f and f.endswith(".csv")
            ]
            if experience_files:
                _parse_experience_csv(zf.read(experience_files[0]), data)

            # Try to extract skills
            skills_files = [
                f for f in zf.namelist() if "Skills" in f and f.endswith(".csv")
            ]
            if skills_files:
                _parse_skills_csv(zf.read(skills_files[0]), data)

        logger.debug(
            f"Parsed LinkedIn export: {len(data['skills'])} skills, "
            f"{len(data['past_roles'])} roles"
        )
        return data

    @staticmethod
    def parse_pasted_text(text: str) -> dict[str, list[Any]]:
        """Parse LinkedIn profile data from plain text or PDF text.

        Attempts to extract structured data from pasted profile text using heuristics.
        Looks for common patterns like job titles, companies, dates, and skills.

        Args:
            text: Plain text extracted from LinkedIn profile or PDF.

        Returns:
            Dictionary with extracted profile data (skills, education, roles).
        """
        data: dict[str, list[Any]] = {
            "skills": [],
            "education": [],
            "past_roles": [],
        }

        lines = text.split("\n")
        in_skills = False
        in_education = False
        in_experience = False

        for i, line in enumerate(lines):
            line_lower = line.lower().strip()

            # Detect sections
            if line_lower.startswith("skill"):
                in_skills = True
                in_education = False
                in_experience = False
                continue
            elif line_lower.startswith("education"):
                in_education = True
                in_skills = False
                in_experience = False
                continue
            elif line_lower in ("experience", "work experience", "employment"):
                in_experience = True
                in_skills = False
                in_education = False
                continue

            line = line.strip()
            if not line:
                continue

            # Extract skills (simple comma-separated or bullet points)
            if in_skills and line and not line.startswith("Education"):
                if "," in line:
                    skills = [s.strip() for s in line.split(",")]
                    data["skills"].extend(skills)
                elif line and len(line) < 50:
                    data["skills"].append(line)

            # Extract education
            elif in_education and line and not line.startswith("Experience"):
                # Simple heuristic: look for institution names
                if (
                    "degree" in line_lower
                    or "university" in line_lower
                    or any(
                        keyword in line_lower
                        for keyword in ["bachelor", "master", "phd"]
                    )
                ):
                    data["education"].append(line)

            # Extract experience
            elif in_experience:
                # Look for role title + company pattern
                role = _try_parse_role_from_text(line, lines, i)
                if role:
                    data["past_roles"].append(role)

        logger.debug(
            f"Parsed pasted text: {len(data.get('skills', []))} skills, "
            f"{len(data.get('past_roles', []))} roles"
        )
        return data

    @staticmethod
    def fetch_profile_url(
        profile_url: str, allow_fetch: bool = False
    ) -> dict[str, list[Any]]:
        """Fetch a LinkedIn profile from a URL (secondary, opt-in path).

        This path makes a direct HTTP request to a LinkedIn profile URL.
        LinkedIn's ToS technically prohibits this even for your own profile,
        so it is gated behind an explicit allow_fetch flag and should only be
        used with clear user acknowledgment of the ToS risk.

        LinkedIn actively masks (asterisks) almost all profile data -- including
        every historical job title -- from anonymous/automated requests. Only
        education history and any public skill tags survive this path; use
        parse_pasted_text or parse_export for actual work-experience import.

        Args:
            profile_url: Full URL to the LinkedIn profile.
            allow_fetch: Must be explicitly True; raises ValueError if False.

        Returns:
            Dictionary with extracted profile data (skills, education; roles
            is always empty -- LinkedIn hides job titles from anonymous
            requests).

        Raises:
            ValueError: If allow_fetch is False.
            RuntimeError: If fetch fails.
        """
        if not allow_fetch:
            raise ValueError(
                "LinkedIn URL fetch is disabled by default due to ToS concerns. "
                "Set linkedin_import_allow_url_fetch=true to enable (at your own risk)."
            )

        # Use a realistic User-Agent to avoid immediate rejection
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        }

        logger.warning(
            f"Fetching LinkedIn profile from {profile_url}. "
            "This may violate LinkedIn's ToS. Proceed at your own risk."
        )

        html = _fetch_profile_html(profile_url, headers)
        data = _extract_profile_from_html(html)
        logger.debug(
            f"Fetched LinkedIn profile (anonymous): {len(data['education'])} "
            f"education, {len(data['skills'])} skills found"
        )
        return data


def _fetch_profile_html(profile_url: str, headers: dict[str, str]) -> str:
    """Fetch profile HTML, retrying past LinkedIn's intermittent bot-blocks.

    LinkedIn answers roughly half of anonymous requests with HTTP 999 (its
    non-standard "blocked" status, which requests' raise_for_status ignores
    because it is outside the 4xx/5xx range), returning a stub page instead of
    the profile. Retrying usually gets through.

    Args:
        profile_url: Full URL to the LinkedIn profile.
        headers: Request headers to send.

    Returns:
        The profile page HTML.

    Raises:
        RuntimeError: If the request fails or every attempt is blocked.
    """
    import requests  # noqa: PLC0415

    last_status: int | None = None
    for attempt in range(_FETCH_MAX_ATTEMPTS):
        try:
            resp = requests.get(profile_url, headers=headers, timeout=10)
        except requests.RequestException as e:
            raise RuntimeError(
                f"Failed to fetch LinkedIn profile: {e}. "
                "LinkedIn may have blocked the request."
            ) from e

        last_status = resp.status_code
        if resp.status_code == _LINKEDIN_BLOCKED_STATUS:
            logger.debug(
                f"LinkedIn returned {_LINKEDIN_BLOCKED_STATUS} (blocked) on attempt "
                f"{attempt + 1}/{_FETCH_MAX_ATTEMPTS}"
            )
            if attempt < _FETCH_MAX_ATTEMPTS - 1:
                sleep(_FETCH_RETRY_DELAY * (attempt + 1))
            continue

        try:
            resp.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(
                f"Failed to fetch LinkedIn profile: {e}. "
                "LinkedIn may have blocked the request."
            ) from e
        return resp.text

    raise RuntimeError(
        f"LinkedIn blocked every fetch attempt (HTTP {last_status}). This is "
        "common for automated requests. Use the paste or data-export import "
        "method instead -- they are more reliable and carry no ToS risk."
    )


def _find_person_ld(html: str) -> dict[str, Any] | None:
    """Extract the schema.org Person block from a profile page's JSON-LD.

    Args:
        html: Raw HTML of the profile page.

    Returns:
        The Person node as a dict, or None if not found/parseable.
    """
    for block in _LD_JSON_RE.findall(html):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        graph = parsed.get("@graph", []) if isinstance(parsed, dict) else []
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "Person":
                return node
    return None


def _is_redacted(value: str) -> bool:
    """Return True for LinkedIn's asterisk-masked placeholder text.

    LinkedIn replaces most profile fields with asterisks for anonymous or
    automated visitors as an anti-scraping measure.

    Args:
        value: Raw field text.

    Returns:
        True when the value is empty or entirely asterisks.
    """
    compact = value.strip().replace(" ", "")
    return not compact or all(c == "*" for c in compact)


def _clean_ld_text(value: object) -> str | None:
    """Return a usable string from a JSON-LD field, or None if missing/redacted.

    Args:
        value: Raw JSON-LD field value.

    Returns:
        Stripped text, or None when absent or redacted.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if not text or _is_redacted(text) else text


def _extract_profile_from_html(html: str) -> dict[str, list[Any]]:
    """Extract whatever public, non-redacted profile data is present.

    Only education and public skill tags are extracted -- LinkedIn hides
    every historical job title from anonymous/automated requests, so
    past_roles is always empty from this path.

    Args:
        html: Raw HTML of the profile page.

    Returns:
        Dictionary with extracted profile data (skills, education, roles).
    """
    data: dict[str, list[Any]] = {"skills": [], "education": [], "past_roles": []}
    person = _find_person_ld(html)
    if not person:
        return data

    for edu in person.get("alumniOf") or []:
        if not isinstance(edu, dict):
            continue
        school = _clean_ld_text(edu.get("name"))
        if not school:
            continue
        raw_member = edu.get("member")
        member: dict[str, Any] = raw_member if isinstance(raw_member, dict) else {}
        start, end = member.get("startDate"), member.get("endDate")
        label = f"{school} ({start or '?'}-{end or '?'})" if (start or end) else school
        data["education"].append(label)

    for skill in person.get("knowsAbout") or []:
        if cleaned := _clean_ld_text(skill):
            data["skills"].append(cleaned)

    if not data["education"] and not data["skills"]:
        logger.warning(
            "LinkedIn anonymous fetch returned no usable data -- LinkedIn hides "
            "most profile data (including all historical job titles) from "
            "automated/anonymous requests. Use parse_pasted_text or parse_export "
            "for full work history."
        )
    return data


def _parse_profile_csv(csv_data: bytes, data: dict[str, list[Any]]) -> None:
    """Extract profile info from LinkedIn's Profile.csv.

    Args:
        csv_data: Raw CSV file content.
        data: Dictionary to populate with extracted data.
    """
    try:
        text = csv_data.decode("utf-8")
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            if not row:
                continue
            # LinkedIn profile CSV has various columns; look for education
            if "Organization" in row and row["Organization"]:
                data["education"].append(row["Organization"])
    except Exception as e:
        logger.warning(f"Failed to parse Profile.csv: {e}")


def _parse_experience_csv(csv_data: bytes, data: dict[str, list[Any]]) -> None:
    """Extract experience (job history) from LinkedIn's Experience.csv.

    Args:
        csv_data: Raw CSV file content.
        data: Dictionary to populate with extracted data.
    """
    try:
        text = csv_data.decode("utf-8")
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            if not row:
                continue
            # LinkedIn Experience CSV has: Title, Company, Started On, Ended On
            title = row.get("Title", "").strip() or row.get("title", "").strip()
            company = row.get("Company", "").strip() or row.get("company", "").strip()
            start_date = (
                row.get("Started On", "").strip() or row.get("started on", "").strip()
            )
            end_date = (
                row.get("Ended On", "").strip() or row.get("ended on", "").strip()
            )

            if title or company:
                role = CvRole(
                    title=title or "Unknown",
                    company=company or "Unknown",
                    start_date=start_date or None,
                    end_date=end_date or None,
                    description=None,
                )
                data["past_roles"].append(role.model_dump())
    except Exception as e:
        logger.warning(f"Failed to parse Experience.csv: {e}")


def _parse_skills_csv(csv_data: bytes, data: dict[str, list[Any]]) -> None:
    """Extract skills from LinkedIn's Skills.csv.

    Args:
        csv_data: Raw CSV file content.
        data: Dictionary to populate with extracted data.
    """
    try:
        text = csv_data.decode("utf-8")
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            if not row:
                continue
            # LinkedIn Skills CSV has: Name, Proficiency, Endorsed
            skill = row.get("Name", "").strip() or row.get("name", "").strip()
            if skill:
                data["skills"].append(skill)
    except Exception as e:
        logger.warning(f"Failed to parse Skills.csv: {e}")


def _try_parse_role_from_text(line: str, lines: list[str], idx: int) -> CvRole | None:
    """Try to extract a role from a single line of text.

    Heuristic: look for patterns like "Title at Company" or "Title, Company".

    Args:
        line: Current line of text.
        lines: Full list of lines (for context).
        idx: Current line index.

    Returns:
        Parsed CvRole if found, None otherwise.
    """
    # Look for "at" or comma separators
    if " at " in line.lower():
        parts = line.split(" at ")
        if len(parts) == 2:
            return CvRole(
                title=parts[0].strip(),
                company=parts[1].strip(),
                start_date=None,
                end_date=None,
                description=None,
            )
    elif "," in line:
        parts = line.split(",", 1)
        if len(parts) == 2 and len(parts[0]) < 50 and len(parts[1]) < 50:
            return CvRole(
                title=parts[0].strip(),
                company=parts[1].strip(),
                start_date=None,
                end_date=None,
                description=None,
            )

    return None


def merge_linkedin_into_profile(
    existing_profile: CvProfile, linkedin_data: dict[str, list[Any]]
) -> tuple[CvProfile, dict[str, list[Any]]]:
    """Merge external profile data into an existing CvProfile, filling gaps only.

    Never overwrites existing data; only adds new skills, education, and roles.
    Returns both the merged profile and a diff of what was added.

    Args:
        existing_profile: Current CvProfile to merge into.
        linkedin_data: Data extracted from LinkedIn, or any other source using
            the same {skills, education, past_roles} shape (e.g. person search).

    Returns:
        Tuple of (merged_profile, diff_dict) where diff_dict shows what was added.
    """
    diff: dict[str, list[Any]] = {
        "added_skills": [],
        "added_education": [],
        "added_roles": [],
    }

    # Merge skills (add new ones not already present)
    existing_skills = {s.lower() for s in existing_profile.skills}
    new_skills: list[str] = []
    for skill in linkedin_data.get("skills", []) or []:
        skill_str = str(skill).strip()
        if skill_str and skill_str.lower() not in existing_skills:
            new_skills.append(skill_str)
            diff["added_skills"].append(skill_str)

    merged_skills = existing_profile.skills + new_skills

    # Merge education (add new ones not already present)
    existing_education = {e.lower() for e in existing_profile.education}
    new_education: list[str] = []
    for edu in linkedin_data.get("education", []) or []:
        edu_str = str(edu).strip()
        if edu_str and edu_str.lower() not in existing_education:
            new_education.append(edu_str)
            diff["added_education"].append(edu_str)

    merged_education = existing_profile.education + new_education

    # Merge roles (add new ones not already present by company+title combo)
    existing_role_keys = {
        (r.company.lower(), r.title.lower()) for r in existing_profile.past_roles
    }
    new_roles: list[CvRole] = []
    for role_data in linkedin_data.get("past_roles", []) or []:
        if isinstance(role_data, dict):
            role = CvRole(**role_data)
        else:
            role = cast(CvRole, role_data)

        role_key = (role.company.lower(), role.title.lower())
        if role_key not in existing_role_keys:
            new_roles.append(role)
            diff["added_roles"].append(
                {
                    "title": role.title,
                    "company": role.company,
                    "start_date": role.start_date,
                    "end_date": role.end_date,
                }
            )

    merged_roles = existing_profile.past_roles + new_roles

    merged_profile = CvProfile(
        skills=merged_skills,
        years_experience=existing_profile.years_experience,
        education=merged_education,
        past_roles=merged_roles,
    )

    return merged_profile, diff


def compute_linkedin_hash(data: dict[str, list[Any]]) -> str:
    """Compute hash of LinkedIn data for caching purposes.

    Args:
        data: Dictionary with LinkedIn profile data.

    Returns:
        Hex string of SHA256 hash.
    """
    json_str = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()
