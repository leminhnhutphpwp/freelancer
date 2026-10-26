"""Async client for the Freelancer.com REST API (auth via Personal Access Token).

All requests carry the ``Freelancer-OAuth-V1`` header with the access token, per
https://developers.freelancer.com docs ("Using Access Tokens").
Array query params use the ``key[]=value`` repeated style that the API expects.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.log import get_logger, sanitize

log = get_logger(__name__)

# Freelancer wraps every successful response as {"status": "success", "request_id": ..., "result": ...}
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FreelancerError(Exception):
    """Error from the Freelancer API.

    ``kind`` is one of: auth, client, not_found, rate_limited, server, network, invalid_response.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        kind: str = "client",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.kind = kind
        self.request_id = request_id

    @property
    def is_auth_failure(self) -> bool:
        return self.kind == "auth"


def flatten_params(params: dict[str, Any] | None) -> list[tuple[str, Any]]:
    """Convert ``{"users": [1, 2]}`` to ``[("users[]", 1), ("users[]", 2)]``; bools to true/false."""
    if not params:
        return []
    flat: list[tuple[str, Any]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            flat.append((key, "true" if value else "false"))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if item is not None:
                    flat.append((f"{key}[]", item))
        else:
            flat.append((key, value))
    return flat


class FreelancerClient:
    """Thin typed wrapper over the endpoints this project needs."""

    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = "https://www.freelancer.com/api",
        timeout: float = 20.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
        retry_sleep: Any = None,
    ) -> None:
        if not access_token:
            raise ValueError("Freelancer access token is not configured")
        self._token = access_token
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {access_token}",
                "freelancer-oauth-v1": access_token,
                "Accept": "application/json",
            },
        )
        self._retry_sleep = retry_sleep or asyncio.sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> FreelancerClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ core

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform one request with bounded retries; return the parsed ``result`` dict."""
        attempt = 0
        last_error: FreelancerError | None = None
        while True:
            attempt += 1
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=flatten_params(params) if params else None,
                    json=json_body,
                    data=form_data,
                )
            except httpx.HTTPError as exc:
                # Network-level failure: safe to retry (request may not have completed).
                last_error = FreelancerError(
                    f"Network error calling Freelancer API: {exc.__class__.__name__}",
                    kind="network",
                )
                log.warning(
                    "freelancer.network_error",
                    extra={
                        "extra_fields": {
                            "method": method,
                            "path": path,
                            "attempt": attempt,
                            "error": str(exc),
                        }
                    },
                )
                if attempt <= self._max_retries:
                    await self._retry_sleep(min(2**attempt, 30))
                    continue
                raise last_error from exc

            if response.status_code in _RETRYABLE_STATUS:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                last_error = self._error_from_response(response)
                log.warning(
                    "freelancer.retryable",
                    extra={
                        "extra_fields": {
                            "method": method,
                            "path": path,
                            "status": response.status_code,
                            "attempt": attempt,
                        }
                    },
                )
                if attempt <= self._max_retries:
                    await self._retry_sleep(retry_after or min(2**attempt, 30))
                    continue
                raise last_error

            if response.status_code >= 400:
                raise self._error_from_response(response)

            try:
                payload = response.json()
            except ValueError as exc:
                raise FreelancerError(
                    "Freelancer API returned non-JSON response",
                    status_code=response.status_code,
                    kind="invalid_response",
                ) from exc

            if not isinstance(payload, dict):
                raise FreelancerError(
                    "Unexpected Freelancer API response shape",
                    status_code=response.status_code,
                    kind="invalid_response",
                )

            status = payload.get("status")
            request_id = payload.get("request_id")
            if status != "success":
                error = payload.get("error") or {}
                raise FreelancerError(
                    str(error.get("detail") or payload.get("message") or "Freelancer API error"),
                    status_code=response.status_code,
                    error_code=error.get("code") or payload.get("error_code"),
                    kind="client",
                    request_id=request_id,
                )
            result = payload.get("result")
            if result is None:
                return {}
            if not isinstance(result, dict):
                raise FreelancerError(
                    "Unexpected Freelancer API result shape",
                    status_code=response.status_code,
                    kind="invalid_response",
                    request_id=request_id,
                )
            return result

    def _error_from_response(self, response: httpx.Response) -> FreelancerError:
        code: str | None = None
        detail: str | None = None
        request_id: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    code = error.get("code")
                    detail = error.get("detail")
                else:
                    detail = payload.get("message")
                request_id = payload.get("request_id")
        except ValueError:
            pass
        status = response.status_code
        if status in (401, 403):
            kind = "auth"
        elif status == 404:
            kind = "not_found"
        elif status == 429:
            kind = "rate_limited"
        elif status >= 500:
            kind = "server"
        else:
            kind = "client"
        message = detail or f"Freelancer API HTTP {status}"
        return FreelancerError(message, status_code=status, error_code=code, kind=kind, request_id=request_id)

    # -------------------------------------------------------------- users API

    async def get_self(self, **projections: bool) -> dict[str, Any]:
        """Authenticated user information. Confirms the token works.

        Accepts boolean projections such as ``jobs=True``, ``display_info=True``,
        ``reputation=True`` (see API docs). Response shape varies by account:
        either a ``users`` list or the flattened user object.
        """
        params = {k: v for k, v in projections.items() if v}
        result = await self.request("GET", "/users/0.1/self/", params=params or None)
        users = result.get("users")
        if isinstance(users, list) and users:
            entry = users[0]
            if isinstance(entry, dict):
                return entry.get("user") if isinstance(entry.get("user"), dict) else entry
        # Some tenants return the user fields inline.
        return {k: v for k, v in result.items() if k != "devices"}

    async def get_user(self, user_id: int, **projections: bool) -> dict[str, Any]:
        result = await self.request(
            "GET",
            f"/users/0.1/users/{user_id}/",
            params={"users": [user_id], **{k: v for k, v in projections.items() if v}},
        )
        users = result.get("users") or []
        return users[0] if users else {}

    # Profiles ------------------------------------------------------------------

    async def list_profiles(self, *, user_id: int | None = None, profile_ids: list[int] | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if profile_ids:
            params["ids"] = profile_ids
        result = await self.request("GET", "/users/0.1/profiles", params=params or None)
        return list(result.get("profiles") or [])

    async def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self.request("POST", "/users/0.1/profiles", json_body=payload)
        return dict(result)

    async def update_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self.request("PUT", "/users/0.1/profiles", json_body=payload)
        return dict(result)

    # Portfolio -----------------------------------------------------------------

    async def list_portfolios(self, user_id: int, *, limit: int = 50, offset: int = 0) -> list[dict]:
        result = await self.request(
            "GET",
            "/users/0.1/portfolios/",
            params={"users": [user_id], "limit": limit, "offset": offset},
        )
        portfolios = result.get("portfolios")
        if portfolios is None:
            portfolios = result.get("user_portfolios") or result.get("results") or []
        items: list[dict] = []
        for entry in portfolios:
            if isinstance(entry, dict):
                # The API nests lists per user in some versions.
                inner = entry.get("portfolios")
                if isinstance(inner, list):
                    items.extend(p for p in inner if isinstance(p, dict))
                else:
                    items.append(entry)
        return items

    # Skills / jobs (user job records) -------------------------------------------

    # The API schema literally uses the key "jobs[]" inside the JSON body.
    async def add_skills(self, job_ids: list[int]) -> dict[str, Any]:
        return dict(await self.request("POST", "/users/0.1/self/jobs", json_body={"jobs[]": job_ids}))

    async def set_skills(self, job_ids: list[int]) -> dict[str, Any]:
        return dict(await self.request("PUT", "/users/0.1/self/jobs", json_body={"jobs[]": job_ids}))

    async def delete_skills(self, job_ids: list[int]) -> dict[str, Any]:
        return dict(await self.request("DELETE", "/users/0.1/self/jobs", json_body={"jobs[]": job_ids}))

    # ------------------------------------------------------------- projects API

    async def search_projects(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Search active projects. ``filters`` maps to documented query params
        (query, project_types, jobs, min_price, max_price, min_avg_price, max_avg_price,
        from_time, sort_field, limit, offset, full_description, job_details, ...)."""
        return await self.request("GET", "/projects/0.1/projects/active/", params=filters)

    async def get_project(self, project_id: int, **projections: bool) -> dict[str, Any]:
        result = await self.request(
            "GET",
            f"/projects/0.1/projects/{project_id}/",
            params={"projects": [project_id], **{k: v for k, v in projections.items() if v}},
        )
        projects = result.get("projects") or []
        return projects[0] if projects else {}

    async def get_self_projects(self) -> list[dict]:
        result = await self.request("GET", "/projects/0.1/self/")
        return list(result.get("projects") or [])

    # ------------------------------------------------------------------- bids

    async def create_bid(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(await self.request("POST", "/projects/0.1/bids/", json_body=payload))

    async def update_bid(self, bid_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(await self.request("PUT", f"/projects/0.1/bids/{bid_id}/", json_body=payload))

    async def bid_action(self, bid_id: int, action: str, **extra: Any) -> dict[str, Any]:
        return dict(
            await self.request(
                "PUT",
                f"/projects/0.1/bids/{bid_id}/",
                form_data={"action": action, **extra},
            )
        )

    async def list_bids(
        self,
        *,
        bidders: list[int] | None = None,
        projects: list[int] | None = None,
        bid_ids: list[int] | None = None,
        statuses: list[str] | None = None,
        frontend_statuses: list[str] | None = None,
        award_statuses: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if bidders:
            params["bidders"] = bidders
        if projects:
            params["projects"] = projects
        if bid_ids:
            params["ids"] = bid_ids
        if statuses:
            params["bid_statuses"] = statuses
        if frontend_statuses:
            params["frontend_bid_statuses"] = frontend_statuses
        if award_statuses:
            params["award_statuses"] = award_statuses
        result = await self.request("GET", "/projects/0.1/bids/", params=params)
        return list(result.get("bids") or [])

    # ------------------------------------------------------------- messaging

    async def list_threads(self, params: dict[str, Any] | None = None) -> list[dict]:
        result = await self.request("GET", "/messages/0.1/threads/", params=params)
        return list(result.get("threads") or [])

    async def list_messages(self, thread_id: int, *, params: dict[str, Any] | None = None) -> list[dict]:
        result = await self.request(
            "GET",
            f"/messages/0.1/threads/{thread_id}/messages/",
            params=params,
        )
        return list(result.get("messages") or [])

    async def create_message(self, thread_id: int, text: str, *, client_message_id: str) -> dict[str, Any]:
        result = await self.request(
            "POST",
            f"/messages/0.1/threads/{thread_id}/messages/",
            json_body={"message": text, "client_message_id": client_message_id},
        )
        return dict(result)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, min(seconds, 60.0))


def safe_error_info(exc: FreelancerError) -> dict[str, Any]:
    """Serializable, token-free error summary for API responses/logs."""
    return sanitize(
        {
            "kind": exc.kind,
            "status_code": exc.status_code,
            "error_code": exc.error_code,
            "request_id": exc.request_id,
            "message": str(exc),
        }
    )
