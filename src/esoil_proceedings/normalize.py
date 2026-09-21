from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .models import Author, Section, Source, Submission, SubmissionType

COAUTHOR_QUESTION_IDENTIFIER = "CDHXWQFZ"
NO_COAUTHORS = {"-", "—", "нет"}
SUSPICIOUS_INITIALS = re.compile(r"(?:^|\s)[A-ZА-ЯЁ],[A-ZА-ЯЁ]\.", re.IGNORECASE)


def normalize_submissions(
    raw_submissions: Iterable[Mapping[str, Any]],
    include_states: Iterable[str],
    include_submission_types: Iterable[str] = (),
    speaker_emails: Mapping[str, str] | None = None,
) -> list[Submission]:
    states = set(include_states)
    submission_types = {name.casefold() for name in include_submission_types}
    submissions = [
        normalize_submission(raw, speaker_emails)
        for raw in raw_submissions
        if str(raw.get("state", "")) in states
        and (
            not submission_types
            or _raw_submission_type_name(raw).casefold() in submission_types
        )
    ]
    return sorted(submissions, key=_sort_key)


def _raw_submission_type_name(raw: Mapping[str, Any]) -> str:
    value = raw.get("submission_type")
    if not isinstance(value, Mapping):
        return ""
    return _localized(value.get("name"))


def normalize_submission(
    raw: Mapping[str, Any], speaker_emails: Mapping[str, str] | None = None
) -> Submission:
    code = _text(raw.get("code"))
    track = raw.get("track") if isinstance(raw.get("track"), Mapping) else {}
    section = Section(
        id=track.get("id"),
        name=_localized(track.get("name")),
        position=_numeric(track.get("position")),
    )
    raw_submission_type = (
        raw.get("submission_type") if isinstance(raw.get("submission_type"), Mapping) else {}
    )
    submission_type = SubmissionType(
        id=raw_submission_type.get("id"),
        name=_localized(raw_submission_type.get("name")),
    )
    authors = _speaker_authors(raw.get("speakers"), speaker_emails or {})
    raw_coauthors, question_identifier = _coauthor_answer(raw.get("answers"))
    if _is_plenary_affiliation(submission_type, authors, raw_coauthors):
        authors[0].affiliation = _clean_text(raw_coauthors or "")
        parsing_warnings: list[str] = []
    else:
        coauthors, parsing_warnings = parse_coauthors(raw_coauthors)
        authors.extend(coauthors)
    return Submission(
        code=code,
        state=_text(raw.get("state")),
        title=_localized(raw.get("title")),
        section=section,
        submission_type=submission_type,
        authors=authors,
        keywords=parse_keywords(_localized(raw.get("description"))),
        abstract=_localized(raw.get("abstract")),
        source=Source(
            submission_code=code,
            coauthor_answer=raw_coauthors,
            coauthor_question_identifier=question_identifier,
        ),
        normalization_warnings=parsing_warnings,
    )


def _is_plenary_affiliation(
    submission_type: SubmissionType, authors: list[Author], raw_answer: str | None
) -> bool:
    if submission_type.name.strip().casefold() != "пленарный доклад":
        return False
    if len(authors) != 1 or authors[0].affiliation or not raw_answer:
        return False
    answer = raw_answer.strip()
    return (
        bool(answer)
        and answer.casefold() not in NO_COAUTHORS
        and "," not in answer
        and ";" not in answer
    )


def parse_coauthors(value: str | None) -> tuple[list[Author], list[str]]:
    if value is None or not value.strip() or value.strip().casefold() in NO_COAUTHORS:
        return [], []
    authors: list[Author] = []
    warnings: list[str] = []
    raw_items = value.split(";")
    for number, raw_item in enumerate(raw_items, start=1):
        item = raw_item.strip()
        if not item:
            if number == len(raw_items):
                # A trailing semicolon is common and does not imply a missing author.
                continue
            warnings.append(f"coauthor item {number} is empty")
            continue
        if SUSPICIOUS_INITIALS.search(item):
            warnings.append(
                f"coauthor item {number} has suspicious name punctuation and cannot be "
                f"parsed reliably: {item}"
            )
            continue
        name, separator, affiliation = item.partition(",")
        name = name.strip()
        affiliation = affiliation.strip()
        if not separator or not name or not affiliation:
            warnings.append(f"coauthor item {number} cannot be parsed reliably: {item}")
            continue
        authors.append(Author(name=name, affiliation=affiliation, role="coauthor"))
    return authors, warnings


def parse_keywords(value: str | None) -> list[str]:
    if not value:
        return []
    return [keyword.strip() for keyword in value.split(",") if keyword.strip()]


def _speaker_authors(value: Any, speaker_emails: Mapping[str, str]) -> list[Author]:
    if not isinstance(value, list):
        return []
    authors: list[Author] = []
    for speaker in value:
        if not isinstance(speaker, Mapping):
            continue
        authors.append(
            Author(
                name=_text(speaker.get("name")),
                affiliation=_localized(speaker.get("biography")),
                role="speaker",
                email=(
                    _text(speaker.get("email"))
                    or speaker_emails.get(_text(speaker.get("code")))
                    or None
                ),
            )
        )
    return authors


def _coauthor_answer(answers: Any) -> tuple[str | None, str | None]:
    if not isinstance(answers, list):
        return None, None
    for item in answers:
        if not isinstance(item, Mapping):
            continue
        question = item.get("question")
        if not isinstance(question, Mapping):
            continue
        identifier = _text(question.get("identifier"))
        if identifier != COAUTHOR_QUESTION_IDENTIFIER:
            continue
        answer = item.get("answer")
        if answer is None:
            return None, identifier
        if isinstance(answer, str):
            return answer, identifier
        return str(answer), identifier
    return None, None


def _localized(value: Any, locale: str = "ru") -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, Mapping):
        localized = value.get(locale)
        if isinstance(localized, str):
            return _clean_text(localized)
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return _clean_text(candidate)
    return ""


def _text(value: Any) -> str:
    return _clean_text(value) if isinstance(value, str) else ""


def _clean_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def _numeric(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sort_key(submission: Submission) -> tuple[bool, float, str, str]:
    position = submission.section.position
    return (
        position is None,
        float(position) if position is not None else 0.0,
        submission.section.name.casefold(),
        submission.title.casefold(),
    )
