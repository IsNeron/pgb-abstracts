from __future__ import annotations

from copy import deepcopy

from esoil_proceedings.models import Author
from esoil_proceedings.normalize import normalize_submission
from esoil_proceedings.validate import validate_submissions


def test_validation_errors_and_warnings(raw_submission: dict[str, object]) -> None:
    broken = deepcopy(raw_submission)
    broken.update({"title": "", "abstract": "", "description": "", "track": None, "speakers": []})
    submission = normalize_submission(broken)
    submission.normalization_warnings.append("coauthor item 1 cannot be parsed reliably")

    record = validate_submissions([submission])[0]

    assert set(record.errors) == {
        "missing title",
        "missing abstract",
        "missing speaker",
        "missing track",
    }
    assert "missing keywords" in record.warnings
    assert "coauthor item 1 cannot be parsed reliably" in record.warnings
    assert record.status == "error"


def test_duplicate_title_and_author_detection(raw_submission: dict[str, object]) -> None:
    first = normalize_submission(raw_submission)
    first.authors.append(
        Author(
            name=first.authors[0].name,
            affiliation=first.authors[0].affiliation,
            role="coauthor",
        )
    )
    second_raw = deepcopy(raw_submission)
    second_raw["code"] = "OTHER"
    second_raw["title"] = "  ГОДОВАЯ ДИНАМИКА ПОЧВ  "
    second = normalize_submission(second_raw)

    records = validate_submissions([first, second])

    assert "duplicate title" in records[0].warnings
    assert "duplicate title" in records[1].warnings
    assert any(warning.startswith("duplicate author:") for warning in records[0].warnings)


def test_name_punctuation_and_affiliation_warnings(raw_submission: dict[str, object]) -> None:
    submission = normalize_submission(raw_submission)
    submission.authors[0].name = "Иванов А,В."
    submission.authors[0].affiliation = "МГУ"

    record = validate_submissions([submission])[0]

    assert "suspicious name punctuation: Иванов А,В." in record.warnings
    assert "affiliation too short: Иванов А,В." in record.warnings


def test_speaker_is_optional_for_practical_events(raw_submission: dict[str, object]) -> None:
    raw_submission["track"] = {
        "id": 99,
        "name": {"ru": "Научно-практические мероприятия"},
        "position": 99,
    }
    raw_submission["speakers"] = []
    submission = normalize_submission(raw_submission)

    record = validate_submissions([submission])[0]

    assert "missing speaker" not in record.errors
    assert record.status == "valid"
