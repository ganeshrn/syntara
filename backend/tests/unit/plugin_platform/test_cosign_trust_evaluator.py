"""Unit coverage for pinned-public-key Cosign trust admission."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from syntara.plugin_platform.exceptions import (
    CatalogIndexTrustError,
    CatalogSourceConfigurationError,
    PluginArtifactTrustError,
)
from syntara.plugin_platform.services.catalog_reconciliation_orchestrator import CatalogIndexTrustRequest
from syntara.plugin_platform.services.cosign_trust_evaluator import (
    CosignVerificationError,
    CosignVerificationRequest,
    PinnedCosignKeyTrustPolicy,
    PinnedCosignTrustEvaluator,
    SubprocessCosignVerificationRunner,
)
from syntara.plugin_platform.services.plugin_artifact_trust import PluginArtifactTrustRequest

_ORIGIN = "https://quay.example.test"
_DIGEST = "sha256:" + "a" * 64
_PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nAQID\n-----END PUBLIC KEY-----\n"


@dataclass
class _RecordingRunner:
    requests: list[CosignVerificationRequest] = field(default_factory=list)
    failure: CosignVerificationError | None = None

    async def verify(self, request: CosignVerificationRequest) -> None:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure


def _policy(*, require_transparency_log: bool = True) -> PinnedCosignKeyTrustPolicy:
    return PinnedCosignKeyTrustPolicy(
        reference="trust/local-quay",
        registry_origin=_ORIGIN,
        repository_prefixes=("syntara",),
        plugin_namespaces=frozenset({"syntara"}),
        public_key_pem=_PUBLIC_KEY,
        require_transparency_log=require_transparency_log,
    )


def _catalog_request(**overrides: str) -> CatalogIndexTrustRequest:
    values = {
        "source_id": "local-quay",
        "registry_origin": _ORIGIN,
        "index_repository": "syntara/catalog-index",
        "channel": "stable",
        "index_digest": _DIGEST,
        "trust_policy_ref": "trust/local-quay",
        "mirror_policy": "direct",
    }
    values.update(overrides)
    return CatalogIndexTrustRequest(**values)  # type: ignore[arg-type]


def _plugin_request(**overrides: str) -> PluginArtifactTrustRequest:
    values = {
        "source_id": "local-quay",
        "registry_origin": _ORIGIN,
        "artifact_repository": "syntara/plugins/github",
        "artifact_digest": _DIGEST,
        "namespace": "syntara",
        "name": "github",
        "version": "1.0.0",
        "trust_policy_ref": "trust/local-quay",
        "mirror_policy": "direct",
    }
    values.update(overrides)
    return PluginArtifactTrustRequest(**values)  # type: ignore[arg-type]


async def test_catalog_index_verification_uses_the_selected_policy_and_exact_digest() -> None:
    runner = _RecordingRunner()
    evaluator = PinnedCosignTrustEvaluator({_policy().reference: _policy()}, runner=runner)

    decision = await evaluator.evaluate_catalog_index(_catalog_request())

    assert runner.requests == [
        CosignVerificationRequest(
            image_reference=f"quay.example.test/syntara/catalog-index@{_DIGEST}",
            public_key_pem=_PUBLIC_KEY,
            require_transparency_log=True,
        )
    ]
    assert decision.summary["verifier"] == "cosign"
    assert decision.summary["transparencyLogRequired"] is True
    assert decision.summary["signerKeySha256"].startswith("sha256:")


async def test_plugin_artifact_verification_rejects_a_namespace_outside_the_pinned_policy() -> None:
    runner = _RecordingRunner()
    evaluator = PinnedCosignTrustEvaluator({_policy().reference: _policy()}, runner=runner)

    with pytest.raises(PluginArtifactTrustError, match="COSIGN_POLICY_NAMESPACE_DENIED"):
        await evaluator.evaluate_plugin_artifact(_plugin_request(namespace="acme"))

    assert runner.requests == []


async def test_catalog_index_verification_rejects_a_repository_outside_the_pinned_policy() -> None:
    runner = _RecordingRunner()
    evaluator = PinnedCosignTrustEvaluator({_policy().reference: _policy()}, runner=runner)

    with pytest.raises(CatalogIndexTrustError, match="COSIGN_POLICY_REPOSITORY_DENIED"):
        await evaluator.evaluate_catalog_index(_catalog_request(index_repository="other/catalog-index"))

    assert runner.requests == []


async def test_unknown_policy_fails_as_source_configuration_before_cosign_runs() -> None:
    runner = _RecordingRunner()
    evaluator = PinnedCosignTrustEvaluator({_policy().reference: _policy()}, runner=runner)

    with pytest.raises(CatalogSourceConfigurationError, match="trust policy is unavailable"):
        await evaluator.evaluate_plugin_artifact(_plugin_request(trust_policy_ref="trust/missing"))

    assert runner.requests == []


async def test_cosign_denial_becomes_the_correct_artifact_trust_error() -> None:
    runner = _RecordingRunner(failure=CosignVerificationError("COSIGN_VERIFICATION_FAILED"))
    evaluator = PinnedCosignTrustEvaluator({_policy().reference: _policy()}, runner=runner)

    with pytest.raises(PluginArtifactTrustError, match="COSIGN_VERIFICATION_FAILED"):
        await evaluator.evaluate_plugin_artifact(_plugin_request())


async def test_subprocess_runner_uses_an_ephemeral_key_and_never_inherits_host_docker_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Process:
        returncode = 0

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            message = "The successful process must not be killed"
            raise AssertionError(message)

    async def create_process(*args: object, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        public_key_path = Path(str(args[3]))
        captured["public_key"] = public_key_path.read_text()
        docker_config = Path(str(kwargs["env"]["DOCKER_CONFIG"]))  # type: ignore[index]
        captured["docker_config_was_dir"] = docker_config.is_dir()
        return _Process()

    monkeypatch.setattr(
        "syntara.plugin_platform.services.cosign_trust_evaluator.shutil.which",
        lambda executable: f"/test/bin/{executable}",
    )
    monkeypatch.setattr(
        "syntara.plugin_platform.services.cosign_trust_evaluator.asyncio.create_subprocess_exec",
        create_process,
    )

    await SubprocessCosignVerificationRunner().verify(
        CosignVerificationRequest(
            image_reference=f"quay.example.test/syntara/plugins/github@{_DIGEST}",
            public_key_pem=_PUBLIC_KEY,
            require_transparency_log=False,
        )
    )

    assert captured["args"] == (
        "/test/bin/cosign",
        "verify",
        "--key",
        captured["args"][3],
        "--output",
        "json",
        "--insecure-ignore-tlog",
        f"quay.example.test/syntara/plugins/github@{_DIGEST}",
    )
    assert captured["public_key"] == _PUBLIC_KEY
    environment = captured["kwargs"]["env"]  # type: ignore[index]
    assert isinstance(environment, dict)
    assert set(environment) == {"DOCKER_CONFIG", "PATH"}
    assert captured["docker_config_was_dir"] is True
