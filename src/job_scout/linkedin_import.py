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
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from job_scout.evaluator import _extract_json
from job_scout.llm.base import LLMError
from job_scout.models import CurrentRoleConflict, CvProfile, CvRole

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient

# Legal-form and filler tokens dropped when comparing company names.
_COMPANY_NOISE_WORDS = frozenset(
    {
        "bv",
        "b",
        "v",
        "nv",
        "n",
        "cv",
        "c",
        "vof",
        "holding",
        "group",
        "groep",
        "inc",
        "ltd",
        "llc",
        "gmbh",
        "sa",
        "the",
        "de",
        "het",
    }
)

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


def _build_pdf_prompt(text: str) -> str:
    """Build the prompt that structures a LinkedIn PDF export.

    Args:
        text: Text extracted from the "Save to PDF" profile export.

    Returns:
        Complete prompt string.
    """
    return f"""Extract the work history from this LinkedIn profile export.
Respond ONLY with JSON.

The export lists each role as: company name, then job title, then a date range,
then a location. Dates may be in Dutch (e.g. "augustus 2024 - Present",
"maart 2021 - juli 2024"). Convert them to YYYY-MM.

A role that is ongoing ("Present", "Heden", "- heden") MUST have "end_date": null.
Every other role MUST have a real end_date. At most ONE role may be ongoing.

PROFILE EXPORT:
{text[:6000]}

Respond with this exact JSON structure:
{{
  "skills": ["<skill>"],
  "education": ["<degree, institution (years)>"],
  "past_roles": [
    {{"title": "<job title>", "company": "<company>",
      "start_date": "YYYY-MM", "end_date": "YYYY-MM or null",
      "description": null}}
  ]
}}"""


def parse_linkedin_pdf(pdf_path: str | Path, *, client: LLMClient) -> dict[str, Any]:
    """Parse a LinkedIn "Save to PDF" profile export into structured data.

    This is the richest safe import path: unlike an anonymous URL fetch, the
    PDF the account holder downloads of their own profile contains the full
    work history with real job titles and date ranges, so it can correct a CV
    whose current role is out of date.

    Args:
        pdf_path: Path to the downloaded profile PDF.
        client: LLM client used to structure the extracted text.

    Returns:
        Dictionary with skills, education, past_roles, and current_company.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        ValueError: If no text could be extracted from the PDF.
    """
    from job_scout.cv_parser import parse_cv  # noqa: PLC0415

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"LinkedIn PDF not found: {pdf_path}")

    text = parse_cv(path)
    if not text.strip():
        raise ValueError(
            f"No text could be extracted from {pdf_path}. Make sure it is the "
            'PDF from LinkedIn\'s "Save to PDF" option, not a screenshot.'
        )

    data: dict[str, Any] = {
        "skills": [],
        "education": [],
        "past_roles": [],
        "current_company": None,
    }
    try:
        parsed = _extract_json(
            client.complete(_build_pdf_prompt(text), purpose="cv_parsing")
        )
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"LinkedIn PDF parsing failed: {exc}")
        return data

    data["skills"] = [
        str(s).strip() for s in parsed.get("skills") or [] if str(s).strip()
    ]
    data["education"] = [
        str(e).strip() for e in parsed.get("education") or [] if str(e).strip()
    ]
    data["past_roles"] = _roles_from_pdf(parsed.get("past_roles"))
    current = [r for r in data["past_roles"] if not r.get("end_date")]
    data["current_company"] = current[-1]["company"] if current else None
    logger.info(
        f"Parsed LinkedIn PDF: {len(data['past_roles'])} roles, "
        f"current employer={data['current_company']!r}"
    )
    return data


def _roles_from_pdf(value: object) -> list[dict[str, Any]]:
    """Validate roles parsed from a LinkedIn PDF export.

    Guards the "exactly one current role" invariant the export implies: if
    the model marks several roles ongoing, only the most recent is kept open
    so downstream code cannot read two employers as current.

    Args:
        value: Raw "past_roles" value from the LLM response.

    Returns:
        Validated role dicts, oldest first.
    """
    if not isinstance(value, list):
        return []
    roles: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title, company = item.get("title"), item.get("company")
        if not title or not company:
            continue
        roles.append(
            CvRole(
                title=str(title).strip(),
                company=str(company).strip(),
                start_date=_opt(item.get("start_date")),
                end_date=_opt(item.get("end_date")),
                description=_opt(item.get("description")),
            ).model_dump()
        )
    roles.sort(key=lambda r: r.get("start_date") or "")
    open_roles = [r for r in roles if not r["end_date"]]
    for role in open_roles[:-1]:
        logger.debug(f"Closing extra open role: {role['title']} @ {role['company']}")
        role["end_date"] = "unknown"
    return roles


def _opt(value: object) -> str | None:
    """Return a stripped string, or None for empty/null/"null" values.

    Args:
        value: Raw value from the LLM response.

    Returns:
        Stripped string, or None.
    """
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in ("null", "none", "present") else text


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


def _current_employer(person: dict[str, Any]) -> str | None:
    """Return the profile's current employer name, if LinkedIn left it visible.

    LinkedIn masks previous employers and every job title for anonymous
    visitors, but keeps the *current* employer readable (it is also in the
    page title and meta description). That single fact is enough to detect a
    CV whose "current" role is out of date.

    Args:
        person: The schema.org Person node.

    Returns:
        The current employer name, or None if absent or masked.
    """
    for org in person.get("worksFor") or []:
        if isinstance(org, dict) and (name := _clean_ld_text(org.get("name"))):
            return name
    return None


def _extract_profile_from_html(html: str) -> dict[str, Any]:
    """Extract whatever public, non-redacted profile data is present.

    Only education, public skill tags, and the current employer are
    extractable -- LinkedIn hides every historical job title from
    anonymous/automated requests, so past_roles is always empty here.

    Args:
        html: Raw HTML of the profile page.

    Returns:
        Dictionary with skills, education, past_roles, and current_company.
    """
    data: dict[str, Any] = {
        "skills": [],
        "education": [],
        "past_roles": [],
        "current_company": None,
    }
    person = _find_person_ld(html)
    if not person:
        return data

    data["current_company"] = _current_employer(person)

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

    if not any((data["education"], data["skills"], data["current_company"])):
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


def _normalise_company(name: str) -> str:
    """Reduce a company name to a comparable core token set.

    Strips legal suffixes and punctuation so "Laser 2000 Benelux C.V." and
    "Laser 2000 Benelux" compare equal.

    Args:
        name: Raw company name.

    Returns:
        Normalised comparison key.
    """
    lowered = re.sub(r"[^\w\s]", " ", name.lower())
    words = [w for w in lowered.split() if w not in _COMPANY_NOISE_WORDS]
    return " ".join(words)


def _same_company(left: str, right: str) -> bool:
    """Return True when two company names plausibly refer to one employer.

    Args:
        left: First company name.
        right: Second company name.

    Returns:
        True when the normalised names match or one contains the other.
    """
    a, b = _normalise_company(left), _normalise_company(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def detect_stale_current_role(
    profile: CvProfile, current_company: str | None
) -> CurrentRoleConflict | None:
    """Detect a CV whose open-ended role is no longer the person's real job.

    A CV role with no end date is read as "current" everywhere downstream,
    including by the fit evaluator. When a dated CV is uploaded, that role is
    often an old one, which skews matching toward the wrong kind of work.

    Args:
        profile: The parsed CV profile.
        current_company: Employer reported by LinkedIn, if known.

    Returns:
        A CurrentRoleConflict when the CV disagrees with LinkedIn, else None.
    """
    if not current_company:
        return None

    open_roles = [r for r in profile.past_roles if not r.end_date]
    if any(_same_company(r.company, current_company) for r in open_roles):
        return None  # CV already reflects the real employer

    stale = open_roles[-1] if open_roles else None
    return CurrentRoleConflict(
        cv_current_title=stale.title if stale else None,
        cv_current_company=stale.company if stale else None,
        cv_current_since=stale.start_date if stale else None,
        linkedin_current_company=current_company,
    )


def reconcile_current_role(
    profile: CvProfile, current_company: str | None
) -> CvProfile:
    """Ensure only the real current employer has an open-ended role.

    Merging imported history cannot remove a stale role on its own: dedup
    matches on company *and* title, so a CV saying "Photonics Sales Engineer"
    and an import saying "Fotonica Sales Engineer" at the same employer are
    treated as different roles, leaving two jobs looking current at once.

    Args:
        profile: The merged CV profile.
        current_company: The employer the person actually works for now.

    Returns:
        A profile in which at most the current employer's role is open-ended.
    """
    if not current_company:
        return profile

    roles: list[CvRole] = []
    closed = 0
    for role in profile.past_roles:
        is_stale = not role.end_date and not _same_company(
            role.company, current_company
        )
        if is_stale:
            closed += 1
            roles.append(role.model_copy(update={"end_date": "unknown"}))
        else:
            roles.append(role)

    if closed:
        logger.info(f"Closed {closed} stale open-ended role(s) vs {current_company}")
    return profile.model_copy(update={"past_roles": roles})


def apply_current_role_correction(
    profile: CvProfile,
    conflict: CurrentRoleConflict,
    *,
    new_title: str,
    started: str | None = None,
    ended_previous: str | None = None,
) -> CvProfile:
    """Close the stale open-ended role and record the real current one.

    Args:
        profile: The CV profile to correct.
        conflict: The detected conflict identifying the stale role.
        new_title: Job title at the current employer (LinkedIn masks titles,
            so this must be supplied by the user).
        started: Start date at the current employer, if known.
        ended_previous: End date to close the stale role with, if known.

    Returns:
        A new CvProfile with the corrected employment history.
    """
    roles: list[CvRole] = []
    for role in profile.past_roles:
        stale = (
            not role.end_date
            and conflict.cv_current_company is not None
            and _same_company(role.company, conflict.cv_current_company)
        )
        roles.append(
            role.model_copy(update={"end_date": ended_previous or "unknown"})
            if stale
            else role
        )

    roles.append(
        CvRole(
            title=new_title,
            company=conflict.linkedin_current_company,
            start_date=started,
            end_date=None,
            description=None,
        )
    )
    return profile.model_copy(update={"past_roles": roles})


def _find_matching_role(roles: list[CvRole], candidate: CvRole) -> int | None:
    """Locate an existing role describing the same job as *candidate*.

    The same employment shows up worded differently in a CV and a LinkedIn
    export, so an exact title match is too strict. Two roles are the same job
    when they are at the same employer and either start in the same month or
    carry the same title.

    Args:
        roles: Roles already on the profile.
        candidate: Imported role to place.

    Returns:
        Index of the matching role, or None when it is genuinely new.
    """
    for idx, role in enumerate(roles):
        same_start = _same_month(role.start_date, candidate.start_date)
        same_end = _same_month(role.end_date, candidate.end_date)
        same_title = role.title.strip().lower() == candidate.title.strip().lower()

        if _same_company(role.company, candidate.company) and (
            same_start or same_title
        ):
            return idx
        # The same employer is often named differently across sources -- an
        # acronym here, its expansion (sometimes in another language) there.
        # Identical start and end months are a strong enough signal on their
        # own, and a shared distinctive word confirms a matching start.
        if same_start and (same_end or _shares_company_token(role, candidate)):
            logger.debug(
                f"Matched {candidate.title!r} @ {candidate.company!r} to existing "
                f"{role.title!r} @ {role.company!r} on dates"
            )
            return idx
    return None


def _same_month(left: str | None, right: str | None) -> bool:
    """Return True when two dates name the same year and month.

    Args:
        left: First date, "YYYY-MM" or longer.
        right: Second date.

    Returns:
        True when both are present and share a year-month prefix.
    """
    return bool(left and right and left[:7] == right[:7])


def _shares_company_token(left: CvRole, right: CvRole) -> bool:
    """Return True when two company names share a distinctive word.

    Args:
        left: First role.
        right: Second role.

    Returns:
        True when the normalised names share a token of 3+ characters.
    """
    a = {w for w in _normalise_company(left.company).split() if len(w) >= 3}
    b = {w for w in _normalise_company(right.company).split() if len(w) >= 3}
    return bool(a & b)


def _enrich_role(existing: CvRole, imported: CvRole) -> CvRole:
    """Fill gaps in an existing role from an imported duplicate.

    The import is treated as the more current source for employment *dates*
    -- that is the whole point of importing it, since a dated CV leaves an
    old job open-ended. Wording already on the CV is kept.

    Args:
        existing: Role already on the profile.
        imported: The matching imported role.

    Returns:
        The enriched role (unchanged when the import adds nothing).
    """
    updates: dict[str, Any] = {}
    if imported.end_date and existing.end_date != imported.end_date:
        updates["end_date"] = imported.end_date
    if imported.start_date and not existing.start_date:
        updates["start_date"] = imported.start_date
    if imported.description and not existing.description:
        updates["description"] = imported.description
    return existing.model_copy(update=updates) if updates else existing


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
        "updated_roles": [],
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

    # Merge roles. A CV and a LinkedIn export describe the same job in
    # different words ("Stagiair" vs "Intern ... Engineer", "Laser 2000
    # Benelux CV" vs "Laser 2000 Benelux C.V."), so matching titles exactly
    # would import every role a second time.
    merged_roles = list(existing_profile.past_roles)
    for role_data in linkedin_data.get("past_roles", []) or []:
        if isinstance(role_data, dict):
            role = CvRole(**role_data)
        else:
            role = cast(CvRole, role_data)

        match_idx = _find_matching_role(merged_roles, role)
        if match_idx is None:
            merged_roles.append(role)
            diff["added_roles"].append(
                {
                    "title": role.title,
                    "company": role.company,
                    "start_date": role.start_date,
                    "end_date": role.end_date,
                }
            )
            continue

        updated = _enrich_role(merged_roles[match_idx], role)
        if updated != merged_roles[match_idx]:
            merged_roles[match_idx] = updated
            diff["updated_roles"].append(
                {
                    "title": updated.title,
                    "company": updated.company,
                    "start_date": updated.start_date,
                    "end_date": updated.end_date,
                }
            )

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
