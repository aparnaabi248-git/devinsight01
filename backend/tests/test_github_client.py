"""GitHub service layer: URL parsing, rate limiting, caching, pagination, errors."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import NotFoundError, RateLimitError, ValidationError
from app.services.github_client import (
    GitHubClient,
    RateLimitState,
    ResponseCache,
    parse_repo_url,
)


class FakeResponse(httpx.Response):
    """httpx.Response with a bound request, which the client needs for raise_for_status."""

    def __init__(
        self, status_code: int, json_body=None, headers=None, text: str | None = None
    ):
        super().__init__(
            status_code,
            json=json_body if json_body is not None else {},
            headers=headers or {},
            request=httpx.Request("GET", "https://api.github.com/test"),
        )
        if text is not None:
            self._text = text


# ------------------------------------------------------------------ URL parsing
@pytest.mark.parametrize(
    "value,expected",
    [
        ("pallets/click", "pallets/click"),
        ("https://github.com/pallets/click", "pallets/click"),
        ("http://github.com/pallets/click/", "pallets/click"),
        ("https://github.com/pallets/click.git", "pallets/click"),
        ("https://github.com/pallets/click/tree/main/src", "pallets/click"),
        ("https://api.github.com/repos/pallets/click", "pallets/click"),
        ("git@github.com:pallets/click.git", "pallets/click"),
        ("  https://github.com/pallets/click  ", "pallets/click"),
    ],
)
def test_parse_repo_url_accepts_valid_inputs(value, expected):
    assert parse_repo_url(value).full_name == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not-a-repo",
        "https://evil.example.com/owner/name",  # SSRF guard
        "https://gitlab.com/owner/name",
        "https://github.com/onlyowner",
    ],
)
def test_parse_repo_url_rejects_invalid_and_non_github(value):
    with pytest.raises(ValidationError):
        parse_repo_url(value)


# ------------------------------------------------------------------ rate limit
def test_rate_limit_state_tracks_exhaustion():
    state = RateLimitState(limit=60, remaining=60)
    assert state.exhausted is False
    state.remaining = 0
    assert state.exhausted is False  # reset is in the past, so not "exhausted yet"
    assert state.as_dict()["remaining"] == 0


def test_request_raises_404_as_not_found():
    """A 404 from GitHub must surface as NotFoundError, not a raw HTTP exception."""
    client = GitHubClient(token="t")
    client._client = _TransportClient(FakeResponse(404, json_body={"message": "Not Found"}))
    with pytest.raises(NotFoundError):
        client.request("GET", "/repos/does-not/exist", use_cache=False)


def test_request_surfaces_rate_limit_error():
    client = GitHubClient(token="t")
    response = FakeResponse(403, text="API rate limit exceeded for user")
    with pytest.raises(RateLimitError):
        client.request("GET", "/repos/a/b", use_cache=False) if False else _raise(
            client, response
        )


def _raise(client: GitHubClient, response: FakeResponse) -> None:
    """Drive the client's error branch with a stubbed transport response."""
    client._client = _TransportClient(response)
    client.request("GET", "/repos/a/b", use_cache=False)


class _TransportClient:
    """Minimal stand-in for httpx.Client that always returns `response`."""

    def __init__(self, response: httpx.Response):
        self.response = response
        self.request_calls = 0

    def request(self, *args, **kwargs):
        self.request_calls += 1
        return self.response


# ---------------------------------------------------------------------- cache
def test_response_cache_round_trip(tmp_path):
    cache = ResponseCache(tmp_path / "cache")
    key = cache._key("GET:/repos/a/b", {"page": 1}, "fingerprint")
    assert cache.get(key)[0] is None  # miss
    cache.put(key, {"full_name": "a/b"}, {"etag": '"abc"'})
    body, meta = cache.get(key)
    assert body == {"full_name": "a/b"}
    assert meta["etag"] == '"abc"'


def test_response_cache_key_depends_on_params_and_identity():
    cache = ResponseCache()
    a = cache._key("GET:/x", {"page": 1}, "anon")
    b = cache._key("GET:/x", {"page": 2}, "anon")
    c = cache._key("GET:/x", {"page": 1}, "token-abc")
    assert a != b and a != c


def test_response_cache_expiry(tmp_path, monkeypatch):
    cache = ResponseCache(tmp_path / "cache")
    key = cache._key("GET:/x", None, "f")
    cache.put(key, {"v": 1}, {})
    assert cache.get(key)[0] is not None
    # Simulate the TTL elapsing.
    import json

    path = cache._path(key)
    envelope = json.loads(path.read_text())
    envelope["stored_at"] = 0
    path.write_text(json.dumps(envelope))
    assert cache.get(key)[0] is None


# -------------------------------------------------------------------- client
def test_replayed_cache_response_is_not_decoded_as_gzip(tmp_path):
    """Regression: a cached body must not inherit GitHub's `content-encoding: gzip`.

    The cache stores re-serialised plain JSON. Replaying the original encoding header
    made httpx attempt to gunzip it and raised DecodingError, which surfaced to users as
    an opaque 500 during ingestion.
    """
    client = GitHubClient(token="t", cache=ResponseCache(tmp_path / "cache"))
    key = client.cache._key("GET:/repos/a/b", None, client.token_fingerprint)
    client.cache.put(
        key,
        {"full_name": "a/b"},
        {
            "etag": '"abc"',
            "headers": {
                "content-encoding": "gzip",
                "content-length": "1234",
                "content-type": "application/json",
            },
        },
    )

    response = client.request("GET", "/repos/a/b")
    assert response.status_code == 200
    assert response.headers.get("X-DevInsight-Cache") == "HIT"
    assert "content-encoding" not in {k.lower() for k in response.headers}
    assert response.json() == {"full_name": "a/b"}


def test_304_without_a_cached_body_retries_unconditionally(tmp_path):
    """A 304 with nothing usable cached must re-ask, not surface as an error."""
    client = GitHubClient(token="t", cache=ResponseCache(tmp_path / "cache"))
    # Prime the cache with metadata only, as an aged-out entry would leave behind.
    key = client.cache._key("GET:/repos/a/b", None, client.token_fingerprint)
    client.cache.put(key, {"full_name": "a/b"}, {"etag": '"abc"'})
    envelope_path = client.cache._path(key)
    envelope = json.loads(envelope_path.read_text())
    envelope["stored_at"] = 0  # expire the entry
    envelope_path.write_text(json.dumps(envelope))

    responses = [
        FakeResponse(304, headers={"ETag": '"abc"'}),
        FakeResponse(200, {"full_name": "a/b"}),
    ]
    transport = _SequenceClient(responses)
    client._client = transport

    response = client.request("GET", "/repos/a/b")
    assert response.status_code == 200
    assert response.json() == {"full_name": "a/b"}
    assert transport.calls == 2, "the second attempt must drop the conditional headers"
    assert "if-none-match" not in {k.lower() for k in transport.last_headers}


class _SequenceClient:
    """Replays a list of responses and records the headers of the last request."""

    def __init__(self, responses: list[httpx.Response]):
        self.responses = responses
        self.index = 0
        self.calls = 0
        self.last_headers: dict = {}

    def request(self, *args, **kwargs):
        self.calls += 1
        self.last_headers = kwargs.get("headers") or {}
        response = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return response


def test_client_does_not_leak_token_in_repr_or_fingerprint():
    client = GitHubClient(token="ghp_supersecret")
    assert "ghp_supersecret" not in client.token_fingerprint
    assert client.token_fingerprint == GitHubClient(token="ghp_supersecret").token_fingerprint
    assert client.token_fingerprint != GitHubClient(token="other").token_fingerprint


def test_authenticated_flag_reflects_token_presence():
    assert GitHubClient(token="abc").authenticated is True
    assert GitHubClient(token=None).authenticated is False


def test_paginate_stops_at_max_items():
    pages = [
        FakeResponse(
            200,
            [{"id": i} for i in range(0, 5)],
            headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
        ),
        FakeResponse(200, [{"id": i} for i in range(5, 10)]),
    ]
    client = GitHubClient(token="t")
    client._client = _PagedClient(pages)
    items = list(client.paginate("/x", per_page=5, max_items=7))
    assert len(items) == 7


class _PagedClient:
    def __init__(self, pages: list[httpx.Response]):
        self.pages = pages
        self.index = 0

    def request(self, *args, **kwargs):
        response = self.pages[min(self.index, len(self.pages) - 1)]
        self.index += 1
        return response
