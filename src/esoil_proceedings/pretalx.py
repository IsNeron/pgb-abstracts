from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import OpenerDirector, Request, build_opener


class PretalxError(RuntimeError):
    """Base exception for Pretalx download failures."""


class PretalxAuthError(PretalxError):
    """The API rejected the request credentials."""


class PretalxRateLimitError(PretalxError):
    """The API kept rate-limiting requests after all retries."""


class Response(Protocol):
    status: int
    headers: Message | Mapping[str, str]

    def read(self) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *args: object) -> None: ...


@dataclass(slots=True)
class PretalxClient:
    base_url: str
    event: str
    token: str | None = None
    timeout: float = 30.0
    max_attempts: int = 4
    opener: OpenerDirector | Any = None
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.token is None:
            self.token = os.environ.get("PRETALX_API_TOKEN") or None
        if self.opener is None:
            self.opener = build_opener()
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")

    @property
    def submissions_url(self) -> str:
        event = quote(self.event, safe="")
        return f"{self.base_url}/api/events/{event}/submissions/"

    def fetch_submissions(self) -> list[dict[str, Any]]:
        query = urlencode(
            [
                ("page_size", "100"),
                ("expand", "speakers"),
                ("expand", "track"),
                ("expand", "submission_type"),
                ("expand", "answers"),
                ("expand", "answers.question"),
            ]
        )
        return self._fetch_paginated(f"{self.submissions_url}?{query}")

    def fetch_speakers(self) -> list[dict[str, Any]]:
        event = quote(self.event, safe="")
        url = f"{self.base_url}/api/events/{event}/speakers/?page_size=100"
        return self._fetch_paginated(url)

    def _fetch_paginated(self, initial_url: str) -> list[dict[str, Any]]:
        next_url: str | None = initial_url
        results_all: list[dict[str, Any]] = []
        visited: set[str] = set()

        while next_url:
            next_url = self._safe_pagination_url(next_url)
            self._ensure_same_origin(next_url)
            if next_url in visited:
                raise PretalxError(f"Pagination cycle detected at {next_url}")
            visited.add(next_url)
            page = self._request_json(next_url)
            results = page.get("results")
            if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
                raise PretalxError("Pretalx response has no valid 'results' list")
            results_all.extend(results)
            raw_next = page.get("next")
            if raw_next is not None and not isinstance(raw_next, str):
                raise PretalxError("Pretalx response has an invalid 'next' value")
            next_url = raw_next
        return results_all

    def _request_json(self, url: str) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "esoil-proceedings/0.1"}
        if self.token:
            headers["Authorization"] = f"Token {self.token}"

        for attempt in range(self.max_attempts):
            request = Request(url, headers=headers)
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    if not isinstance(data, dict):
                        raise PretalxError("Pretalx response root is not an object")
                    return data
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    hint = "Set PRETALX_API_TOKEN" if not self.token else "Check PRETALX_API_TOKEN"
                    raise PretalxAuthError(f"Pretalx returned HTTP {exc.code}. {hint}.") from exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable:
                    raise PretalxError(f"Pretalx returned HTTP {exc.code}") from exc
                if attempt == self.max_attempts - 1:
                    if exc.code == 429:
                        raise PretalxRateLimitError(
                            f"Pretalx still returned HTTP 429 after {self.max_attempts} attempts"
                        ) from exc
                    raise PretalxError(
                        f"Pretalx still returned HTTP {exc.code} after {self.max_attempts} attempts"
                    ) from exc
                self.sleep(self._retry_delay(attempt, exc.headers))
            except (URLError, TimeoutError) as exc:
                if attempt == self.max_attempts - 1:
                    raise PretalxError(
                        f"Pretalx request failed after {self.max_attempts} attempts: {exc}"
                    ) from exc
                self.sleep(2**attempt)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise PretalxError("Pretalx returned invalid JSON") from exc
        raise AssertionError("retry loop ended unexpectedly")

    def _safe_pagination_url(self, url: str) -> str:
        absolute = urljoin(f"{self.base_url}/", url)
        expected = urlsplit(self.base_url)
        actual = urlsplit(absolute)
        # Some Pretalx installations behind a TLS-terminating proxy emit http://
        # pagination links. Never send a token over that downgrade; upgrade the
        # URL only when the authority is exactly the configured HTTPS authority.
        is_safe_upgrade = (
            expected.scheme == "https"
            and actual.scheme == "http"
            and actual.netloc == expected.netloc
        )
        if is_safe_upgrade:
            return urlunsplit(("https", actual.netloc, actual.path, actual.query, actual.fragment))
        return absolute

    def _ensure_same_origin(self, url: str) -> None:
        expected = urlsplit(self.base_url)
        actual = urlsplit(url)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            raise PretalxError("Refusing to follow pagination to a different origin")

    @staticmethod
    def _retry_delay(attempt: int, headers: Mapping[str, str] | Message | None) -> float:
        if headers:
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    return min(max(float(retry_after), 0.0), 60.0)
                except ValueError:
                    pass
        return float(2**attempt)
