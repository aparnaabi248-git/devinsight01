"""The *only* module that talks to GitHub.

Handles authentication, pagination, ETag-based caching, rate-limit backoff, retries and
error translation. Route handlers and the ingestion pipeline must go through this.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.errors import GitHubError, NotFoundError, RateLimitError, ValidationError
from app.core.logging import get_logger

log = get_logger("github.client")

# Only GitHub hosts are ever accepted — prevents SSRF via the repository URL field.
ALLOWED_HOSTS = {"github.com", "www.github.com", "api.github.com"}
REPO_URL_RE = re.compile(r"^(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$")

RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}


@dataclass
class RateLimitState:
    limit: int = 60
    remaining: int = 60
    reset: datetime = field(default_factory=lambda: datetime.now(UTC))
    calls_made: int = 0

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 1 and self.reset > datetime.now(UTC)

    def wait_seconds(self) -> int:
        return max(1, int((self.reset - datetime.now(UTC)).total_seconds()) + 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "reset": self.reset.isoformat(),
            "calls_made": self.calls_made,
        }


class ResponseCache:
    """ETag/Last-Modified aware disk cache. Survives process restarts, safe for containers."""

    def __init__(self, root: Path | None = None):
        self.root = root or (settings.RAW_DIR / "_http_cache")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _key(url: str, params: dict | None, token_fingerprint: str) -> str:
        raw = url + "|" + json.dumps(params or {}, sort_keys=True) + "|" + token_fingerprint
        return hashlib.sha256(raw.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> tuple[dict | None, dict | None]:
        """Return (cached_body, cache_meta). Body is None on miss."""
        p = self._path(key)
        if not p.exists():
            return None, None
        try:
            envelope = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - envelope["stored_at"] > settings.GITHUB_CACHE_TTL:
                return None, envelope.get("meta")
            return envelope["body"], envelope.get("meta")
        except (json.JSONDecodeError, KeyError, OSError):
            return None, None

    def put(self, key: str, body: Any, meta: dict | None, ttl: int | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"stored_at": time.time(), "body": body, "meta": meta or {}}
        with self._lock:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(envelope, default=str), encoding="utf-8")
            tmp.replace(p)
        if ttl is not None:  # honour forced TTL from the caller
            env = json.loads(p.read_text(encoding="utf-8"))
            env["stored_at"] = time.time() - settings.GITHUB_CACHE_TTL + max(0, ttl)
            p.write_text(json.dumps(env, default=str), encoding="utf-8")

    def clear(self) -> int:
        n = 0
        for f in self.root.rglob("*.json"):
            f.unlink(missing_ok=True)
            n += 1
        return n


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def api_base(self) -> str:
        return f"/repos/{self.owner}/{self.name}"


def parse_repo_url(url: str) -> RepoRef:
    """Accept `owner/name`, a full github.com URL, or a clone URL. Reject anything else."""
    raw = (url or "").strip()
    if not raw:
        raise ValidationError("repository URL must not be empty")

    if "://" in raw or raw.startswith("git@") or raw.startswith("ssh://"):
        if raw.startswith("git@"):
            # `git@github.com:owner/name.git` — the path follows a colon, not a slash.
            host, _, path = "github.com", "", raw[4:]
            if ":" in path:
                path = path.split(":", 1)[1]
        else:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
            path = parsed.path
        if host not in ALLOWED_HOSTS:
            raise ValidationError(
                f"only github.com repositories are supported (got host '{host or 'unknown'}')"
            )
    else:
        path = raw

    path = path.strip("/")
    if path.startswith("repos/"):  # tolerate an API URL
        path = path[len("repos/") :]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ValidationError(
            f"'{url}' is not a valid repository — expected 'owner/name' or a github.com URL"
        )
    m = REPO_URL_RE.match(f"{parts[0]}/{parts[1]}")
    if not m:
        raise ValidationError(f"'{url}' is not a valid repository identifier")
    return RepoRef(owner=m.group("owner"), name=m.group("name"))


class GitHubClient:
    """Rate-limit aware, retrying, ETag-caching GitHub REST client."""

    def __init__(
        self,
        token: str | None = None,
        cache: ResponseCache | None = None,
        client: httpx.Client | None = None,
    ):
        self.token = token if token is not None else settings.GITHUB_TOKEN
        self.cache = cache if cache is not None else ResponseCache()
        self.rate = RateLimitState(
            limit=5000 if self.token else 60, remaining=5000 if self.token else 60
        )
        self._client = client
        self._owns_client = client is None

    # -------------------------------------------------------------- lifecycle
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "DevInsight/1.0",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.Client(
                base_url=settings.GITHUB_API_URL,
                headers=headers,
                timeout=settings.GITHUB_TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
        self._client = None

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def token_fingerprint(self) -> str:
        """Identity of the credential, never the credential itself (safe for cache keys/logs)."""
        return hashlib.sha256((self.token or "anon").encode()).hexdigest()[:12]

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------ low level
    def _update_rate(self, response: httpx.Response) -> None:
        lim = response.headers.get("X-RateLimit-Limit")
        rem = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if lim and rem:
            self.rate.limit, self.rate.remaining = int(lim), int(rem)
        if reset:
            self.rate.reset = datetime.fromtimestamp(int(reset), tz=UTC)
        elif not lim:
            self.rate.calls_made += 1
            self.rate.remaining = max(0, self.rate.remaining - 1)

    # Headers that describe the *original wire* body must not be replayed: the cached
    # body is re-serialised plain JSON, so a stale `content-encoding: gzip` would make
    # httpx try to gunzip it and fail with a DecodingError.
    REPLAY_UNSAFE_HEADERS = {
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "connection",
    }

    @classmethod
    def _replay_headers(cls, stored: dict | None, extra: dict | None = None) -> dict[str, str]:
        headers = {
            k: v
            for k, v in (stored or {}).items()
            if k.lower() not in cls.REPLAY_UNSAFE_HEADERS
        }
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        use_cache: bool = True,
        force_ttl: int | None = None,
    ) -> httpx.Response:
        cache_key = self.cache._key(f"{method}:{path}", params, self.token_fingerprint)
        meta: dict | None = None

        if use_cache and method == "GET":
            body, cached_meta = self.cache.get(cache_key)
            if body is not None:
                cached_meta = cached_meta or {}
                headers = self._replay_headers(
                    cached_meta.get("headers"),
                    {
                        "X-DevInsight-Cache": "HIT",
                        "X-DevInsight-Status": str(cached_meta.get("status", 200)),
                    },
                )
                try:
                    return httpx.Response(
                        int(cached_meta.get("status", 200)),
                        content=json.dumps(body).encode(),
                        headers=headers,
                    )
                except ValueError:  # pragma: no cover
                    pass
            meta = cached_meta

        if self.rate.exhausted:
            wait = self.rate.wait_seconds()
            if wait <= 65:
                log.warning(
                    "github rate limit exhausted, sleeping",
                    extra={"context": {"wait_seconds": wait}},
                )
                time.sleep(wait)
            else:
                raise RateLimitError(
                    "GitHub API rate limit exhausted. Configure GITHUB_TOKEN to raise the "
                    f"limit from {self.rate.limit} to 5000 requests/hour.",
                    context={"reset_at": self.rate.reset.isoformat(), "wait_seconds": wait},
                )

        last_error: Exception | None = None
        force_full = False  # set when a 304 arrives but there is nothing cached to serve
        for attempt in range(1, settings.GITHUB_MAX_RETRIES + 1):
            try:
                request_headers: dict[str, str] = {}
                if meta and not force_full:
                    if meta.get("etag"):
                        request_headers["If-None-Match"] = meta["etag"]
                    if meta.get("last_modified"):
                        request_headers["If-Modified-Since"] = meta["last_modified"]

                response = self.client.request(
                    method, path, params=params, headers=request_headers
                )
                self._update_rate(response)

                if response.status_code == 304:
                    # Not Modified: the conditional request matched a stored ETag.
                    cached, cached_meta = self.cache.get(cache_key)
                    if cached is not None:
                        headers = self._replay_headers(
                            (cached_meta or {}).get("headers"),
                            {"X-DevInsight-Cache": "REVALIDATED"},
                        )
                        return httpx.Response(
                            200, content=json.dumps(cached).encode(), headers=headers
                        )
                    # Nothing usable cached (e.g. the entry aged out of the TTL window).
                    # Re-ask unconditionally instead of surfacing 304 as an error.
                    meta = None
                    force_full = True
                    continue

                if response.status_code == 404:
                    # With an exhausted quota it is genuinely impossible to tell a
                    # missing repository from one we are simply not allowed to see, so
                    # say so rather than asserting the repository does not exist.
                    hint = (
                        " (the API quota is exhausted, so GitHub may be masking this "
                        "repository rather than reporting it missing - add "
                        "GITHUB_TOKEN, or ingest it with scripts/add_repo.py, which "
                        "uses the git transport and is not rate limited)"
                        if self.rate.remaining <= 0
                        else ""
                    )
                    raise NotFoundError(
                        f"GitHub resource not found: {path}{hint}",
                        code="github_not_found",
                        context={"rate_remaining": self.rate.remaining},
                    )
                if response.status_code == 403 and "rate limit" in response.text.lower():
                    raise RateLimitError(
                        "GitHub API rate limit exceeded. Add GITHUB_TOKEN to your environment.",
                        context={"reset_at": self.rate.reset.isoformat()},
                    )
                if response.status_code in RETRYABLE_STATUS:
                    sleep_for = min(2 ** (attempt - 1), 16)
                    if response.status_code == 403:
                        sleep_for = max(sleep_for, 5)
                    log.warning(
                        "github retryable error",
                        extra={
                            "context": {
                                "path": path,
                                "status": response.status_code,
                                "attempt": attempt,
                                "sleep": sleep_for,
                            }
                        },
                    )
                    time.sleep(sleep_for)
                    last_error = GitHubError(
                        f"GitHub returned {response.status_code} for {path}"
                    )
                    continue

                response.raise_for_status()

                if use_cache and method == "GET":
                    try:
                        payload = response.json()
                        self.cache.put(
                            cache_key,
                            payload,
                            {
                                "etag": response.headers.get("ETag"),
                                "last_modified": response.headers.get("Last-Modified"),
                                "headers": dict(response.headers),
                                "status": response.status_code,
                            },
                            ttl=force_ttl,
                        )
                    except json.JSONDecodeError:
                        pass
                return response

            except (RateLimitError, NotFoundError):
                raise
            except httpx.HTTPStatusError as exc:
                last_error = GitHubError(
                    f"GitHub API error {exc.response.status_code} for {path}"
                )
            except httpx.HTTPError as exc:
                last_error = GitHubError(f"GitHub connection error for {path}: {exc}")
                log.warning(
                    "github connection error",
                    extra={"context": {"path": path, "error": str(exc)}},
                )

        raise last_error or GitHubError(f"GitHub request failed for {path}")

    # ------------------------------------------------------------ high level
    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw).json()

    def paginate(
        self,
        path: str,
        *,
        params: dict | None = None,
        per_page: int | None = None,
        max_items: int | None = None,
        max_pages: int = 50,
    ) -> Iterator[dict]:
        """Yield every item across pages, stopping at `max_items` or `max_pages`.

        Pagination uses Link-header `rel="next"` when GitHub provides it, which is the
        only reliable way to page large collections without guessing page numbers.
        """
        per_page = per_page or settings.GITHUB_PER_PAGE
        page = 1
        emitted = 0
        while page <= max_pages:
            query = {"per_page": per_page, "page": page, **(params or {})}
            response = self.request("GET", path, params=query)
            payload = response.json()
            if not isinstance(payload, list):
                yield payload
                return
            if not payload:
                return
            for item in payload:
                yield item
                emitted += 1
                if max_items is not None and emitted >= max_items:
                    return
            if "next" not in response.headers.get("Link", ""):
                return
            page += 1

    # ------------------------------------------------------------ endpoints
    def get_repository(self, ref: RepoRef | str) -> dict:
        ref = ref if isinstance(ref, RepoRef) else parse_repo_url(str(ref))
        return self.get(ref.api_base, force_ttl=300)

    def get_commits(
        self, ref: RepoRef, *, since: datetime | None = None, max_items: int | None = None
    ) -> list[dict]:
        params = {"since": since.isoformat() + "Z"} if since else None
        return list(
            self.paginate(
                f"{ref.api_base}/commits",
                params=params,
                max_items=max_items or settings.INGEST_MAX_COMMITS,
                max_pages=40,
            )
        )

    def get_issues(
        self, ref: RepoRef, *, state: str = "all", max_items: int | None = None
    ) -> list[dict]:
        """GitHub's /issues endpoint returns PRs too; `pull_request` key identifies them."""
        return list(
            self.paginate(
                f"{ref.api_base}/issues",
                params={"state": state, "sort": "created", "direction": "desc"},
                max_items=max_items or settings.INGEST_MAX_ISSUES,
                max_pages=20,
            )
        )

    def get_pull_requests(
        self, ref: RepoRef, *, state: str = "all", max_items: int | None = None
    ) -> list[dict]:
        return list(
            self.paginate(
                f"{ref.api_base}/pulls",
                params={"state": state, "sort": "created", "direction": "desc"},
                max_items=max_items,
                max_pages=15,
            )
        )

    def get_releases(self, ref: RepoRef, *, max_items: int = 200) -> list[dict]:
        return list(
            self.paginate(f"{ref.api_base}/releases", max_items=max_items, max_pages=5)
        )

    def get_contributors(self, ref: RepoRef, *, max_items: int = 500) -> list[dict]:
        return list(
            self.paginate(f"{ref.api_base}/contributors", max_items=max_items, max_pages=5)
        )

    def get_issue_comments(
        self, ref: RepoRef, number: int, max_items: int = 100
    ) -> list[dict]:
        return list(
            self.paginate(
                f"{ref.api_base}/issues/{number}/comments", max_items=max_items, max_pages=3
            )
        )

    def get_rate_limit(self) -> dict[str, Any]:
        try:
            data = self.get("/rate_limit", use_cache=False, force_ttl=0)
            core = data.get("resources", {}).get("core", {})
            return {
                "limit": core.get("limit", self.rate.limit),
                "remaining": core.get("remaining", self.rate.remaining),
                "reset": core.get("reset"),
                "authenticated": self.authenticated,
            }
        except GitHubError:
            return self.rate.as_dict() | {"authenticated": self.authenticated}

    def search_code_commits(self, ref: RepoRef, query: str) -> list[dict]:
        return list(
            self.paginate(
                "/search/commits",
                params={"q": f"{query} repo:{ref.full_name}", "per_page": 30},
                max_items=100,
                max_pages=2,
            )
        )


_client: GitHubClient | None = None
_client_lock = threading.Lock()


def get_github_client() -> GitHubClient:
    """Process-wide singleton so the HTTP pool and ETag cache are shared."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = GitHubClient(
                    token=os.getenv("GITHUB_TOKEN") or settings.GITHUB_TOKEN
                )
    return _client
