"""GitHub REST + GraphQL client.

Small on purpose: retries, rate-limit awareness, and typed errors. Anything
returned by this client is untrusted upstream content - it is data, never
instructions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from repo_troubleshooter.config import Settings, get_settings

USER_AGENT = "repo-troubleshooter/0.1 (+evidence-constrained troubleshooting agent)"


class GitHubError(RuntimeError):
    pass


class GitHubAuthError(GitHubError):
    pass


class GitHubRateLimited(GitHubError):
    def __init__(self, reset_in: float) -> None:
        super().__init__(f"GitHub rate limit hit; resets in {reset_in:.0f}s")
        self.reset_in = reset_in


@dataclass
class RateBudget:
    remaining: int | None = None
    limit: int | None = None
    reset_at: float | None = None

    def seconds_until_reset(self) -> float:
        if self.reset_at is None:
            return 0.0
        return max(0.0, self.reset_at - time.time())


class GitHubClient:
    def __init__(self, settings: Settings | None = None, token: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.token = token if token is not None else self.settings.resolved_github_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.Client(
            base_url=self.settings.github_api_url,
            headers=headers,
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=True,
        )
        self.rest_budget = RateBudget()
        self.graphql_budget = RateBudget()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- low level --------------------------------------------------------

    def _absorb_rate_headers(self, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        limit = response.headers.get("x-ratelimit-limit")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining is not None:
            self.rest_budget.remaining = int(remaining)
        if limit is not None:
            self.rest_budget.limit = int(limit)
        if reset is not None:
            self.rest_budget.reset_at = float(reset)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.settings.http_max_retries):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:  # network flake -> retry
                last_error = exc
                time.sleep(min(2**attempt, 30))
                continue

            self._absorb_rate_headers(response)

            if response.status_code in (401, 403) and "rate limit" in response.text.lower():
                wait = self.rest_budget.seconds_until_reset()
                if wait > 0 and attempt < self.settings.http_max_retries - 1:
                    time.sleep(min(wait + 1, 90))
                    continue
                raise GitHubRateLimited(wait)
            if response.status_code == 401:
                raise GitHubAuthError("GitHub rejected the token (401).")
            if response.status_code in (429, 502, 503, 504):
                time.sleep(min(2**attempt, 30))
                continue
            return response

        raise GitHubError(f"{method} {url} failed after retries: {last_error}")

    # --- REST -------------------------------------------------------------

    def rest(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._request("GET", path, params=params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(f"GET {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    def rest_paginated(
        self, path: str, params: dict[str, Any] | None = None, max_pages: int = 100
    ) -> list[Any]:
        items: list[Any] = []
        page_params = dict(params or {})
        page_params.setdefault("per_page", 100)
        url: str | None = path
        pages = 0
        while url and pages < max_pages:
            response = self._request("GET", url, params=page_params if pages == 0 else None)
            if response.status_code == 404:
                break
            if response.status_code >= 400:
                raise GitHubError(f"GET {url} -> {response.status_code}: {response.text[:300]}")
            payload = response.json()
            if isinstance(payload, list):
                items.extend(payload)
            else:
                items.append(payload)
            url = response.links.get("next", {}).get("url")
            pages += 1
        return items

    # --- GraphQL ----------------------------------------------------------

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise GitHubAuthError(
                "GitHub GraphQL requires a token. Set RT_GITHUB_TOKEN or run `gh auth login`."
            )
        response = self._request(
            "POST", "/graphql", json={"query": query, "variables": variables or {}}
        )
        if response.status_code >= 400:
            raise GitHubError(f"GraphQL -> {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in payload["errors"])
            if "rate limit" in messages.lower():
                raise GitHubRateLimited(self.graphql_budget.seconds_until_reset())
            raise GitHubError(f"GraphQL errors: {messages}")
        data = payload.get("data") or {}
        if rate := data.get("rateLimit"):
            self.graphql_budget.remaining = rate.get("remaining")
            self.graphql_budget.limit = rate.get("limit")
        return data
