"""Profile persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_scout.cv.models import CVDocument, TextSection
from job_scout.cv.storage import (
    DATA_ENV_VAR,
    ProfileStore,
    StorageError,
    default_data_dir,
    normalise_slug,
)
from tests.cv.conftest import make_image


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sam de Vries", "sam-de-vries"),
        ("  Mixed CASE  ", "mixed-case"),
        ("Zoë Müller", "zoe-muller"),
        ("a...b", "a-b"),
        ("Job @ 2024!", "job-2024"),
    ],
)
def test_normalise_slug(raw: str, expected: str) -> None:
    assert normalise_slug(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "...", "///", "!!!"])
def test_unusable_names_are_rejected(raw: str) -> None:
    with pytest.raises(StorageError):
        normalise_slug(raw)


@pytest.mark.parametrize(
    "attack",
    ["../../etc/passwd", "..\\..\\windows", "/absolute/path", "a/../../b"],
)
def test_slugs_cannot_escape_the_data_directory(
    store: ProfileStore, attack: str
) -> None:
    directory = store.profile_dir(attack)
    assert store.profiles_dir in directory.parents
    assert ".." not in directory.parts


def test_save_and_load_round_trip(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    loaded = store.load("me")
    assert loaded.model_dump() == cv.model_dump()


def test_save_creates_the_uploads_directory(
    store: ProfileStore, cv: CVDocument
) -> None:
    store.save("me", cv)
    assert store.uploads_dir("me").is_dir()


def test_save_leaves_no_temporary_file(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    assert not list(store.profile_dir("me").glob("*.tmp"))


def test_save_overwrites_cleanly(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    cv.full_name = "Someone Else"
    cv.main = [TextSection(title="Only", body="one")]
    store.save("me", cv)
    loaded = store.load("me")
    assert loaded.full_name == "Someone Else"
    assert len(loaded.main) == 1


def test_load_missing_profile_raises(store: ProfileStore) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        store.load("ghost")


def test_load_corrupt_json_raises(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    store.document_path("me").write_text("{not json", encoding="utf-8")
    with pytest.raises(StorageError, match="corrupt"):
        store.load("me")


def test_load_schema_violation_raises(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    store.document_path("me").write_text(
        json.dumps({"theme": {"accent": "not-a-colour"}}), encoding="utf-8"
    )
    with pytest.raises(StorageError, match="corrupt"):
        store.load("me")


def test_list_profiles_is_sorted(store: ProfileStore, cv: CVDocument) -> None:
    for name in ("charlie", "alpha", "bravo"):
        store.save(name, cv)
    assert store.list_profiles() == ["alpha", "bravo", "charlie"]


def test_list_ignores_directories_without_a_document(
    store: ProfileStore, cv: CVDocument
) -> None:
    store.save("real", cv)
    (store.profiles_dir / "stray").mkdir(parents=True)
    assert store.list_profiles() == ["real"]


def test_delete_removes_everything(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    store.save_photo("me", make_image(100, 100, (1, 2, 3)))
    store.delete("me")
    assert not store.profile_dir("me").exists()
    assert store.list_profiles() == []


def test_delete_missing_profile_raises(store: ProfileStore) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        store.delete("ghost")


def test_save_photo_squares_the_image(store: ProfileStore, cv: CVDocument) -> None:
    from PIL import Image

    store.save("me", cv)
    store.save_photo("me", make_image(400, 200, (10, 20, 30)))
    with Image.open(store.uploads_dir("me") / "portrait.png") as image:
        assert image.width == image.height == 200


def test_save_photo_rejects_junk(store: ProfileStore, cv: CVDocument) -> None:
    store.save("me", cv)
    with pytest.raises(StorageError):
        store.save_photo("me", b"definitely not an image")


def test_photo_path_only_resolves_inside_uploads(
    store: ProfileStore, cv: CVDocument
) -> None:
    store.save("me", cv)
    store.save_photo("me", make_image(50, 50, (0, 0, 0)))
    cv.photo = "../../../../etc/passwd"
    # Only the bare filename is honoured, so the traversal resolves to nothing.
    assert store.photo_path("me", cv) is None

    cv.photo = "portrait.png"
    resolved = store.photo_path("me", cv)
    assert resolved is not None
    assert resolved.parent == store.uploads_dir("me")


def test_photo_path_is_none_without_a_photo(
    store: ProfileStore, cv: CVDocument
) -> None:
    cv.photo = ""
    assert store.photo_path("me", cv) is None


def test_ensure_default_seeds_both_languages(store: ProfileStore) -> None:
    slug = store.ensure_default()
    assert slug == "default"
    assert store.list_profiles() == ["default", "nederlands"]

    for name, language in (("default", "EN"), ("nederlands", "NL")):
        doc = store.load(name)
        assert doc.full_name == "Sam de Vries"
        assert doc.language == language
        assert store.photo_path(name, doc) is not None

    # A second call must not create anything further.
    assert store.ensure_default() == "default"
    assert store.list_profiles() == ["default", "nederlands"]


def test_ensure_default_keeps_existing_profiles(
    store: ProfileStore, cv: CVDocument
) -> None:
    store.save("mine", cv)
    assert store.ensure_default() == "mine"
    assert store.list_profiles() == ["mine"]


def test_data_dir_honours_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_ENV_VAR, str(tmp_path / "elsewhere"))
    assert default_data_dir() == (tmp_path / "elsewhere").resolve()


def test_data_dir_defaults_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(DATA_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_data_dir() == (tmp_path / "data").resolve()
