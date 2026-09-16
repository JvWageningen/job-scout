"""End-to-end PDF rendering, checked by reading the produced PDF back."""

from __future__ import annotations

import io

import pytest

from job_scout.cv.models import (
    ContactItem,
    ContactSection,
    CVDocument,
    DetailItem,
    DetailsSection,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
    ListSection,
    SkillItem,
    SkillsSection,
    TextSection,
    Theme,
)
from job_scout.cv.render import build_geometry
from job_scout.cv.sample import sample_portrait
from tests.cv.conftest import (
    assert_colour,
    make_image,
    open_pdf,
    page_text,
    render_bytes,
)


def test_sample_renders_a_single_page(cv: CVDocument) -> None:
    payload = render_bytes(cv)
    assert payload.startswith(b"%PDF")
    with open_pdf(payload) as document:
        assert document.page_count == 1


def test_sample_content_survives_the_round_trip(cv: CVDocument) -> None:
    text = page_text(render_bytes(cv))
    assert "PHOTONICS SALES ENGINEER" in text
    assert "Noordwijk University of Technology" in text
    assert "sam.devries@example.com".upper() in text.upper()
    assert "UTRECHT" in text.upper()


def test_body_text_is_not_letter_spaced(cv: CVDocument) -> None:
    """Regression: Tc is graphics state, so a spaced heading used to bleed into
    the body text that followed it, rendering 'i n t e r e s t s'."""
    text = page_text(render_bytes(cv))
    assert "measurement" in text
    assert "m e a s u r e m e n t" not in text


def test_headings_are_letter_spaced(cv: CVDocument) -> None:
    """Section bars use Tc tracking, which PyMuPDF reports as spaced glyphs."""
    text = page_text(render_bytes(cv))
    assert "W O R K" in text
    assert "E D U C A T I O N" in text


def test_detail_labels_get_their_suffix(cv: CVDocument) -> None:
    assert "RESIDENCE:" in page_text(render_bytes(cv)).upper()


def test_pdf_metadata_is_set(cv: CVDocument) -> None:
    with open_pdf(render_bytes(cv)) as document:
        assert document.metadata is not None
        assert "Sam de Vries" in document.metadata["title"]
        assert document.metadata["creator"] == "cv-builder"


def test_sidebar_and_page_backgrounds_use_theme_colours(cv: CVDocument) -> None:
    cv.theme.sidebar_bg = "#123456"
    cv.theme.page_bg = "#FEFEFA"
    with open_pdf(render_bytes(cv)) as document:
        pixmap = document[0].get_pixmap(dpi=72)
        geometry = build_geometry(cv.theme)
        sidebar_x = int(geometry.sidebar_width * 0.5)
        page_x = int(geometry.page_width - 10)
        assert_colour(pixmap.pixel(sidebar_x, pixmap.height - 12), (0x12, 0x34, 0x56))
        assert_colour(pixmap.pixel(page_x, pixmap.height - 12), (0xFE, 0xFE, 0xFA))


def test_sidebar_width_follows_the_theme(cv: CVDocument) -> None:
    cv.theme.sidebar_width_pct = 0.5
    with open_pdf(render_bytes(cv)) as document:
        pixmap = document[0].get_pixmap(dpi=72)
        geometry = build_geometry(cv.theme)
        inside = int(geometry.sidebar_width) - 6
        outside = int(geometry.sidebar_width) + 6
        assert pixmap.pixel(inside, pixmap.height // 2) != pixmap.pixel(
            outside, pixmap.height // 2
        )


def test_long_history_flows_onto_more_pages(cv: CVDocument) -> None:
    section = next(s for s in cv.main if s.kind == "experience")
    assert isinstance(section, ExperienceSection)
    section.entries.extend(
        ExperienceEntry(
            title=f"Role number {index}",
            organisation="A company with a reasonably long name",
            period="2019 - 2020",
            description="A description long enough to take several lines. " * 3,
        )
        for index in range(14)
    )
    payload = render_bytes(cv)
    with open_pdf(payload) as document:
        assert document.page_count >= 2
        pages = [page.get_text().upper() for page in document]
    # The overflow must actually continue onto a later page, not be dropped.
    assert "ROLE NUMBER 13" in "".join(pages)
    assert "ROLE NUMBER 13" not in pages[0]
    # Education still follows the work history rather than being lost: the last
    # qualification in the document is the last thing rendered.
    education = next(s for s in cv.main if s.kind == "education")
    assert isinstance(education, EducationSection)
    assert education.entries[-1].school.upper() in pages[-1]


def test_every_page_gets_the_sidebar_panel(cv: CVDocument) -> None:
    section = next(s for s in cv.main if s.kind == "experience")
    assert isinstance(section, ExperienceSection)
    section.entries.extend(
        ExperienceEntry(title=f"Role {i}", description="text " * 40) for i in range(20)
    )
    with open_pdf(render_bytes(cv)) as document:
        assert document.page_count >= 2
        geometry = build_geometry(cv.theme)
        for number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(dpi=72)
            # Sampling one fixed point is brittle - it lands on a section bar as
            # soon as the sidebar gains content. Assert the panel colour dominates
            # the strip instead.
            raw = cv.theme.sidebar_bg.lstrip("#")
            want = tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))
            hits = total = 0
            for y in range(4, pixmap.height - 4, 4):
                for x in range(4, int(geometry.sidebar_width) - 4, 4):
                    total += 1
                    pixel = pixmap.pixel(x, y)[:3]
                    if all(abs(pixel[i] - want[i]) <= 3 for i in range(3)):
                        hits += 1
            assert hits / total > 0.5, (
                f"page {number}: sidebar panel covers only {hits / total:.0%}"
            )


def test_empty_document_still_renders() -> None:
    payload = render_bytes(CVDocument())
    with open_pdf(payload) as document:
        assert document.page_count == 1


def test_disabled_sections_are_skipped(cv: CVDocument) -> None:
    for section in cv.main:
        if section.kind == "education":
            section.enabled = False
    text = page_text(render_bytes(cv))
    # "EDUCATION" alone would false-match "educations" in a job description.
    assert "Noordwijk University of Technology" not in text
    assert "E D U C A T I O N" not in text
    assert "PHOTONICS SALES ENGINEER" in text


def test_long_name_is_shrunk_to_fit() -> None:
    """A long surname must shrink rather than break across lines mid-word."""
    doc = CVDocument(full_name="Bartholomew Featherstonehaugh")
    text = page_text(render_bytes(doc))
    assert "F E A T H E R S T O N E H A U G H" in text


def test_short_name_keeps_the_full_size() -> None:
    from job_scout.cv.render.document import NAME_SCALE, _fit_name

    theme = Theme()
    style = _fit_name("Jo Ng", theme, 178.0)
    assert style.size == pytest.approx(theme.base_font_size * NAME_SCALE)


def test_extremely_long_name_still_renders() -> None:
    doc = CVDocument(full_name="Wolfeschlegelsteinhausenbergerdorff " * 2)
    with open_pdf(render_bytes(doc)) as document:
        assert document.page_count >= 1


def test_portrait_is_embedded_when_present(cv: CVDocument) -> None:
    portrait = sample_portrait()
    if not portrait.is_file():  # pragma: no cover - asset always ships
        pytest.skip("sample portrait missing")
    with open_pdf(render_bytes(cv, portrait)) as document:
        assert document[0].get_images()


def test_missing_portrait_is_ignored(cv: CVDocument, tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    payload = render_bytes(cv, tmp_path / "nope.png")
    with open_pdf(payload) as document:
        assert document.page_count == 1
        assert not document[0].get_images()


def test_broken_portrait_is_ignored(cv: CVDocument, tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"this is not a png")
    payload = render_bytes(cv, broken)
    with open_pdf(payload) as document:
        assert not document[0].get_images()


def test_show_photo_toggle_removes_the_portrait(cv: CVDocument) -> None:
    cv.theme.show_photo = False
    with open_pdf(render_bytes(cv, sample_portrait())) as document:
        assert not document[0].get_images()


def test_contact_links_become_pdf_annotations() -> None:
    doc = CVDocument(
        full_name="Test",
        sidebar=[
            ContactSection(
                title="Contact",
                items=[
                    ContactItem(
                        icon="envelope",
                        value="a@b.com",
                        url="https://example.com/profile",
                    )
                ],
            )
        ],
    )
    with open_pdf(render_bytes(doc)) as document:
        targets = [link.get("uri") for link in document[0].get_links()]
    assert "https://example.com/profile" in targets


def test_letter_page_size_changes_dimensions(cv: CVDocument) -> None:
    cv.theme.page_size = "LETTER"
    with open_pdf(render_bytes(cv)) as document:
        assert document[0].rect.width == pytest.approx(612, abs=1)
        assert document[0].rect.height == pytest.approx(792, abs=1)


def test_helvetica_fallback_renders(cv: CVDocument) -> None:
    cv.theme.font_family = "Helvetica"
    assert "PHOTONICS SALES ENGINEER" in page_text(render_bytes(cv))


def test_every_section_kind_renders() -> None:
    doc = CVDocument(
        full_name="Kind Coverage",
        sidebar=[
            ContactSection(
                title="Contact", items=[ContactItem(icon="phone", value="123")]
            ),
            SkillsSection(
                title="Skills",
                show_levels=True,
                items=[SkillItem(name="Python", level=5), SkillItem(name="Rust")],
            ),
            DetailsSection(
                title="Personal", items=[DetailItem(label="City", value="Delft")]
            ),
        ],
        main=[
            TextSection(title="Profile", icon="person", body="Some prose."),
            ExperienceSection(
                title="Work",
                icon="laptop",
                entries=[
                    ExperienceEntry(
                        title="Engineer",
                        organisation="Acme",
                        period="2020",
                        description="Did things.",
                        bullets=["Shipped a feature", "Fixed a bug"],
                    )
                ],
            ),
            ListSection(title="Interests", bulleted=True, items=["Optics", "Cycling"]),
        ],
    )
    # Sidebar content is upper-cased by the template, so compare case-insensitively.
    text = page_text(render_bytes(doc)).upper()
    for expected in ("DELFT", "SOME PROSE.", "SHIPPED A FEATURE", "OPTICS", "RUST"):
        assert expected in text


def test_skill_rating_bars_are_drawn() -> None:
    accent = "#FF0000"
    doc = CVDocument(
        theme=Theme(accent=accent, sidebar_bg="#000000"),
        sidebar=[
            SkillsSection(
                title="Skills",
                show_levels=True,
                columns=1,
                items=[SkillItem(name="Python", level=5)],
            )
        ],
    )
    with open_pdf(render_bytes(doc)) as document:
        pixmap = document[0].get_pixmap(dpi=150)
        found = any(
            pixmap.pixel(x, y) == (0xFF, 0xFF, 0xFF)
            for y in range(0, pixmap.height, 3)
            for x in range(0, int(pixmap.width * 0.3), 3)
        )
    # The bar is drawn in the sidebar's text colour, which is white by default.
    assert found


def test_unicode_content_renders() -> None:
    doc = CVDocument(
        full_name="Zoë Müller",
        main=[TextSection(title="Profil", body="Führung, naïve café — dash…")],
    )
    text = page_text(render_bytes(doc))
    assert "Führung" in text
    assert "café" in text


def test_render_writes_to_any_binary_stream(cv: CVDocument) -> None:
    from job_scout.cv.render import render_pdf

    buffer = io.BytesIO()
    assert render_pdf(cv, buffer) == 1
    assert buffer.getvalue().startswith(b"%PDF")


def test_render_is_deterministic_for_text(cv: CVDocument) -> None:
    assert page_text(render_bytes(cv)) == page_text(render_bytes(cv))


def test_portrait_is_clipped_to_a_circle(cv: CVDocument, tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    photo = tmp_path / "red.png"
    photo.write_bytes(make_image(400, 400, (255, 0, 0)))
    cv.theme.sidebar_bg = "#000000"

    with open_pdf(render_bytes(cv, photo)) as document:
        pixmap = document[0].get_pixmap(dpi=150)
        geometry = build_geometry(cv.theme)
        scale = 150 / 72
        centre_x = int(geometry.sidebar_width / 2 * scale)
        # The portrait's top-left corner region falls outside the circle, so it
        # must still show the sidebar colour rather than the image.
        diameter = geometry.sidebar_width * cv.theme.photo_diameter_pct * scale
        top = int(geometry.top_margin * scale)
        corner_x = int(centre_x - diameter / 2 + 4)
        corner_y = top + 4
        assert pixmap.pixel(corner_x, corner_y) == (0, 0, 0)
        assert pixmap.pixel(centre_x, top + int(diameter / 2)) == (255, 0, 0)


def test_geometry_columns_do_not_overlap() -> None:
    geometry = build_geometry(Theme())
    sidebar = geometry.sidebar_column
    main = geometry.main_column
    assert sidebar.x + sidebar.width <= main.x
    assert main.x + main.width <= geometry.page_width


def test_pymupdf_agrees_with_our_text_measurement(cv: CVDocument) -> None:
    """Guards the wrapping maths: nothing may spill past the column edge."""
    geometry = build_geometry(cv.theme)
    limit = geometry.main_column.x + geometry.main_column.width + 1.0
    with open_pdf(render_bytes(cv)) as document:
        for page in document:
            for block in page.get_text("blocks"):
                x0, _, x1 = block[0], block[1], block[2]
                if x0 < geometry.main_column.x - 1:
                    continue
                assert x1 <= limit, f"text overflows the main column: {block[4]!r}"
