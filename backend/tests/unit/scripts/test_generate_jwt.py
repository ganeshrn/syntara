"""Regression tests for the local JWT generation helper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

from tools import generate_jwt

if TYPE_CHECKING:
    from syntara.core.models import User


class _TokenService:
    """Capture the public token-service call without loading a signing key."""

    def __init__(self, **_: object) -> None:
        self._key_manager = SimpleNamespace(key_id="test-key")
        self.subject_id: UUID | None = None

    def create_access_token(self, *, subject_id: UUID, username: str, email: str) -> str:
        self.subject_id = subject_id
        assert username == "admin"
        assert email == "admin@example.test"
        return "test-access-credential"


@pytest.mark.asyncio
async def test_generate_tokens_uses_the_token_service_subject_id_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the developer helper aligned with TokenService's stable API."""
    service = _TokenService()
    monkeypatch.setattr(generate_jwt, "TokenService", lambda **_: service)
    user_id = UUID("d0ce04b9-8fa8-4695-b242-47d19f74fb66")

    user = cast("User", SimpleNamespace(id=user_id, username="admin", email="admin@example.test"))
    tokens = await generate_jwt.generate_tokens(user, access_only=True)

    assert service.subject_id == user_id
    assert tokens["access_token"] == "test-access-credential"  # noqa: S105
