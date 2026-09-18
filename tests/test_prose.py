"""Generated prose is put in the house style without changing what it says."""

from __future__ import annotations

import pytest

from job_scout.prose import clean_items, clean_prose, normalise_ranges

EM, EN, EURO = "\u2014", "\u2013", "\u20ac"


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        (
            f"Salaris {EURO}3.500 {EN} {EURO}5.000 bruto per maand",
            f"Salaris {EURO}3.500-{EURO}5.000 bruto per maand",
        ),
        (
            f"Schaal 10: {EURO} 3.500 - {EURO} 4.800 per maand.",
            f"Schaal 10: {EURO} 3.500-{EURO} 4.800 per maand.",
        ),
        (
            f"Salary {EURO}3,500{EN}{EURO}4,500 per month.",
            f"Salary {EURO}3,500-{EURO}4,500 per month.",
        ),
        (f"EUR 42k{EN}55k per jaar", "EUR 42k-55k per jaar"),
        ("3.500 EUR - 4.800 EUR", "3.500 EUR-4.800 EUR"),
        (f"Werkzaam van maart 2019 {EN} heden", "Werkzaam van maart 2019-heden"),
        (
            f"Projectleider (jan 2019 {EN} dec 2021)",
            "Projectleider (jan 2019-dec 2021)",
        ),
        (f"Sept 2019 {EM} Present", "Sept 2019-Present"),
        (f"Van 2019 {EN} 2021 bij Acme.", "Van 2019-2021 bij Acme."),
    ],
)
def test_a_range_stays_a_range(generated: str, cleaned: str) -> None:
    """humanise sees a range only between two digits; these are ranges too."""
    assert clean_prose(generated) == cleaned


@pytest.mark.parametrize(
    "kept",
    [
        "06-51418568",
        "B2B-klanten in de e-commerce, 3-jarig contract.",
        "Mijn salarisindicatie is EUR 50.000-70.000 per jaar.",
    ],
)
def test_ordinary_hyphens_are_left_alone(kept: str) -> None:
    assert normalise_ranges(kept) == kept
    assert clean_prose(kept) == kept


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        (
            f"Ik heb vijf jaar ervaring {EM} nu wil ik verder.",
            "Ik heb vijf jaar ervaring, nu wil ik verder.",
        ),
        ("Kosten 30% omlaag - en snel.", "Kosten 30% omlaag, en snel."),
        (f"In 2019 {EM} een goed jaar.", "In 2019, een goed jaar."),
    ],
)
def test_a_dash_that_joins_clauses_still_becomes_a_comma(
    generated: str, cleaned: str
) -> None:
    """Only a dash between two ends of a range is kept as a hyphen."""
    assert clean_prose(generated) == cleaned


def test_a_range_never_swallows_the_line_break_before_a_bullet() -> None:
    assert clean_prose("Gestart in 2019\n- 2020 was goed") == (
        "Gestart in 2019\n- 2020 was goed"
    )


def test_e_mail_addresses_and_links_are_kept_exactly() -> None:
    """humanise would read the underscores as markdown emphasis."""
    generated = (
        "Mail jan__de__vries@example.nl of kijk op https://x.example/a__b__c "
        f"{EM} graag **snel**."
    )

    assert clean_prose(generated) == (
        "Mail jan__de__vries@example.nl of kijk op https://x.example/a__b__c, "
        "graag snel."
    )


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        (
            f'De zin "Ik ben {EM} echt {EM} gemotiveerd" is gegenereerd {EM} jammer.',
            f'De zin "Ik ben {EM} echt {EM} gemotiveerd" is gegenereerd, jammer.',
        ),
        (f'Vervang "{EM}" door een punt.', f'Vervang "{EM}" door een punt.'),
        (
            f"Replace the em dash ({EM}) with a full stop.",
            f"Replace the em dash ({EM}) with a full stop.",
        ),
        (
            f"Schrap \u201cmet **veel** passie\u201d {EM} dat is loos.",
            "Schrap \u201cmet **veel** passie\u201d, dat is loos.",
        ),
        (
            f"Liever \u201eik ben er klaar voor {EM} echt\u201d niet.",
            f"Liever \u201eik ben er klaar voor {EM} echt\u201d niet.",
        ),
    ],
)
def test_quotes_are_kept_when_asked(generated: str, cleaned: str) -> None:
    """A review quoting the applicant's letter must show what the letter says."""
    assert clean_prose(generated, keep_quotes=True) == cleaned


def test_quotes_are_cleaned_by_default() -> None:
    """Most prose is all the model's own, quotation marks included."""
    assert clean_prose(f'Zeg "ja {EM} graag".') == 'Zeg "ja, graag".'


def test_a_quote_holding_an_e_mail_address_comes_back_whole() -> None:
    generated = f'Hij schreef "mail a__b@example.nl {EM} snel" {EM} prima.'

    assert clean_prose(generated, keep_quotes=True) == (
        f'Hij schreef "mail a__b@example.nl {EM} snel", prima.'
    )


def test_empty_text_stays_empty() -> None:
    assert clean_prose("") == ""


def test_clean_items_cleans_only_the_named_string_fields() -> None:
    """Parsed JSON is cleaned before validation; the rest is left for it."""
    items: object = [
        {"question": f"Waarom {EM} nu?", "kind": f"a {EM} b", "why": 3},
        "not an object",
    ]

    clean_items(items, ("question", "why"))

    assert items == [
        {"question": "Waarom, nu?", "kind": f"a {EM} b", "why": 3},
        "not an object",
    ]


def test_clean_items_ignores_a_value_that_is_not_a_list() -> None:
    items: object = {"question": f"Waarom {EM} nu?"}

    clean_items(items, ("question",))

    assert items == {"question": f"Waarom {EM} nu?"}


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        ("2019 -- heden", "2019-heden"),
        (f"{EURO} 3.500 -- {EURO} 4.800", f"{EURO} 3.500-{EURO} 4.800"),
        ("42k--55k", "42k-55k"),
        ("jan 2019 -- dec 2021", "jan 2019-dec 2021"),
        ("Strong fit -- but the role is senior", "Strong fit, but the role is senior"),
        ("run it with --verbose", "run it with --verbose"),
    ],
)
def test_a_double_hyphen_range_stays_a_range(generated: str, cleaned: str) -> None:
    """Plain text writes a range with two hyphens as often as with one."""
    assert clean_prose(generated) == cleaned


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        (
            f"Groei van {EURO} 10 {EN} {EURO} 14 miljoen omzet",
            f"Groei van {EURO} 10-{EURO} 14 miljoen omzet",
        ),
        (f"Omzet {EURO}3m {EN} {EURO}5m", f"Omzet {EURO}3m-{EURO}5m"),
        (f"Omzet 3 mln {EN} 5 mln", "Omzet 3 mln-5 mln"),
    ],
)
def test_a_revenue_range_stays_a_range(generated: str, cleaned: str) -> None:
    """Research and reviews write revenue in millions, not only salaries in k."""
    assert clean_prose(generated) == cleaned


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        (
            "Twee dingen:\n- ik plande de audits\n\u2022 ik schreef de procedure",
            "Twee dingen: ik plande de audits. Ik schreef de procedure.",
        ),
        ("* eerst dit\n* dan dat", "Eerst dit. Dan dat."),
        (
            "1. eerst dit\n2) dan dat\n\nNieuwe alinea.\n- met een punt.",
            "Eerst dit. Dan dat.\n\nNieuwe alinea. Met een punt.",
        ),
        ("2019 was een goed jaar.\n2020 ook.", "2019 was een goed jaar.\n2020 ook."),
        ("\u2022 company review: cons", "Company review: cons"),
        (
            f"Ik deed twee dingen.\n- plande audits {EM} vooruit;\n- schreef,",
            "Ik deed twee dingen. Plande audits, vooruit; schreef.",
        ),
    ],
)
def test_a_list_becomes_sentences_where_no_list_belongs(
    generated: str, cleaned: str
) -> None:
    """A spoken answer has no bullets; a paragraph break and a year stay."""
    assert clean_prose(generated, flatten_lists=True) == cleaned


def test_a_list_keeps_its_markers_by_default() -> None:
    """A letter may hold a list on purpose, so flattening is asked for."""
    assert clean_prose("* eerst dit\n* dan dat") == "- eerst dit\n- dan dat"


def test_flattening_keeps_links_and_addresses_whole() -> None:
    text = "Mail a-b@x.example\n- zie https://x.example/a-b"

    assert clean_prose(text, flatten_lists=True) == (
        "Mail a-b@x.example. Zie https://x.example/a-b."
    )


def test_clean_items_can_flatten_lists() -> None:
    items: object = [{"draft_answer": "Twee dingen:\n- plannen\n- schrijven"}]

    clean_items(items, ("draft_answer",), flatten_lists=True)

    assert items == [{"draft_answer": "Twee dingen: plannen. Schrijven."}]
