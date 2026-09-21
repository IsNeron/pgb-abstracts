from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .models import Author, Section, Source, Submission, SubmissionType

COAUTHOR_QUESTION_IDENTIFIER = "CDHXWQFZ"
MISSING_VALUES = {"", "-", "–", "—"}
NO_COAUTHORS = {*MISSING_VALUES, "нет"}
PERSON_IN_AFFILIATION_PATTERN = re.compile(
    r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.?\s*[А-ЯЁ]\.(?:\d+)?"
)
SHORT_NAME_PATTERN = re.compile(
    r"^(?P<surname>[^\W\d_]+(?:-[^\W\d_]+)*)\s+"
    r"(?P<first>[^\W\d_])\.\s*(?P<patronymic>[^\W\d_])\.?$",
    re.UNICODE,
)
INDEXED_AFFILIATION_PATTERN = re.compile(r"^\s*(\d+)\s*(.+)$")
SURNAME_INITIALS_PATTERN = re.compile(
    r"^[^\W\d_]+(?:-[^\W\d_]+)*\s+[^\W\d_]\.\s*[^\W\d_]\.?$",
    re.UNICODE,
)
INITIALS_SURNAME_PATTERN = re.compile(
    r"^(?:[^\W\d_]\.\s*){1,2}[^\W\d_]+(?:-[^\W\d_]+)*$",
    re.UNICODE,
)
INITIALS_FIRST_CANONICAL_PATTERN = re.compile(
    r"^(?P<first>[^\W\d_])\.\s*(?P<patronymic>[^\W\d_])\.\s*"
    r"(?P<surname>[^\W\d_]+(?:-[^\W\d_]+)*)$",
    re.UNICODE,
)


def normalize_submissions(
    raw_submissions: Iterable[Mapping[str, Any]],
    include_states: Iterable[str],
    include_submission_types: Iterable[str] = (),
    speaker_emails: Mapping[str, str] | None = None,
) -> list[Submission]:
    states = set(include_states)
    submission_types = {name.casefold() for name in include_submission_types}
    return [
        normalize_submission(raw, speaker_emails)
        for raw in raw_submissions
        if str(raw.get("state", "")) in states
        and (
            not submission_types
            or _raw_submission_type_name(raw).casefold() in submission_types
        )
    ]


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
        authors[0].affiliation = _clean_missing(raw_coauthors or "")
        parsing_warnings: list[str] = []
    else:
        coauthors, parsing_warnings = parse_coauthors(raw_coauthors)
        authors.extend(coauthors)
    authors = _deduplicate_authors(authors)
    abstract = _localized(raw.get("abstract"))
    return Submission(
        code=code,
        state=_text(raw.get("state")),
        title=_localized(raw.get("title")),
        section=section,
        submission_type=submission_type,
        authors=authors,
        keywords=parse_keywords(_localized(raw.get("description"))),
        abstract="" if _is_missing(abstract) else abstract,
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
    indexed = _parse_indexed_coauthors(value)
    if indexed is not None:
        return indexed
    authors: list[Author] = []
    warnings: list[str] = []
    raw_items = value.replace("；", ";").split(";")
    for number, raw_item in enumerate(raw_items, start=1):
        item = raw_item.strip()
        if not item:
            if number == len(raw_items):
                # A trailing semicolon is common and does not imply a missing author.
                continue
            warnings.append(f"coauthor item {number} is empty")
            continue
        name, separator, affiliation = item.partition(",")
        name = name.strip()
        affiliation = _clean_missing(affiliation)
        if not separator or not name or not affiliation:
            warnings.append(f"coauthor item {number} cannot be parsed reliably: {item}")
            continue
        if _affiliation_contains_person_name(affiliation):
            warnings.append(
                f"coauthor item {number} affiliation may contain additional person names: "
                f"{affiliation}"
            )
            continue
        authors.append(Author(name=name, affiliation=affiliation, role="coauthor"))
    return authors, warnings


def _parse_indexed_coauthors(value: str) -> tuple[list[Author], list[str]] | None:
    lines = [line.strip() for line in _clean_text(value).split("\n") if line.strip()]
    if len(lines) < 2:
        return None
    affiliations: dict[str, str] = {}
    for line in lines[1:]:
        match = INDEXED_AFFILIATION_PATTERN.fullmatch(line)
        if not match:
            return None
        affiliations[match.group(1)] = match.group(2).strip()
    if not affiliations:
        return None

    parsed: list[tuple[str, str | None]] = []
    for token in (part.strip() for part in lines[0].split(",")):
        match = re.fullmatch(r"(.+?)(\d+)?", token)
        if not match:
            return None
        name = match.group(1).strip()
        index = match.group(2)
        if not (
            SURNAME_INITIALS_PATTERN.fullmatch(name)
            or INITIALS_SURNAME_PATTERN.fullmatch(name)
        ):
            return None
        parsed.append((name, index))
    if not parsed or not any(index for _, index in parsed):
        return None

    authors: list[Author] = []
    warnings: list[str] = []
    for number, (name, index) in enumerate(parsed, start=1):
        affiliation = affiliations.get(index or "", "")
        if not affiliation:
            warnings.append(
                f"coauthor item {number} has no affiliation for index {index or 'none'}: {name}"
            )
        authors.append(Author(name=name, affiliation=affiliation, role="coauthor"))
    return authors, warnings


def parse_keywords(value: str | None) -> list[str]:
    if value is None or _is_missing(value):
        return []
    return [
        keyword.strip()
        for keyword in value.split(",")
        if keyword.strip() and not _is_missing(keyword)
    ]


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
                affiliation=_clean_missing(_localized(speaker.get("biography"))),
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


def _clean_missing(value: str) -> str:
    cleaned = _clean_text(value)
    return "" if _is_missing(cleaned) else cleaned


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in MISSING_VALUES


def _affiliation_contains_person_name(value: str) -> bool:
    normalized = " ".join(value.split())
    for match in PERSON_IN_AFFILIATION_PATTERN.finditer(normalized):
        prefix = normalized[: match.start()]
        if re.search(r"(?:\bим\.?|\bимени)\s+$", prefix, flags=re.IGNORECASE):
            continue
        return True
    return False


def _deduplicate_authors(authors: list[Author]) -> list[Author]:
    unique: list[Author] = []
    seen: set[str] = set()
    for author in authors:
        key = _normalized_author_key(author.name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(author)
    return unique


def _normalized_author_key(value: str) -> str:
    name = " ".join(value.split())
    parts = name.split()
    if len(parts) == 3 and all(_is_name_word(part) for part in parts):
        name = f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    else:
        initials_first = INITIALS_FIRST_CANONICAL_PATTERN.fullmatch(name)
        short = SHORT_NAME_PATTERN.fullmatch(name)
        if initials_first:
            name = (
                f"{initials_first.group('surname')} "
                f"{initials_first.group('first')}.{initials_first.group('patronymic')}."
            )
        elif short:
            name = (
                f"{short.group('surname')} "
                f"{short.group('first')}.{short.group('patronymic')}."
            )
    return name.casefold()


def _is_name_word(value: str) -> bool:
    return all(part.isalpha() and len(part) > 1 for part in value.split("-"))


def _numeric(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
