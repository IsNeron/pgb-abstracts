from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class Section:
    id: int | str | None
    name: str
    position: int | float | None = None


@dataclass(slots=True)
class SubmissionType:
    id: int | str | None
    name: str


@dataclass(slots=True)
class Author:
    name: str
    affiliation: str
    role: Literal["speaker", "coauthor"]
    email: str | None = None


@dataclass(slots=True)
class Source:
    submission_code: str
    coauthor_answer: str | None = None
    coauthor_question_identifier: str | None = None


@dataclass(slots=True)
class Submission:
    code: str
    state: str
    title: str
    section: Section
    submission_type: SubmissionType
    authors: list[Author]
    keywords: list[str]
    abstract: str
    source: Source
    normalization_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for author in result["authors"]:
            if author.get("email") is None:
                author.pop("email")
        if not self.normalization_warnings:
            result.pop("normalization_warnings")
        return result


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    submission_code: str
    title: str
    track: str
    authors_count: int
    has_keywords: bool
    has_abstract: bool
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    status: Literal["valid", "warning", "error"]
