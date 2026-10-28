from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .affiliations import AffiliationRegistry, affiliation_key, clean_whitespace
from .models import Author, QAIssue, Section, Source, Submission, SubmissionType
from .richtext import MISSING_VALUES, normalize_richtext, plain_source

COAUTHOR_QUESTION_IDENTIFIER = "CDHXWQFZ"
NO_COAUTHORS = {*MISSING_VALUES, "нет"}
ORGANIZATION_IN_NAME = re.compile(
    r"\b(?:ФГБНУ|ФГБУ|ФИЦ|РАН|МГУ|ООО|АО)\b|"
    r"\b(?:университет|институт|центр|лаборатори|кафедр|факультет)\w*",
    re.I,
)
NAME_WORD = r"[A-ZА-ЯЁ][a-zа-яё]+(?:-[A-ZА-ЯЁa-zа-яё]+)*"
INITIAL = r"[A-ZА-ЯЁ]"
SHORT_NAME_PATTERN = re.compile(
    rf"^(?P<surname>{NAME_WORD})\s+(?P<first>{INITIAL})\.\s*"
    rf"(?P<patronymic>{INITIAL})\.?$"
)
INITIALS_FIRST_PATTERN = re.compile(
    rf"^(?P<first>{INITIAL})\.\s*(?P<patronymic>{INITIAL})\.\s+"
    rf"(?P<surname>{NAME_WORD})$"
)
FULL_NAME_PATTERN = re.compile(rf"^({NAME_WORD})\s+({NAME_WORD})\s+({NAME_WORD})$")
PERSON_IN_AFFILIATION_PATTERN = re.compile(rf"\b{NAME_WORD}\s+{INITIAL}\.\s*{INITIAL}\.")
DEFAULT_COMMON_WORDS = ("почвенный", "почвенная", "почвенное", "почвенные", "технология")


@dataclass
class ParsedAuthor:
    name: str
    affiliation: str
    role: str = "coauthor"
    email: str | None = None


def normalize_submissions(
    raw_submissions: Iterable[Mapping[str, Any]],
    include_states: Iterable[str],
    include_submission_types: Iterable[str] = (),
    speaker_emails: Mapping[str, str] | None = None,
    affiliation_registry: AffiliationRegistry | None = None,
    keyword_common_words: Iterable[str] = DEFAULT_COMMON_WORDS,
) -> list[Submission]:
    states = set(include_states)
    types = {name.casefold() for name in include_submission_types}
    return [
        normalize_submission(raw, speaker_emails, affiliation_registry, keyword_common_words)
        for raw in raw_submissions
        if str(raw.get("state", "")) in states
        and (not types or _raw_submission_type_name(raw).casefold() in types)
    ]


def normalize_submission(
    raw: Mapping[str, Any],
    speaker_emails: Mapping[str, str] | None = None,
    affiliation_registry: AffiliationRegistry | None = None,
    keyword_common_words: Iterable[str] = DEFAULT_COMMON_WORDS,
) -> Submission:
    registry = affiliation_registry or AffiliationRegistry()
    track = raw.get("track") if isinstance(raw.get("track"), Mapping) else {}
    raw_type = raw.get("submission_type")
    raw_type = raw_type if isinstance(raw_type, Mapping) else {}
    submission_type = SubmissionType(raw_type.get("id"), _localized(raw_type.get("name")))
    parsed: list[ParsedAuthor] = []
    for speaker in raw.get("speakers") or []:
        if isinstance(speaker, Mapping):
            parsed.append(
                ParsedAuthor(
                    name=_localized(speaker.get("name")),
                    affiliation=_localized(speaker.get("biography")),
                    role="speaker",
                    email=_text(speaker.get("email"))
                    or (speaker_emails or {}).get(_text(speaker.get("code")))
                    or None,
                )
            )
    raw_coauthors, question_identifier = _coauthor_answer(raw.get("answers"))
    issues: list[QAIssue] = []
    if _is_plenary_affiliation(submission_type, parsed, raw_coauthors):
        parsed[0].affiliation = raw_coauthors or ""
    else:
        coauthors, issues = parse_coauthors(raw_coauthors)
        parsed.extend(coauthors)
    authors, affiliations = normalize_authors(parsed, registry, issues)
    code = _text(raw.get("code"))
    return Submission(
        code=code,
        state=_text(raw.get("state")),
        title=plain_source(_localized(raw.get("title"))),
        section=Section(
            track.get("id"), _localized(track.get("name")), _numeric(track.get("position"))
        ),
        submission_type=submission_type,
        authors=authors,
        affiliations=affiliations,
        keywords=normalize_keywords(_localized(raw.get("description")), keyword_common_words),
        abstract=normalize_richtext(_localized(raw.get("abstract"))),
        source=Source(code, raw_coauthors, question_identifier),
        normalization_issues=issues,
    )


def normalize_person_name(value: str) -> tuple[str, bool]:
    name = clean_whitespace(value)
    if ORGANIZATION_IN_NAME.search(name):
        return name, False
    for pattern in (SHORT_NAME_PATTERN, INITIALS_FIRST_PATTERN):
        match = pattern.fullmatch(name)
        if match:
            return f"{match['surname']} {match['first']}.{match['patronymic']}.", True
    full = FULL_NAME_PATTERN.fullmatch(name)
    if full:
        return f"{full[1]} {full[2][0]}.{full[3][0]}.", True
    return name, False


def normalize_authors(
    parsed: list[ParsedAuthor],
    registry: AffiliationRegistry,
    issues: list[QAIssue],
) -> tuple[list[Author], dict[int, str]]:
    # Canonicalize and merge authors with affiliation strings before assigning IDs.
    unique: dict[str, tuple[ParsedAuthor, list[str]]] = {}
    for item in parsed:
        name, canonical = normalize_person_name(plain_source(item.name))
        if not canonical:
            issues.append(QAIssue("WARNING", "noncanonical_person_name", name))
        strings = registry.split(plain_source(item.affiliation.replace("\n", "<br>")))
        key = name.casefold()
        if key in unique and name:
            issues.append(QAIssue("INFO", "duplicate_author", item.name))
            previous, prior_strings = unique[key]
            prior_strings.extend(strings)
            if item.role == "speaker":
                previous.role = "speaker"
            previous.email = previous.email or item.email
        else:
            item.name = name
            unique[key if name else f"unnamed:{len(unique)}"] = (item, strings)
    affiliations: dict[int, str] = {}
    numbers: dict[str, int] = {}
    authors: list[Author] = []
    for item, strings in unique.values():
        refs: list[int] = []
        for affiliation in strings:
            key = affiliation_key(affiliation)
            if key in numbers:
                issues.append(QAIssue("INFO", "duplicate_affiliation", affiliation))
            else:
                numbers[key] = len(affiliations) + 1
                affiliations[numbers[key]] = affiliation
            if numbers[key] not in refs:
                refs.append(numbers[key])
        authors.append(Author(item.name, refs, item.role, item.email))
    return authors, affiliations


def parse_coauthors(value: str | None) -> tuple[list[ParsedAuthor], list[QAIssue]]:
    if value is None or value.strip().casefold() in NO_COAUTHORS:
        return [], []
    indexed = _parse_indexed_coauthors(value)
    if indexed is not None:
        return indexed
    authors: list[ParsedAuthor] = []
    issues: list[QAIssue] = []
    items = value.replace("；", ";").split(";")
    for index, raw_item in enumerate(items):
        item = plain_source(raw_item).strip()
        if not item and index == len(items) - 1:
            continue
        name, separator, affiliation = item.partition(",")
        if not separator or not name.strip() or not affiliation.strip():
            issues.append(QAIssue("WARNING", "unparsed_coauthor", raw_item))
            continue
        if affiliation_contains_person_name(affiliation):
            issues.append(QAIssue("ERROR", "unparsed_coauthor", raw_item))
            issues.append(
                QAIssue("WARNING", "suspicious_affiliation_contains_person_name", affiliation)
            )
            continue
        authors.append(ParsedAuthor(name.strip(), affiliation.strip()))
    return authors, issues


def _parse_indexed_coauthors(value: str) -> tuple[list[ParsedAuthor], list[QAIssue]] | None:
    lines = [line.strip() for line in value.replace("\r", "\n").split("\n") if line.strip()]
    if len(lines) < 2:
        return None
    affiliations: dict[str, str] = {}
    for line in lines[1:]:
        match = re.fullmatch(r"(\d+)\s*(.+)", line)
        if not match:
            return None
        if match[1] in affiliations:
            return [], [QAIssue("ERROR", "unparsed_coauthor", value)]
        affiliations[match[1]] = match[2]
    pattern = re.compile(
        rf"\s*((?:{NAME_WORD}\s+{INITIAL}\.\s*{INITIAL}\."
        rf"|{INITIAL}\.\s*{INITIAL}\.\s*{NAME_WORD}))\s*"
        r"(\d+(?:\s*,\s*\d+)*)(?:\s*,\s*|$)"
    )
    cursor = 0
    authors: list[ParsedAuthor] = []
    issues: list[QAIssue] = []
    while cursor < len(lines[0]):
        match = pattern.match(lines[0], cursor)
        if not match:
            return [], [QAIssue("ERROR", "unparsed_coauthor", value)]
        refs = re.split(r"\s*,\s*", match[2])
        if any(ref not in affiliations for ref in refs):
            issues.append(QAIssue("ERROR", "unparsed_coauthor", match[0]))
        authors.append(
            ParsedAuthor(
                match[1], " + ".join(affiliations[ref] for ref in refs if ref in affiliations)
            )
        )
        cursor = match.end()
    return authors, issues


def normalize_keywords(
    value: str | None,
    common_words: Iterable[str] = DEFAULT_COMMON_WORDS,
) -> list[str]:
    if value is None or value.strip() in MISSING_VALUES:
        return []
    ordinary = set(common_words)
    keywords: list[str] = []
    for fragment in re.split(r"[,;]", plain_source(value)):
        keyword = clean_whitespace(fragment)
        keyword = re.sub(r"\s+([.,:!?])", r"\1", keyword).rstrip(".;").strip()
        if not keyword or keyword in MISSING_VALUES:
            continue
        first = keyword.split()[0]
        if (
            first[0].isupper()
            and first[1:].islower()
            and first.lower() in ordinary
            and not ORGANIZATION_IN_NAME.search(keyword)
        ):
            keyword = first[0].lower() + keyword[1:]
        keywords.append(keyword)
    return keywords


parse_keywords = normalize_keywords


def affiliation_contains_person_name(value: str) -> bool:
    normalized = clean_whitespace(value)
    for match in PERSON_IN_AFFILIATION_PATTERN.finditer(normalized):
        if not re.search(r"(?:\bим\.?|\bимени)\s+$", normalized[: match.start()], re.I):
            return True
    return False


def _is_plenary_affiliation(
    submission_type: SubmissionType,
    authors: list[ParsedAuthor],
    answer: str | None,
) -> bool:
    return bool(
        submission_type.name.casefold() == "пленарный доклад"
        and len(authors) == 1
        and not authors[0].affiliation
        and answer
        and answer.strip().casefold() not in NO_COAUTHORS
        and "," not in answer
        and ";" not in answer
    )


def _coauthor_answer(answers: Any) -> tuple[str | None, str | None]:
    if isinstance(answers, list):
        for item in answers:
            if not isinstance(item, Mapping):
                continue
            question = item.get("question")
            if (
                isinstance(question, Mapping)
                and question.get("identifier") == COAUTHOR_QUESTION_IDENTIFIER
            ):
                answer = item.get("answer")
                return str(answer) if answer is not None else None, COAUTHOR_QUESTION_IDENTIFIER
    return None, None


def _raw_submission_type_name(raw: Mapping[str, Any]) -> str:
    value = raw.get("submission_type")
    return _localized(value.get("name")) if isinstance(value, Mapping) else ""


def _localized(value: Any, locale: str = "ru") -> str:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, Mapping):
        if isinstance(value.get(locale), str):
            return _text(value[locale])
        return next((_text(x) for x in value.values() if isinstance(x, str) and x.strip()), "")
    return ""


def _text(value: Any) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip() if isinstance(value, str) else ""


def _numeric(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
