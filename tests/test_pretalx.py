from __future__ import annotations

import io
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from esoil_proceedings.pretalx import PretalxAuthError, PretalxClient, PretalxError


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.status = 200
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_pagination_and_expand_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRETALX_API_TOKEN", raising=False)
    opener = FakeOpener(
        [
            FakeResponse(
                {
                    "results": [{"code": "ONE", "untouched": {"x": 1}}],
                    "next": "http://conf.example/api/events/pgb2026/submissions/?page=2",
                }
            ),
            FakeResponse({"results": [{"code": "TWO"}], "next": None}),
        ]
    )
    result = PretalxClient("https://conf.example", "pgb2026", opener=opener).fetch_submissions()

    assert result == [{"code": "ONE", "untouched": {"x": 1}}, {"code": "TWO"}]
    first_url = opener.requests[0].full_url
    query = parse_qs(urlsplit(first_url).query)
    assert query["page_size"] == ["100"]
    assert query["expand"] == [
        "speakers",
        "track",
        "submission_type",
        "answers",
        "answers.question",
    ]
    assert len(opener.requests) == 2
    assert opener.requests[1].full_url.startswith("https://conf.example/")


def test_request_without_token_has_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRETALX_API_TOKEN", raising=False)
    opener = FakeOpener([FakeResponse({"results": [], "next": None})])
    PretalxClient("https://conf.example", "event", opener=opener).fetch_submissions()

    assert opener.requests[0].get_header("Authorization") is None


def test_request_uses_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRETALX_API_TOKEN", "very-secret")
    opener = FakeOpener([FakeResponse({"results": [], "next": None})])
    PretalxClient("https://conf.example", "event", opener=opener).fetch_submissions()

    assert opener.requests[0].get_header("Authorization") == "Token very-secret"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_not_retried(status: int) -> None:
    headers = Message()
    error = HTTPError("https://conf.example", status, "denied", headers, io.BytesIO())
    opener = FakeOpener([error])

    with pytest.raises(PretalxAuthError, match=str(status)):
        PretalxClient("https://conf.example", "event", opener=opener).fetch_submissions()
    assert len(opener.requests) == 1


def test_server_error_is_retried() -> None:
    headers = Message()
    error = HTTPError("https://conf.example", 503, "later", headers, io.BytesIO())
    opener = FakeOpener([error, FakeResponse({"results": [], "next": None})])
    delays: list[float] = []

    PretalxClient(
        "https://conf.example", "event", opener=opener, sleep=delays.append
    ).fetch_submissions()

    assert delays == [1.0]
    assert len(opener.requests) == 2


def test_pagination_cannot_send_token_to_another_origin() -> None:
    opener = FakeOpener(
        [FakeResponse({"results": [], "next": "https://evil.example/steal"})]
    )

    with pytest.raises(PretalxError, match="different origin"):
        PretalxClient(
            "https://conf.example", "event", token="secret", opener=opener
        ).fetch_submissions()
    assert len(opener.requests) == 1


def test_fetch_speakers_uses_paginated_speaker_endpoint() -> None:
    opener = FakeOpener(
        [FakeResponse({"results": [{"code": "SPEAKER", "email": "a@example.org"}], "next": None})]
    )

    result = PretalxClient(
        "https://conf.example", "event", token="secret", opener=opener
    ).fetch_speakers()

    assert result == [{"code": "SPEAKER", "email": "a@example.org"}]
    assert "/api/events/event/speakers/" in opener.requests[0].full_url
    assert "page_size=100" in opener.requests[0].full_url
    assert opener.requests[0].get_header("Authorization") == "Token secret"
