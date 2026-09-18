"""The starter CVs, in English and Dutch.

Both languages are produced by :func:`_build` from a single structure, so the two
versions cannot drift apart: section order, section kinds, icons, entry counts and
theme all come from one place, and only the strings in :class:`Strings` differ.
:func:`job_scout.cv.sample.sample_cv` picks a language; ``tests/test_sample.py``
asserts the two stay structurally identical.

Adding a language means adding one more :class:`Strings` instance - the type
checker then requires every field to be translated.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path

from job_scout.cv.models import (
    ContactItem,
    ContactSection,
    CVDocument,
    DetailItem,
    DetailsSection,
    EducationEntry,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
    SkillItem,
    SkillsSection,
    TextSection,
    Theme,
)

# Content that is identical in every language: proper nouns and product names.
EMAIL = "sam.devries@example.com"
LINKEDIN_URL = "https://nl.linkedin.com/in/sam-de-vries-example"
LINKEDIN_TEXT = "in/sam-de-vries"
PHONE = "+31 (0)6 1234 5678"
SKILLS = ("Python", "SQL", "MATLAB", "LabVIEW", "C++")
CERTIFICATIONS = (("Learn Python 3", "2022"), ("PostgreSQL databases", "2022"))


@dataclass(frozen=True)
class Role:
    """One work-experience entry."""

    title: str
    organisation: str
    period: str
    description: str
    bullets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Study:
    """One education entry."""

    degree: str
    school: str
    period: str
    courses: str
    note: str = ""


@dataclass(frozen=True)
class Strings:
    """Every translatable string in the CV.

    Because this is a frozen dataclass with no defaults on the content fields, a
    new language will not type-check until all of it is translated.
    """

    language: str
    headline: str

    profile_title: str
    profile_body: str

    work_title: str
    description_label: str
    roles: tuple[Role, ...]

    education_title: str
    school_label: str
    period_label: str
    courses_label: str
    studies: tuple[Study, ...]

    contact_title: str
    programming_title: str
    languages_title: str
    language_rows: tuple[tuple[str, str], ...]
    certifications_title: str
    personal_title: str
    personal_rows: tuple[tuple[str, str], ...]


ENGLISH = Strings(
    language="EN",
    headline="Approval Expert at Deltameet",
    profile_title="Profile and interests",
    profile_body=(
        "I have always wanted to know how things work and why, which is what led "
        "me to Applied Physics. What I enjoy most is the hands-on side of it: "
        "working with measurement equipment, devising measurement setups and "
        "solving the problem in front of me. Alongside my assessment work I lead "
        "automation projects, from self-hosted AI for document evaluation to "
        "computer vision for test images, and I am looking to make that process "
        "improvement work my main focus."
    ),
    work_title="Work experience",
    # The original template printed "Job description:" above every entry. It is
    # five lines of chrome that say nothing the job title does not already imply,
    # and dropping it is what lets both languages stay on one page. Type it back
    # into the section's "Description label" field to restore it.
    description_label="",
    roles=(
        Role(
            title="Approval expert",
            organisation="Deltameet Institute - Mobility team",
            period="Aug 2024 - present",
            description=(
                "Type approval of measuring instruments: assessing technical "
                "documentation, test results and conformity against national and "
                "international metrology legislation and standards. Leading "
                "automation projects alongside the assessment work."
            ),
            bullets=(
                "Leading development of a self-hosted AI application that largely "
                "automates evaluation of software documentation against the "
                "applicable guidance.",
                "Built a computer vision application for automatic detection in "
                "test images, so tests can run unattended.",
                "Automating test setups and administrative tasks for the team.",
            ),
        ),
        Role(
            title="Photonics sales engineer",
            organisation="Polderlicht Photonics B.V.",
            period="Mar 2021 - Jul 2024",
            description=(
                "Technical sales and consultancy in photonics with a focus on "
                "light metrology: advising on new customer projects, specifying "
                "fully custom solutions, delivering training and carrying out "
                "on-site installations. Products such as integrating spheres, SWIR "
                "cameras, optical tables, spectrophotogoniometers and laser safety."
            ),
        ),
        Role(
            title="Application engineer",
            organisation="Noordzee Aerospace Centre",
            period="Nov 2019 - Feb 2020",
            description=(
                "Conducting latency tests and optimising time critical measurement "
                "and transmitting equipment."
            ),
        ),
        Role(
            title="Graduation student, nanophotochemistry",
            organisation="Kepler Institute for Nanoscience",
            period="Feb 2018 - Jun 2018",
            description=(
                "Pulse length measurements on ultrafast UVC femtosecond laser "
                "pulses, including devising the complete measurement setup."
            ),
        ),
        Role(
            title="Intern, programming and control engineering",
            organisation="Noordzee Aerospace Centre",
            period="Feb 2017 - Jun 2017",
            description=(
                "Renewing an outdated automated measurement setup at the flight "
                "test department, replacing measurement equipment "
                "and rewriting the software."
            ),
        ),
    ),
    education_title="Education",
    school_label="School:",
    period_label="Period:",
    courses_label="Main courses:",
    studies=(
        Study(
            degree="Pre-master Applied Physics",
            school="Noordwijk University of Technology",
            period="2018 - 2019",
            courses=(
                "Photonics, Optics, Measurement and Control Technology, "
                "Electronics, Signal Processing and Laser Technology."
            ),
            note=(
                "Continued into the Master's programme in 2020 (Nanotechnology, "
                "Optics for Lithography, Advanced Photonics) and left to pursue "
                "hands-on work."
            ),
        ),
        Study(
            degree="BASc Applied Physics",
            school="Rijnmond University of Applied Sciences",
            period="2014 - 2018",
            courses=(
                "Electronics, Electromagnetism, Quantum Mechanics, "
                "Thermodynamics, Optics, Atomic Physics, Nuclear & Particle "
                "Physics and Measurement & Control."
            ),
            note="Member of study association Archimedes.",
        ),
    ),
    contact_title="Contact",
    programming_title="Programming",
    languages_title="Languages",
    language_rows=(("Dutch", "Native"), ("English", "Professional")),
    certifications_title="Certifications",
    personal_title="Personal information",
    personal_rows=(
        ("Residence", "Utrecht"),
        ("Date of birth", "01-01-1990"),
        ("Nationality", "Dutch"),
        ("Driving licence", "B & AM"),
    ),
)


DUTCH = Strings(
    language="NL",
    headline="Approval Expert bij Deltameet",
    profile_title="Profiel en interessegebied",
    profile_body=(
        "Ik heb altijd al willen weten hoe dingen werken en waarom, en dat heeft "
        "mij bij de Technische Natuurkunde gebracht. Wat ik het leukst vind is de "
        "hands-on kant ervan: werken met meetapparatuur, meetopstellingen "
        "bedenken en het probleem oplossen dat voor me ligt. Naast mijn "
        "beoordelingswerk leid ik automatiseringsprojecten, van self-hosted AI "
        "voor documentevaluatie tot computer vision voor testbeelden, en ik wil "
        "mij graag volledig op dat procesverbeteringswerk gaan richten."
    ),
    work_title="Werkervaring",
    description_label="",
    roles=(
        Role(
            title="Approval expert",
            organisation="Deltameet Institute - Mobility team",
            period="aug 2024 - heden",
            description=(
                "Typegoedkeuring van meetinstrumenten: het beoordelen van "
                "technische documentatie, testresultaten en conformiteit aan "
                "nationale en internationale wetgeving en normen op het gebied van "
                "metrologie. Daarnaast het leiden van automatiseringsprojecten."
            ),
            bullets=(
                "Leiden van de ontwikkeling van een self-hosted AI applicatie die "
                "de evaluatie van softwaredocumentatie volgens de geldende "
                "richtlijn grotendeels automatiseert.",
                "Een computer vision applicatie gebouwd voor automatische "
                "detectie in testbeelden, zodat testen onbemand kunnen draaien.",
                "Automatiseren van testopstellingen en administratietaken voor "
                "het team.",
            ),
        ),
        Role(
            title="Photonics sales engineer",
            organisation="Polderlicht Photonics B.V.",
            period="mrt 2021 - jul 2024",
            description=(
                "Technische verkoop en advies op het gebied van fotonica met "
                "focus op lichtmetrologie: adviseren over nieuwe klantprojecten, "
                "volledig custom oplossingen specificeren, trainingen geven en "
                "on-site installaties uitvoeren. Producten zoals integrating "
                "spheres, SWIR camera's, optische tafels, spectrophotogoniometers "
                "en laserveiligheid."
            ),
        ),
        Role(
            title="Application engineer",
            organisation="Noordzee Lucht- en Ruimtevaartcentrum",
            period="nov 2019 - feb 2020",
            description=(
                "Het uitvoeren van latency testen en optimalisatie aan "
                "tijdkritische meet- en zendapparatuur."
            ),
        ),
        Role(
            title="Afstudeerstagiair nanophotochemistry",
            organisation="Kepler Institute for Nanoscience",
            period="feb 2018 - jun 2018",
            description=(
                "Pulslengtemetingen aan ultrakorte UVC femtoseconde laserpulsen, "
                "waaronder het bedenken en opstellen van de volledige "
                "meetopstelling."
            ),
        ),
        Role(
            title="Stagiair programmeren/meet- en regeltechniek",
            organisation="Noordzee Lucht- en Ruimtevaartcentrum",
            period="feb 2017 - jun 2017",
            description=(
                "Het vernieuwen van een verouderde geautomatiseerde "
                "meetopstelling bij de afdeling vluchttesten, "
                "waarbij meetapparatuur is vervangen en de software is herschreven."
            ),
        ),
    ),
    education_title="Opleidingen",
    school_label="School:",
    period_label="Periode:",
    courses_label="Belangrijkste vakken:",
    studies=(
        Study(
            degree="Pre-master Technische Natuurkunde",
            school="Technische Universiteit Noordwijk",
            period="2018 - 2019",
            courses=(
                "Fotonica, Optica, Meet- en Regeltechniek, Elektronica, "
                "Signaalbewerking en Lasertechniek."
            ),
            note=(
                "In 2020 doorgestroomd naar de master (Nanotechnologie, Optics "
                "for Lithography, Advanced Photonics) en gestopt om hands-on werk "
                "te gaan doen."
            ),
        ),
        Study(
            degree="HBO Technische Natuurkunde",
            school="Hogeschool Rijnmond",
            period="2014 - 2018",
            courses=(
                "Elektronica, Elektromagnetisme, Quantummechanica, "
                "Thermodynamica, Optica, Atoomfysica, Kern- en Deeltjesfysica en "
                "Meet- en Regeltechniek."
            ),
            note="Lid van studievereniging Archimedes.",
        ),
    ),
    contact_title="Contact",
    programming_title="Programmeren",
    languages_title="Talen",
    language_rows=(("Nederlands", "Moedertaal"), ("Engels", "Professioneel")),
    certifications_title="Certificeringen",
    personal_title="Persoonlijke gegevens",
    personal_rows=(
        ("Woonplaats", "Utrecht"),
        ("Geboortedatum", "1 januari 1990"),
        ("Nationaliteit", "Nederlandse"),
        ("Rijbewijs", "B & AM"),
    ),
)


LANGUAGES: dict[str, Strings] = {ENGLISH.language: ENGLISH, DUTCH.language: DUTCH}

# The example's person. Kept as a constant so is_untouched_example can
# recognise profiles that earlier versions seeded with it.
EXAMPLE_NAME = "Sam de Vries"
_SKILLS_TITLE = {"EN": "Skills", "NL": "Vaardigheden"}


def sample_portrait() -> Path:
    """Return the path to the bundled sample portrait.

    Returns:
        Path to ``job_scout/cv/assets/sample/portrait.png``.
    """
    root = Path(str(resources.files("job_scout.cv")))
    return root / "assets" / "sample" / "portrait.png"


def _build(text: Strings) -> CVDocument:
    """Assemble the CV for one language.

    Args:
        text: The translated strings.

    Returns:
        A fully populated document.
    """
    return CVDocument(
        full_name=EXAMPLE_NAME,
        headline=text.headline,
        language=text.language,
        # Five roles plus certifications no longer fit at the 9pt default, so the
        # starter profiles ship a slightly tighter setting. Sized so the wordier
        # Dutch version still leaves headroom - at 1.26 it filled 99.6% of the
        # column and the next edit would have spilled onto a second page.
        theme=Theme(base_font_size=8.6, line_spacing=1.22),
        photo="portrait.png",
        sidebar=[
            ContactSection(
                title=text.contact_title,
                items=[
                    ContactItem(icon="envelope", value=EMAIL, url=f"mailto:{EMAIL}"),
                    ContactItem(icon="linkedin", value=LINKEDIN_TEXT, url=LINKEDIN_URL),
                    ContactItem(icon="phone", value=PHONE),
                ],
            ),
            SkillsSection(
                title=text.programming_title,
                columns=2,
                items=[SkillItem(name=name) for name in SKILLS],
            ),
            DetailsSection(
                title=text.languages_title,
                label_suffix="",
                items=[
                    DetailItem(label=label, value=value)
                    for label, value in text.language_rows
                ],
            ),
            DetailsSection(
                title=text.certifications_title,
                label_suffix="",
                items=[
                    DetailItem(label=label, value=value)
                    for label, value in CERTIFICATIONS
                ],
            ),
            DetailsSection(
                title=text.personal_title,
                items=[
                    DetailItem(label=label, value=value)
                    for label, value in text.personal_rows
                ],
            ),
        ],
        main=[
            TextSection(
                title=text.profile_title, icon="person", body=text.profile_body
            ),
            ExperienceSection(
                title=text.work_title,
                icon="laptop",
                description_label=text.description_label,
                entries=[
                    ExperienceEntry(
                        title=role.title,
                        organisation=role.organisation,
                        period=role.period,
                        description=role.description,
                        bullets=list(role.bullets),
                    )
                    for role in text.roles
                ],
            ),
            EducationSection(
                title=text.education_title,
                icon="graduation",
                school_label=text.school_label,
                period_label=text.period_label,
                courses_label=text.courses_label,
                entries=[
                    EducationEntry(
                        degree=study.degree,
                        school=study.school,
                        period=study.period,
                        courses=study.courses,
                        note=study.note,
                    )
                    for study in text.studies
                ],
            ),
        ],
    )


def sample_cv(language: str = "EN") -> CVDocument:
    """Build the starter CV in the requested language.

    Args:
        language: Language tag, ``"EN"`` or ``"NL"``. Unknown tags fall back to
            English rather than raising, so a stale caller still gets a document.

    Returns:
        A fully populated :class:`~job_scout.cv.models.CVDocument`.
    """
    return _build(LANGUAGES.get(language.upper(), ENGLISH))


def blank_cv(language: str = "EN") -> CVDocument:
    """Build an empty CV with the standard section skeleton.

    Nothing in it is content. The preview shows placeholder text in the empty
    fields (see :func:`job_scout.cv.placeholders.with_placeholders`) and that
    text is never saved, so a blank profile can never be mistaken for anyone's
    career.

    Args:
        language: Language tag; the section titles follow it. Unknown tags fall
            back to English.

    Returns:
        A :class:`~job_scout.cv.models.CVDocument` with titled but empty sections.
    """
    text = LANGUAGES.get(language.upper(), ENGLISH)
    return CVDocument(
        full_name="",
        language=text.language,
        sidebar=[
            ContactSection(title=text.contact_title, items=[ContactItem()]),
            SkillsSection(title=_SKILLS_TITLE[text.language], items=[SkillItem()]),
            DetailsSection(title=text.personal_title, items=[DetailItem()]),
        ],
        main=[
            TextSection(title=text.profile_title, icon="person"),
            ExperienceSection(
                title=text.work_title,
                icon="laptop",
                entries=[ExperienceEntry()],
                description_label=text.description_label,
            ),
            EducationSection(
                title=text.education_title,
                icon="graduation",
                entries=[EducationEntry()],
                school_label=text.school_label,
                period_label=text.period_label,
                courses_label=text.courses_label,
            ),
        ],
    )


def is_untouched_example(doc: CVDocument) -> bool:
    """Tell whether a profile is still the example CV nobody started editing.

    Earlier versions seeded every new user with the example, saved like a real
    CV. A profile counts as untouched only while it carries the example's name
    and every employer and school in it is the example's, so a profile someone
    began to make their own is never mistaken for one.

    Args:
        doc: A stored CV.

    Returns:
        True for an unedited example CV.
    """
    if doc.full_name.strip() != EXAMPLE_NAME:
        return False
    names = [
        value.strip()
        for section in doc.all_sections()
        for entry in getattr(section, "entries", None) or []
        for value in (
            getattr(entry, "organisation", "") or "",
            getattr(entry, "school", "") or "",
        )
        if value.strip()
    ]
    sample = example_organisations()
    return bool(names) and all(name.casefold() in sample for name in names)


@cache
def example_organisations() -> frozenset[str]:
    """Return every employer and school named in the example CV, in any language.

    Read from the example itself rather than kept as a second list, so the check
    cannot drift out of step when the example changes.

    Returns:
        The example CV's organisations and schools, case-folded.
    """
    names: set[str] = set()
    for strings in LANGUAGES.values():
        for section in _build(strings).all_sections():
            for entry in getattr(section, "entries", None) or []:
                for field in ("organisation", "school"):
                    value = (getattr(entry, field, "") or "").strip()
                    if value:
                        names.add(value.casefold())
    return frozenset(names)


def example_content(doc: CVDocument) -> list[str]:
    """Name the example-CV employers and schools still present in a CV.

    CV Builder seeds a new profile with the example so the editor is not empty,
    and that seed is saved like any other profile. Anything reading stored CVs
    cannot tell a real one from an untouched example unless it looks: without
    this, an applicant who never opened CV Builder got letters and interview
    answers about a fictional person's career.

    Args:
        doc: A stored CV.

    Returns:
        The example organisations it still contains; empty for a real CV.
    """
    sample = example_organisations()
    found: list[str] = []
    for section in doc.all_sections():
        for entry in getattr(section, "entries", None) or []:
            for field in ("organisation", "school"):
                value = (getattr(entry, field, "") or "").strip()
                if value and value.casefold() in sample and value not in found:
                    found.append(value)
    return found


def has_career_content(doc: CVDocument) -> bool:
    """Tell whether a CV says anything about the person's work or education.

    A blank profile is saved with empty entries and an empty profile text, which
    carry no facts at all. Contact and personal details do not count: a name and
    a phone number say nothing about a career.

    Args:
        doc: A stored CV.

    Returns:
        True when any factual section has been filled in.
    """
    for section in doc.all_sections():
        if not section.enabled or section.kind in {"contact", "details"}:
            continue
        if (getattr(section, "body", "") or "").strip():
            return True
        for entry in getattr(section, "entries", None) or []:
            values = entry.model_dump(exclude={"id"}).values()
            texts = [v for v in values if isinstance(v, str)]
            texts += [b for v in values if isinstance(v, list) for b in v]
            if any(str(text).strip() for text in texts):
                return True
        for item in getattr(section, "items", None) or []:
            if (item if isinstance(item, str) else getattr(item, "name", "")).strip():
                return True
    return False
