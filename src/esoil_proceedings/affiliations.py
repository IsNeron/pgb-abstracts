"""Explicit institution aliases, without fuzzy matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping


def clean_whitespace(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def affiliation_key(value: str) -> str:
    value = clean_whitespace(value).casefold()
    value = value.translate(str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"'}))
    return re.sub(r"\s*([.,\"])\s*", r"\1", value)


class AffiliationRegistry:
    def __init__(self, entries: Iterable[Mapping[str, object]] = ()) -> None:
        self.aliases: dict[str, str] = {}
        for entry in entries:
            canonical, aliases = entry.get("canonical"), entry.get("aliases", [])
            if not isinstance(canonical, str) or not canonical.strip():
                raise ValueError("affiliation canonical must be a nonempty string")
            if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
                raise ValueError("affiliation aliases must be a list of strings")
            canonical = clean_whitespace(canonical)
            for alias in [canonical, *aliases]:
                key = affiliation_key(alias)
                if not key:
                    raise ValueError("affiliation alias must not be empty")
                if key in self.aliases and self.aliases[key] != canonical:
                    raise ValueError(f"conflicting affiliation alias: {alias}")
                self.aliases[key] = canonical

    def canonicalize(self, value: str) -> str:
        return self.aliases.get(affiliation_key(value), clean_whitespace(value))

    def split(self, value: str) -> list[str]:
        """Explicit +/;/newline separators outside quotes/brackets.

        A comma splits only before an entire registered institution suffix.
        Addresses, departments and commas inside institution names survive.
        """
        chunks: list[str] = []
        start = depth = 0
        quoted = False
        for index, char in enumerate(value):
            if char in '"«»“”':
                quoted = not quoted
            elif char in "([":
                depth += 1
            elif char in ")]":
                depth = max(0, depth - 1)
            elif not quoted and not depth and char in "+;\n":
                chunks.append(value[start:index])
                start = index + 1
        chunks.append(value[start:])
        result: list[str] = []
        for chunk in chunks:
            suffixes: list[str] = []
            for match in reversed(list(re.finditer(",", chunk))):
                tail = chunk[match.end() :].strip()
                if affiliation_key(tail) in self.aliases:
                    suffixes.insert(0, tail)
                    chunk = chunk[: match.start()]
            result.extend([chunk, *suffixes])
        return [self.canonicalize(x) for x in result if x.strip() not in {"", "-", "–", "—"}]
