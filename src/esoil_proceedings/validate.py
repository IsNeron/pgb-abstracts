from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .affiliations import affiliation_key
from .models import QAIssue, Submission, ValidationRecord
from .normalize import (
    ORGANIZATION_IN_NAME,
    affiliation_contains_person_name,
    normalize_person_name,
)
from .richtext import richtext_plain, validate_richtext

SPEAKER_OPTIONAL_TRACKS = {"научно-практические мероприятия"}
PLACEHOLDER_EMAILS = {"address@address.ru", "test@test.ru", "example@example.com"}
REFERENCES_HEADING = re.compile(
    r"(?im)^\s*(?:список\s+(?:использованной\s+)?литературы|литературные источники|"
    r"литература|references|bibliography)\s*[:.]?\s*$"
)
NUMERIC_CITATION = re.compile(r"\[\d+(?:\s*[,;–-]\s*\d+)*\]")
AUTHOR_YEAR_CITATION = re.compile(
    r"\([^()\n]*[A-Za-zА-Яа-яЁё][^()\n]*\b(?:19|20)\d{2}[a-zа-я]?[^()\n]*\)"
)


def validate_submissions(submissions: Iterable[Submission]) -> list[ValidationRecord]:
    items = list(submissions)
    counts = Counter(item.title.strip().casefold() for item in items if item.title.strip())
    return [_validate(item, counts) for item in items]


def _validate(submission: Submission, title_counts: Counter[str]) -> ValidationRecord:
    issues = list(submission.normalization_issues)

    def add(kind: str, value: str = "", severity: str = "WARNING") -> None:
        issues.append(QAIssue(severity, kind, value))

    if not submission.title:
        add("missing_title", severity="ERROR")
    elif _is_all_caps(submission.title):
        add("title_is_all_caps", submission.title)
    try:
        validate_richtext(submission.abstract)
        abstract = richtext_plain(submission.abstract)
    except (ValueError, KeyError, TypeError) as exc:
        add("invalid_richtext", str(exc), "ERROR")
        abstract = ""
    if not abstract.strip():
        add("missing_abstract")
    if not submission.section.name:
        add("missing_track", severity="ERROR")
    if not submission.keywords:
        add("missing_keywords")
    for keyword in submission.keywords:
        if re.search(
            r"[*<>]|\s[.,;:]|\.{2,}|\b[а-яё]+[А-ЯЁ][а-яё]+", keyword
        ) or keyword.strip() in {"", "-", "–", "—"}:
            add("suspicious_keyword", keyword)
    if (
        not any(a.role == "speaker" and a.name for a in submission.authors)
        and submission.section.name.strip().casefold() not in SPEAKER_OPTIONAL_TRACKS
    ):
        add("missing_speaker", severity="ERROR")
    for issue in submission.normalization_issues:
        if issue.type == "unparsed_coauthor" and ORGANIZATION_IN_NAME.search(
            issue.value.partition(",")[0]
        ):
            add("suspicious_author_affiliation_leak", issue.value)
    seen_names: set[str] = set()
    for author in submission.authors:
        if not author.name:
            add("author_without_name", severity="ERROR")
        if ORGANIZATION_IN_NAME.search(author.name):
            add("suspicious_author_affiliation_leak", author.name)
        name, canonical = normalize_person_name(author.name)
        if not canonical:
            add("noncanonical_person_name", author.name)
        if name.casefold() in seen_names:
            add("duplicate_author", author.name)
        seen_names.add(name.casefold())
        if not author.affiliations:
            add("missing_affiliation", author.name)
        if any(ref not in submission.affiliations for ref in author.affiliations):
            add("invalid_affiliation_reference", author.name, "ERROR")
        if len(author.affiliations) != len(set(author.affiliations)):
            add("duplicate_affiliation", author.name)
        if author.email and author.email.strip().casefold() in PLACEHOLDER_EMAILS:
            add("placeholder_email", author.email)
    seen_affiliations: set[str] = set()
    for ref, affiliation in submission.affiliations.items():
        if not isinstance(ref, int) or ref < 1:
            add("invalid_affiliation_id", str(ref), "ERROR")
        key = affiliation_key(affiliation)
        if key in seen_affiliations:
            add("duplicate_affiliation", affiliation)
        seen_affiliations.add(key)
        if len(affiliation) < 4:
            add("affiliation_too_short", affiliation)
        if affiliation_contains_person_name(affiliation):
            add("suspicious_affiliation_contains_person_name", affiliation)
    if title_counts[submission.title.strip().casefold()] > 1:
        add("duplicate_title", submission.title)
    references = REFERENCES_HEADING.search(abstract)
    body = abstract[: references.start()] if references else abstract
    numeric = bool(NUMERIC_CITATION.search(body))
    author_year = bool(AUTHOR_YEAR_CITATION.search(body))
    if references and not numeric and not author_year:
        add("references_present_but_no_intext_citations")
    if numeric and author_year:
        add("mixed_citation_style")
    issues = list(dict.fromkeys(issues))
    warnings = tuple(_message(issue) for issue in issues if issue.severity == "WARNING")
    errors = tuple(_message(issue) for issue in issues if issue.severity == "ERROR")
    return ValidationRecord(
        submission_code=submission.code,
        title=submission.title,
        track=submission.section.name,
        authors_count=len(submission.authors),
        has_keywords=bool(submission.keywords),
        has_abstract=bool(abstract.strip()),
        warnings=warnings,
        errors=errors,
        status="error" if errors else "warning" if warnings else "valid",
        issues=tuple(issues),
    )


def _message(issue: QAIssue) -> str:
    return issue.type + (f": {issue.value}" if issue.value else "")


def _is_all_caps(value: str) -> bool:
    letters = [character for character in value if character.isalpha()]
    return bool(letters) and all(not character.islower() for character in letters)
