from __future__ import annotations

import csv
import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from .models import Author, Section, Source, Submission, SubmissionType, ValidationRecord


class ProceedingsDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


ProceedingsDumper.add_representer(str, _represent_string)


def write_raw(path: Path, submissions: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(submissions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_raw(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read raw submissions from {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"Raw submissions in {path} must be a list of objects")
    return data


def write_proceedings(
    path: Path,
    event: str,
    include_states: tuple[str, ...],
    include_submission_types: tuple[str, ...],
    submissions: list[Submission],
) -> None:
    document = {
        "schema_version": 1,
        "event": event,
        "include_states": list(include_states),
        "include_submission_types": list(include_submission_types),
        "submissions": [submission.to_dict() for submission in submissions],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            document,
            Dumper=ProceedingsDumper,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        ),
        encoding="utf-8",
    )


def read_proceedings(path: Path) -> list[Submission]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read proceedings from {path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("submissions"), list):
        raise ValueError(f"Proceedings in {path} must contain a submissions list")
    return [_submission_from_dict(item) for item in document["submissions"]]


def write_validation(path: Path, records: list[ValidationRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    column_names = [field.name for field in fields(ValidationRecord)]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=column_names)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "submission_code": record.submission_code,
                    "title": record.title,
                    "track": record.track,
                    "authors_count": record.authors_count,
                    "has_keywords": str(record.has_keywords).lower(),
                    "has_abstract": str(record.has_abstract).lower(),
                    "warnings": " | ".join(record.warnings),
                    "errors": " | ".join(record.errors),
                    "status": record.status,
                }
            )


def write_validation_report(path: Path, records: list[ValidationRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = sum(not record.errors and not record.warnings for record in records)
    with_warnings = sum(bool(record.warnings) for record in records)
    with_errors = sum(bool(record.errors) for record in records)
    sections: dict[str, list[ValidationRecord]] = {}
    for record in records:
        if record.errors or record.warnings:
            section = " ".join(record.track.split()) or "Без секции"
            sections.setdefault(section, []).append(record)

    lines = [
        "# Отчёт о проверке тезисов",
        "",
        "## Сводка",
        "",
        "| Результат | Количество заявок |",
        "|---|---:|",
        f"| Всего проверено | {len(records)} |",
        f"| Без замечаний | {valid} |",
        f"| С предупреждениями | {with_warnings} |",
        f"| С ошибками | {with_errors} |",
        "",
        "## Как исправлять замечания",
        "",
        "1. Найдите заявку по коду в `data/proceedings.yaml`.",
        "2. Исправьте нормализованные поля `authors`, `title`, `abstract` или `keywords`.",
        "3. Если проблема была в разборе соавторов, удалите исправленную запись из "
        "`normalization_warnings`, но сохраните исходный ответ в `source.coauthor_answer`.",
        "4. Запустите `uv run esoil-proceedings validate`. Команды `normalize` и `all` "
        "перезапишут ручные исправления из raw-данных.",
        "",
    ]
    if not sections:
        lines.append("Ошибок и предупреждений нет.")
    for section, section_records in sections.items():
        lines.extend([f"## Секция: {section}", ""])
        for record in section_records:
            title = " ".join(record.title.split()) or "Без названия"
            lines.extend(
                [
                    f"### {title}",
                    "",
                    f"Код заявки: `{record.submission_code}`",
                    "",
                ]
            )
            lines.extend(
                f"- **Ошибка:** {_humanize_issue(error)}" for error in record.errors
            )
            lines.extend(
                f"- **Предупреждение:** {_humanize_issue(warning)}"
                for warning in record.warnings
            )
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _humanize_issue(issue: str) -> str:
    translations = {
        "missing title": "отсутствует название",
        "missing abstract": "отсутствует текст тезисов",
        "missing speaker": "не указан докладчик",
        "missing track": "не указана секция",
        "missing keywords": "не указаны ключевые слова",
        "duplicate title": "название заявки повторяется",
        "title is all caps": (
            "название целиком набрано прописными буквами; исправьте регистр в "
            "conf.esoil.ru и заново выполните `fetch`, `normalize`, `validate`"
        ),
    }
    if issue in translations:
        return translations[issue]

    prefixes = {
        "missing affiliation: ": "не указана аффилиация автора: ",
        "affiliation too short: ": "подозрительно короткая аффилиация автора: ",
        "suspicious name punctuation: ": "подозрительная пунктуация в имени: ",
        "duplicate author: ": "автор указан повторно: ",
    }
    for prefix, translation in prefixes.items():
        if issue.startswith(prefix):
            return translation + issue.removeprefix(prefix)

    suspicious = re.fullmatch(
        r"coauthor item (\d+) has suspicious name punctuation and cannot be parsed reliably: (.*)",
        issue,
    )
    if suspicious:
        return (
            f"подозрительная пунктуация в имени соавтора №{suspicious.group(1)}; "
            f"строка не разобрана автоматически: {suspicious.group(2)}"
        )
    unparsed = re.fullmatch(r"coauthor item (\d+) cannot be parsed reliably: (.*)", issue)
    if unparsed:
        return (
            f"соавтор №{unparsed.group(1)} не разобран однозначно: {unparsed.group(2)}"
        )
    empty = re.fullmatch(r"coauthor item (\d+) is empty", issue)
    if empty:
        return f"элемент соавторов №{empty.group(1)} пуст"
    return issue


def _submission_from_dict(value: Any) -> Submission:
    if not isinstance(value, dict):
        raise ValueError("Every normalized submission must be a mapping")
    section = value.get("section") or {}
    submission_type = value.get("submission_type") or {}
    source = value.get("source") or {}
    authors = value.get("authors") or []
    if (
        not isinstance(section, dict)
        or not isinstance(submission_type, dict)
        or not isinstance(source, dict)
        or not isinstance(authors, list)
    ):
        raise ValueError("Invalid normalized submission structure")
    return Submission(
        code=str(value.get("code", "")),
        state=str(value.get("state", "")),
        title=str(value.get("title", "")),
        section=Section(
            id=section.get("id"),
            name=str(section.get("name", "")),
            position=section.get("position"),
        ),
        submission_type=SubmissionType(
            id=submission_type.get("id"),
            name=str(submission_type.get("name", "")),
        ),
        authors=[
            Author(
                name=str(author.get("name", "")),
                affiliation=str(author.get("affiliation", "")),
                role=author.get("role", "coauthor"),
                email=str(author["email"]) if author.get("email") else None,
            )
            for author in authors
            if isinstance(author, dict)
        ],
        keywords=[str(keyword) for keyword in value.get("keywords", [])],
        abstract=str(value.get("abstract", "")),
        source=Source(
            submission_code=str(source.get("submission_code", "")),
            coauthor_answer=source.get("coauthor_answer"),
            coauthor_question_identifier=source.get("coauthor_question_identifier"),
        ),
        normalization_warnings=[str(item) for item in value.get("normalization_warnings", [])],
    )
