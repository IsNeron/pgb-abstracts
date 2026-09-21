from __future__ import annotations

import csv

import yaml

from esoil_proceedings.normalize import normalize_submission
from esoil_proceedings.storage import (
    read_proceedings,
    write_proceedings,
    write_validation,
    write_validation_report,
)
from esoil_proceedings.validate import validate_submissions


def test_yaml_round_trip_and_csv_columns(tmp_path, raw_submission: dict[str, object]) -> None:
    submission = normalize_submission(raw_submission)
    submission.authors[0].affiliation = ""
    yaml_path = tmp_path / "proceedings.yaml"
    csv_path = tmp_path / "validation.csv"
    report_path = tmp_path / "validation.md"

    write_proceedings(
        yaml_path,
        "pgb2026",
        ("confirmed",),
        ("Устный доклад",),
        [submission],
    )
    document = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    restored = read_proceedings(yaml_path)
    records = validate_submissions(restored)
    write_validation(csv_path, records)
    write_validation_report(report_path, records)

    assert document["submissions"][0]["source"]["submission_code"] == "37WVUJ"
    assert "abstract: |" in yaml_path.read_text(encoding="utf-8")
    assert restored == [submission]
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert list(row) == [
        "submission_code",
        "title",
        "track",
        "authors_count",
        "has_keywords",
        "has_abstract",
        "warnings",
        "errors",
        "status",
    ]
    report = report_path.read_text(encoding="utf-8")
    assert "# Отчёт о проверке тезисов" in report
    assert "| Всего проверено | 1 |" in report
    assert "## Секция: 1.5. Физика и гидрология почв" in report
    assert "### Годовая динамика почв" in report
    assert "не указана аффилиация автора: Тимофеева Мария Валерьевна" in report
