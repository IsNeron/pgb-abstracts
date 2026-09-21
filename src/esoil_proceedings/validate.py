from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .models import Submission, ValidationRecord

SUSPICIOUS_INITIALS = re.compile(r"(?:^|\s)[A-ZА-ЯЁ],[A-ZА-ЯЁ]\.", re.IGNORECASE)
SPEAKER_OPTIONAL_TRACKS = {"научно-практические мероприятия"}


def validate_submissions(submissions: Iterable[Submission]) -> list[ValidationRecord]:
    items = list(submissions)
    title_counts = Counter(item.title.strip().casefold() for item in items if item.title.strip())
    return [_validate(item, title_counts) for item in items]


def _validate(submission: Submission, title_counts: Counter[str]) -> ValidationRecord:
    errors: list[str] = []
    warnings = list(submission.normalization_warnings)
    speakers = [author for author in submission.authors if author.role == "speaker" and author.name]

    if not submission.title:
        errors.append("missing title")
    elif _is_all_caps(submission.title):
        warnings.append("title is all caps")
    if not submission.abstract:
        errors.append("missing abstract")
    speaker_is_required = submission.section.name.strip().casefold() not in SPEAKER_OPTIONAL_TRACKS
    if not speakers and speaker_is_required:
        errors.append("missing speaker")
    if not submission.section.name:
        errors.append("missing track")
    if not submission.keywords:
        warnings.append("missing keywords")

    seen_authors: set[str] = set()
    duplicates: set[str] = set()
    for author in submission.authors:
        normalized_name = " ".join(author.name.split()).casefold()
        if normalized_name:
            if normalized_name in seen_authors:
                duplicates.add(author.name)
            seen_authors.add(normalized_name)
        if not author.affiliation:
            warnings.append(f"missing affiliation: {author.name or '[unnamed author]'}")
        elif len(author.affiliation.strip()) < 4:
            warnings.append(f"affiliation too short: {author.name or '[unnamed author]'}")
        if SUSPICIOUS_INITIALS.search(author.name):
            warnings.append(f"suspicious name punctuation: {author.name}")
    for name in sorted(duplicates, key=str.casefold):
        warnings.append(f"duplicate author: {name}")

    normalized_title = submission.title.strip().casefold()
    if normalized_title and title_counts[normalized_title] > 1:
        warnings.append("duplicate title")

    status = "error" if errors else "warning" if warnings else "valid"
    return ValidationRecord(
        submission_code=submission.code,
        title=submission.title,
        track=submission.section.name,
        authors_count=len(submission.authors),
        has_keywords=bool(submission.keywords),
        has_abstract=bool(submission.abstract),
        warnings=tuple(warnings),
        errors=tuple(errors),
        status=status,
    )


def _is_all_caps(value: str) -> bool:
    letters = [character for character in value if character.isalpha()]
    return bool(letters) and all(not character.islower() for character in letters)
