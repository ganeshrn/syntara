"""Tests for the trusted Syntara-to-EP HTTP boundary."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from syntara.workflows.clients.execution_plane_client import ExecutionPlaneClient, ExecutionPlaneClientError


def _client(handler: httpx.AsyncBaseTransport) -> ExecutionPlaneClient:
    return ExecutionPlaneClient(
        base_url="https://ep.example.test",
        token_for_project=lambda _project: "test-service-token",
        timeout_seconds=10,
        http_client=httpx.AsyncClient(transport=handler, base_url="https://ep.example.test"),
    )


async def test_submit_sends_project_scoped_token_and_generic_envelope() -> None:
    """The bridge sends the EP envelope, not an arbitrary command or secret value."""
    project_id = uuid4()
    correlation_id = uuid4()
    observed: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["authorization"] = request.headers["authorization"]
        observed["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": str(uuid4()), "status": "pending", "result": None})

    client = _client(httpx.MockTransport(handler))
    try:
        response = await client.submit(
            project_id=project_id,
            request_id="container-request",
            work_correlation_id=correlation_id,
            workload_request={"kind": "ContainerWorkloadRequest"},
        )
    finally:
        await client.close()

    assert response["status"] == "pending"
    assert observed == {
        "path": "/v1/work-items",
        "authorization": "Bearer test-service-token",
        "body": {
            "request_id": "container-request",
            "work_correlation_id": str(correlation_id),
            "workload_type": "container",
            "payload": {"kind": "ContainerWorkloadRequest"},
        },
    }


async def test_client_classifies_transient_server_failure_without_leaking_body() -> None:
    """A server error may retry but its untrusted response body is not surfaced."""
    client = _client(httpx.MockTransport(lambda _: httpx.Response(503, text="secret upstream diagnostic")))
    try:
        with pytest.raises(ExecutionPlaneClientError) as raised:
            await client.get_by_request_id(project_id=uuid4(), request_id="container-request")
    finally:
        await client.close()

    assert raised.value.retryable is True
    assert raised.value.status_code == 503
    assert "secret" not in str(raised.value)


async def test_client_rejects_malformed_work_item_response() -> None:
    """A successful HTTP response still needs a minimal safe work-item shape."""
    client = _client(httpx.MockTransport(lambda _: httpx.Response(202, json={"status": "pending"})))
    try:
        with pytest.raises(ExecutionPlaneClientError, match="incomplete") as raised:
            await client.get_by_request_id(project_id=UUID(int=1), request_id="container-request")
    finally:
        await client.close()

    assert raised.value.retryable is False


async def test_client_escapes_request_identity_before_placing_it_in_a_url_path() -> None:
    """Caller-supplied identity text cannot change the EP endpoint being invoked."""
    observed_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_paths.append(request.url.raw_path.decode("ascii"))
        return httpx.Response(200, json={"id": str(uuid4()), "status": "pending", "result": None})

    client = _client(httpx.MockTransport(handler))
    try:
        await client.get_by_request_id(project_id=uuid4(), request_id="request/with?reserved")
    finally:
        await client.close()

    assert observed_paths == ["/v1/work-items/by-request/request%2Fwith%3Freserved"]
