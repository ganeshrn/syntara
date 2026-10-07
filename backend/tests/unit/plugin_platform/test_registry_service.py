"""Tests for immutable installed-plugin generations and trusted action lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlmodel import select

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from syntara.core.models import User

from syntara.plugin_platform.exceptions import InstalledActionNotFoundError, PluginInstallConflictError
from syntara.plugin_platform.models.registry import (
    InstalledPluginCredentialType,
    InstalledPluginIntegrationType,
    PluginRegistryGeneration,
    PluginRegistryState,
)
from syntara.plugin_platform.services.registry_service import (
    InstalledActionCandidate,
    InstalledCredentialTypeCandidate,
    InstalledIntegrationTypeCandidate,
    PluginRegistryService,
    VerifiedPluginCandidate,
)


def _digest(character: str) -> str:
    """Build a deterministic test-only SHA-256 digest."""
    return f"sha256:{character * 64}"


def _action(
    *,
    name: str = "create-issue",
    digest_character: str = "b",
    integration_type_id: str | None = None,
) -> InstalledActionCandidate:
    """Create a minimal resolved action binding suitable for persistence tests."""
    return InstalledActionCandidate(
        target_name=name,
        display_name="Create issue",
        description="Create an issue in the selected repository.",
        revision_digest=_digest(digest_character),
        runtime_kind="custom-workload",
        image_digest=f"quay.io/syntara/http-runner@{_digest('c')}",
        integration_type_id=integration_type_id,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        error_schema={"type": "object"},
        canonical_definition={"kind": "Action", "metadata": {"name": name}},
    )


def _integration_type() -> InstalledIntegrationTypeCandidate:
    return InstalledIntegrationTypeCandidate(
        type_id="syntara.github.connection",
        display_name="GitHub connection",
        description="GitHub Cloud or Enterprise Server connection.",
        revision_digest=_digest("d"),
        endpoint_kinds=("github-cloud", "github-enterprise"),
        credential_type_ids=("syntara.github.service-token",),
        configuration_schema={"type": "object"},
        canonical_definition={"kind": "IntegrationType", "id": "syntara.github.connection"},
    )


def _credential_type() -> InstalledCredentialTypeCandidate:
    return InstalledCredentialTypeCandidate(
        type_id="syntara.github.service-token",
        integration_type_id="syntara.github.connection",
        display_name="GitHub service token",
        description="A static GitHub bearer token.",
        revision_digest=_digest("e"),
        credential_schema={"type": "object", "properties": {"token": {"type": "string"}}},
        secret_field_names=("token",),
        issuance={"kind": "static-bearer.v1"},
        canonical_definition={"kind": "CredentialRecipe", "id": "syntara.github.service-token"},
    )


def _candidate(
    *,
    version: str = "1.0.0",
    artifact_character: str = "a",
    actions: tuple[InstalledActionCandidate, ...] | None = None,
    integration_types: tuple[InstalledIntegrationTypeCandidate, ...] = (),
    credential_types: tuple[InstalledCredentialTypeCandidate, ...] = (),
) -> VerifiedPluginCandidate:
    """Create a verified-candidate fixture without registry or network activity."""
    return VerifiedPluginCandidate(
        namespace="syntara",
        name="github",
        version=version,
        display_name="GitHub",
        description="GitHub actions.",
        artifact_repository="quay.io/syntara/plugins/github",
        artifact_digest=_digest(artifact_character),
        canonical_descriptor={"kind": "PluginDescriptor", "spec": {"targets": []}},
        verification_summary={"signature": "verified"},
        actions=actions or (_action(),),
        integration_types=integration_types,
        credential_types=credential_types,
    )


@pytest_asyncio.fixture(autouse=True)
async def initialized_plugin_registry(test_db_session: AsyncSession) -> None:
    """Restore the singleton migration seed after the isolated test DB is cleared."""
    state = (await test_db_session.exec(select(PluginRegistryState))).one_or_none()
    if state is None:
        test_db_session.add(PluginRegistryState())
        await test_db_session.commit()


@pytest.mark.asyncio
async def test_install_creates_first_active_generation_and_resolves_action(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """A verified artifact becomes selectable only through the active generation."""
    service = PluginRegistryService(test_db_session)
    candidate = _candidate()

    result = await service.install_verified(candidate, actor_id=test_user.id)
    resolved = await service.resolve_enabled_action("syntara.github.create-issue:" + "b" * 64)

    assert result.generation == 1
    assert result.idempotent is False
    assert resolved.registry_generation == 1
    assert resolved.plugin_digest == candidate.artifact_digest
    assert resolved.image_digest == "quay.io/syntara/http-runner@" + _digest("c")
    assert resolved.canonical_definition == candidate.actions[0].canonical_definition

    state = (await test_db_session.exec(select(PluginRegistryState))).one()
    assert state.active_generation_id is not None
    assert state.next_generation == 2


@pytest.mark.asyncio
async def test_new_install_copies_existing_enabled_members_into_new_generation(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """Installing a second version advances one full registry snapshot, not one route."""
    service = PluginRegistryService(test_db_session)
    first = _candidate()
    second = _candidate(
        version="1.1.0",
        artifact_character="d",
        actions=(_action(name="close-issue", digest_character="e"),),
    )

    await service.install_verified(first, actor_id=test_user.id)
    result = await service.install_verified(second, actor_id=test_user.id)

    first_action = await service.resolve_enabled_action("syntara.github.create-issue:" + "b" * 64)
    second_action = await service.resolve_enabled_action("syntara.github.close-issue:" + "e" * 64)
    generations = list((await test_db_session.exec(select(PluginRegistryGeneration))).all())

    assert result.generation == 2
    assert first_action.registry_generation == 2
    assert second_action.registry_generation == 2
    assert {generation.generation for generation in generations} == {1, 2}


@pytest.mark.asyncio
async def test_duplicate_exact_artifact_is_idempotent_without_advancing_generation(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """A retry cannot create another generation or another installed revision."""
    service = PluginRegistryService(test_db_session)
    candidate = _candidate()

    first = await service.install_verified(candidate, actor_id=test_user.id)
    retry = await service.install_verified(candidate, actor_id=test_user.id)

    assert retry == type(first)(plugin_installation_id=first.plugin_installation_id, generation=1, idempotent=True)
    generations = list((await test_db_session.exec(select(PluginRegistryGeneration))).all())
    assert len(generations) == 1


@pytest.mark.asyncio
async def test_conflicting_digest_for_same_plugin_version_is_rejected(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """A namespace/name/version cannot be silently rebound to another artifact."""
    service = PluginRegistryService(test_db_session)
    await service.install_verified(_candidate(), actor_id=test_user.id)

    with pytest.raises(PluginInstallConflictError):
        await service.install_verified(_candidate(artifact_character="f"), actor_id=test_user.id)


@pytest.mark.asyncio
async def test_unknown_or_disabled_identity_is_not_a_route_to_an_image(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """The resolver fails closed instead of accepting a caller-provided image route."""
    service = PluginRegistryService(test_db_session)
    await service.install_verified(_candidate(), actor_id=test_user.id)

    with pytest.raises(InstalledActionNotFoundError):
        await service.resolve_enabled_action("syntara.github.unknown:" + "0" * 64)


@pytest.mark.asyncio
async def test_disable_and_enable_publish_new_complete_generations(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """Lifecycle changes copy the snapshot and never mutate the installed action itself."""
    service = PluginRegistryService(test_db_session)
    installed = await service.install_verified(_candidate(), actor_id=test_user.id)
    action_id = "syntara.github.create-issue:" + "b" * 64

    disabled = await service.set_enabled(installed.plugin_installation_id, enabled=False, actor_id=test_user.id)
    with pytest.raises(InstalledActionNotFoundError):
        await service.resolve_enabled_action(action_id)
    enabled = await service.set_enabled(installed.plugin_installation_id, enabled=True, actor_id=test_user.id)
    retry = await service.set_enabled(installed.plugin_installation_id, enabled=True, actor_id=test_user.id)

    assert disabled.generation == 2
    assert disabled.enabled is False
    assert enabled.generation == 3
    assert enabled.enabled is True
    assert retry.generation == 3
    assert retry.idempotent is True
    assert (await service.resolve_enabled_action(action_id)).registry_generation == 3


@pytest.mark.asyncio
async def test_pinned_action_resolution_survives_a_later_disable(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """A published workflow keeps its immutable route after a later disable."""
    service = PluginRegistryService(test_db_session)
    installed = await service.install_verified(_candidate(), actor_id=test_user.id)
    action_id = "syntara.github.create-issue:" + "b" * 64

    await service.set_enabled(installed.plugin_installation_id, enabled=False, actor_id=test_user.id)
    pinned = await service.resolve_pinned_action(action_id)

    assert pinned.step_revision_id == action_id
    assert pinned.image_digest == "quay.io/syntara/http-runner@" + _digest("c")
    assert pinned.registry_generation is None


@pytest.mark.asyncio
async def test_install_persists_immutable_connection_type_definitions_and_action_reference(
    test_db_session: AsyncSession,
    test_user: User,
) -> None:
    """Connection contracts are installed with the action but never secret values."""
    integration = _integration_type()
    credential = _credential_type()
    candidate = _candidate(
        actions=(_action(integration_type_id=integration.type_id),),
        integration_types=(integration,),
        credential_types=(credential,),
    )

    installed = await PluginRegistryService(test_db_session).install_verified(candidate, actor_id=test_user.id)
    stored_integration = (
        await test_db_session.exec(
            select(InstalledPluginIntegrationType).where(
                InstalledPluginIntegrationType.plugin_installation_id == installed.plugin_installation_id
            )
        )
    ).one()
    stored_credential = (
        await test_db_session.exec(
            select(InstalledPluginCredentialType).where(
                InstalledPluginCredentialType.plugin_installation_id == installed.plugin_installation_id
            )
        )
    ).one()
    resolved = await PluginRegistryService(test_db_session).resolve_enabled_action(
        "syntara.github.create-issue:" + "b" * 64
    )

    assert stored_integration.type_id == integration.type_id
    assert stored_integration.endpoint_kinds == ["github-cloud", "github-enterprise"]
    assert stored_credential.integration_type_revision_id == stored_integration.id
    assert stored_credential.secret_field_names == ["token"]
    assert resolved.integration_type_revision_id == stored_integration.id


def test_candidate_rejects_duplicate_action_targets_before_persistence() -> None:
    """A malformed verified-candidate handoff cannot create ambiguous action identity."""
    with pytest.raises(ValidationError, match="duplicate action target names"):
        _candidate(actions=(_action(), _action(digest_character="d")))


def test_candidate_rejects_an_invalid_action_schema_before_persistence() -> None:
    """The installed registry never retains schemas that workflow validation cannot use."""
    action_data = _action().model_dump()
    action_data["input_schema"] = {"type": "not-a-json-schema-type"}
    with pytest.raises(ValidationError, match="valid JSON Schema"):
        InstalledActionCandidate.model_validate(action_data)


def test_candidate_rejects_dangling_connection_type_references_before_persistence() -> None:
    """An action cannot refer to a type from another plugin or installation."""
    with pytest.raises(ValidationError, match="undeclared integration type"):
        _candidate(actions=(_action(integration_type_id="syntara.github.connection"),))


def test_step_revision_id_stays_inside_the_shared_abi_limit() -> None:
    """The most-expanded valid identifiers still fit the ABI's 256-character ID bound."""
    long = "a" + "b" * 62
    candidate = VerifiedPluginCandidate(
        namespace=long,
        name=long,
        version="1.0.0",
        display_name="Long plugin",
        description="Long plugin identity boundary test.",
        artifact_repository="quay.io/syntara/plugins/long",
        artifact_digest=_digest("a"),
        canonical_descriptor={"kind": "PluginDescriptor"},
        actions=(_action(name=long),),
    )

    action_id = f"{candidate.namespace}.{candidate.name}.{long}:" + "b" * 64
    assert len(action_id) == 256
