"""Trusted Syntara client for the Execution Plane work-item API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from syntara.core.tls.http_client import build_internal_http_client
from syntara.workflows.exceptions import WorkflowError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import TracebackType
    from typing import Self
    from uuid import UUID

_SERVER_ERROR_MINIMUM_STATUS = 500


class ExecutionPlaneClientError(WorkflowError):
    """A safe Execution Plane API failure classified for Temporal retry policy."""

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        """Record only a bounded status classification, never an upstream body."""
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class ExecutionPlaneClient:
    """Submit, inspect, and cancel one project-scoped generic container work item."""

    def __init__(
        self,
        *,
        base_url: str,
        token_for_project: Callable[[UUID], str],
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the client with operator-owned routing and per-request identity."""
        self._base_url = base_url.rstrip("/")
        self._token_for_project = token_for_project
        self._http_client = http_client or build_internal_http_client(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._owns_client = http_client is None

    async def close(self) -> None:
        """Release the owned HTTP connection pool."""
        if self._owns_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the client context."""
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close an internally created client at activity completion."""
        await self.close()

    async def submit(
        self,
        *,
        project_id: UUID,
        request_id: str,
        work_correlation_id: UUID,
        workload_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Submit a validated container request using a retry-stable identity."""
        return await self._request_work_item(
            "POST",
            "/v1/work-items",
            project_id=project_id,
            body={
                "request_id": request_id,
                "work_correlation_id": str(work_correlation_id),
                "workload_type": "container",
                "payload": dict(workload_request),
            },
        )

    async def get_by_request_id(
        self,
        *,
        project_id: UUID,
        request_id: str,
    ) -> dict[str, Any]:
        """Read the durable state for the retry-stable work-item identity."""
        return await self._request_work_item(
            "GET",
            f"/v1/work-items/by-request/{quote(request_id, safe='')}",
            project_id=project_id,
        )

    async def cancel_by_request_id(
        self,
        *,
        project_id: UUID,
        request_id: str,
        work_correlation_id: UUID,
    ) -> dict[str, Any]:
        """Request cancellation without requiring a transient EP work-item ID."""
        return await self._request_work_item(
            "POST",
            f"/v1/work-items/by-request/{quote(request_id, safe='')}/cancel",
            project_id=project_id,
            body={"work_correlation_id": str(work_correlation_id)},
        )

    async def _request_work_item(
        self,
        method: str,
        path: str,
        *,
        project_id: UUID,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue one authenticated API request and accept only a safe work-item response."""
        try:
            response = await self._http_client.request(
                method,
                path,
                json=body,
                headers={"Authorization": f"Bearer {self._token_for_project(project_id)}"},
            )
        except httpx.RequestError as error:
            msg = "Execution Plane request could not be completed"
            raise ExecutionPlaneClientError(msg, retryable=True) from error

        if response.is_error:
            retryable = (
                response.status_code == httpx.codes.TOO_MANY_REQUESTS
                or response.status_code >= _SERVER_ERROR_MINIMUM_STATUS
            )
            msg = f"Execution Plane returned HTTP {response.status_code}"
            raise ExecutionPlaneClientError(msg, retryable=retryable, status_code=response.status_code)

        try:
            response_data = response.json()
        except ValueError as error:
            msg = "Execution Plane returned an invalid work-item response"
            raise ExecutionPlaneClientError(msg, retryable=False, status_code=response.status_code) from error
        if not isinstance(response_data, dict):
            msg = "Execution Plane returned an invalid work-item response"
            raise ExecutionPlaneClientError(msg, retryable=False, status_code=response.status_code)
        if not isinstance(response_data.get("id"), str) or not isinstance(response_data.get("status"), str):
            msg = "Execution Plane returned an incomplete work-item response"
            raise ExecutionPlaneClientError(msg, retryable=False, status_code=response.status_code)
        result = response_data.get("result")
        if result is not None and not isinstance(result, dict):
            msg = "Execution Plane returned an invalid work-item result"
            raise ExecutionPlaneClientError(msg, retryable=False, status_code=response.status_code)
        return response_data
