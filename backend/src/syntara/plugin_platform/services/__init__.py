"""Services for lifecycle-safe installed-plugin persistence and lookup."""

from .artifact_verifier import (
    PluginArtifactBlobDescriptor,
    PluginArtifactInput,
    PluginArtifactManifestPlan,
    PluginArtifactVerifier,
    VerifiedPluginArtifact,
    VerifiedPluginDocumentation,
)
from .catalog_browse_service import CatalogBrowseService
from .catalog_documentation_hydration_service import (
    CatalogDocumentationHydrationResult,
    CatalogDocumentationHydrationService,
)
from .catalog_index_verifier import (
    CatalogIndexArtifactInput,
    CatalogIndexArtifactVerifier,
    CatalogIndexManifestPlan,
    OciBlobDescriptor,
    VerifiedCatalogIndexArtifact,
)
from .catalog_installation_service import (
    CatalogPluginInstallationService,
    CatalogPluginInstallResult,
    WorkloadImageAdmissionDecision,
    WorkloadImageAdmissionEvaluator,
    WorkloadImageAdmissionRequest,
)
from .catalog_reconciliation_orchestrator import (
    CatalogIndexTrustDecision,
    CatalogIndexTrustEvaluator,
    CatalogIndexTrustRequest,
    CatalogReconciliationOrchestrator,
)
from .catalog_reconciliation_service import (
    CatalogReconciliationDecision,
    CatalogReconciliationResult,
    CatalogReconciliationService,
    catalog_source_fingerprint,
    plan_catalog_reconciliation,
)
from .cosign_trust_evaluator import (
    CosignVerificationError,
    CosignVerificationRequest,
    CosignVerificationRunner,
    PinnedCosignKeyTrustPolicy,
    PinnedCosignTrustEvaluator,
    SubprocessCosignVerificationRunner,
)
from .installed_plugin_browse_service import InstalledPluginBrowseService
from .local_poc_catalog_reconciliation_service import (
    LocalPocCatalogDocumentationHydrationService,
    LocalPocCatalogIndexTrustEvaluator,
    LocalPocCatalogReconciliationService,
    LocalPocCatalogRegistryAuthorizationResolver,
    LocalPocPluginArtifactTrustEvaluator,
)
from .oci_distribution_client import (
    CatalogIndexLocator,
    OciDistributionClient,
    PluginArtifactLocator,
    ResolvedCatalogIndexManifest,
    ResolvedPluginArtifactManifest,
)
from .plugin_artifact_trust import (
    PluginArtifactTrustDecision,
    PluginArtifactTrustEvaluator,
    PluginArtifactTrustRequest,
)
from .plugin_connection_service import (
    PluginConnectionCreate,
    PluginConnectionService,
    ResolvedPluginConnection,
)
from .published_action_composer import (
    PublishedActionReference,
    PublishedActionWorkloadComposer,
    ServerResolvedActionContext,
)
from .registry_access import (
    CatalogRegistryAuthorizationRequest,
    CatalogRegistryAuthorizationResolver,
    CatalogRegistryTLSContextResolver,
    SystemTrustCatalogRegistryTLSContextResolver,
)
from .registry_service import (
    InstalledActionCandidate,
    InstalledCredentialTypeCandidate,
    InstalledIntegrationTypeCandidate,
    InstallResult,
    LifecycleResult,
    PluginRegistryService,
    ResolvedInstalledAction,
    VerifiedPluginCandidate,
)
from .runner_profile import (
    DatabasePlatformRunnerProfileResolver,
    PlatformRunnerProfile,
    PlatformRunnerProfileResolver,
    RunnerProfileRevisionCandidate,
    RunnerProfileRevisionResult,
    RunnerProfileRevisionService,
    StaticPlatformRunnerProfileResolver,
    runner_profile_revision_digest,
)
from .runtime_context import build_runtime_context
from .syntara_registry_credential_resolver import SyntaraCredentialRegistryAuthorizationResolver
from .syntara_registry_tls_resolver import SyntaraCredentialRegistryTLSContextResolver
from .workflow_action_binding import (
    EnabledInstalledActionResolver,
    ExternalActionReference,
    PinnedInstalledActionResolver,
    PublishedWorkflowActionResolver,
    ResolvedActionBinding,
    WorkflowActionBindingResolver,
    WorkflowActionBindingService,
)
from .workload_request import (
    TrustedActionInvocation,
    build_container_workload_request,
    validate_installed_action_inputs,
)

__all__ = [
    "CatalogBrowseService",
    "CatalogDocumentationHydrationResult",
    "CatalogDocumentationHydrationService",
    "CatalogIndexArtifactInput",
    "CatalogIndexArtifactVerifier",
    "CatalogIndexLocator",
    "CatalogIndexManifestPlan",
    "CatalogIndexTrustDecision",
    "CatalogIndexTrustEvaluator",
    "CatalogIndexTrustRequest",
    "CatalogPluginInstallResult",
    "CatalogPluginInstallationService",
    "CatalogReconciliationDecision",
    "CatalogReconciliationOrchestrator",
    "CatalogReconciliationResult",
    "CatalogReconciliationService",
    "CatalogRegistryAuthorizationRequest",
    "CatalogRegistryAuthorizationResolver",
    "CatalogRegistryTLSContextResolver",
    "CosignVerificationError",
    "CosignVerificationRequest",
    "CosignVerificationRunner",
    "DatabasePlatformRunnerProfileResolver",
    "EnabledInstalledActionResolver",
    "ExternalActionReference",
    "InstallResult",
    "InstalledActionCandidate",
    "InstalledCredentialTypeCandidate",
    "InstalledIntegrationTypeCandidate",
    "InstalledPluginBrowseService",
    "LifecycleResult",
    "LocalPocCatalogDocumentationHydrationService",
    "LocalPocCatalogIndexTrustEvaluator",
    "LocalPocCatalogReconciliationService",
    "LocalPocCatalogRegistryAuthorizationResolver",
    "LocalPocPluginArtifactTrustEvaluator",
    "OciBlobDescriptor",
    "OciDistributionClient",
    "PinnedCosignKeyTrustPolicy",
    "PinnedCosignTrustEvaluator",
    "PinnedInstalledActionResolver",
    "PlatformRunnerProfile",
    "PlatformRunnerProfileResolver",
    "PluginArtifactBlobDescriptor",
    "PluginArtifactInput",
    "PluginArtifactLocator",
    "PluginArtifactManifestPlan",
    "PluginArtifactTrustDecision",
    "PluginArtifactTrustEvaluator",
    "PluginArtifactTrustRequest",
    "PluginArtifactVerifier",
    "PluginConnectionCreate",
    "PluginConnectionService",
    "PluginRegistryService",
    "PublishedActionReference",
    "PublishedActionWorkloadComposer",
    "PublishedWorkflowActionResolver",
    "ResolvedActionBinding",
    "ResolvedCatalogIndexManifest",
    "ResolvedInstalledAction",
    "ResolvedPluginArtifactManifest",
    "ResolvedPluginConnection",
    "RunnerProfileRevisionCandidate",
    "RunnerProfileRevisionResult",
    "RunnerProfileRevisionService",
    "ServerResolvedActionContext",
    "StaticPlatformRunnerProfileResolver",
    "SubprocessCosignVerificationRunner",
    "SyntaraCredentialRegistryAuthorizationResolver",
    "SyntaraCredentialRegistryTLSContextResolver",
    "SystemTrustCatalogRegistryTLSContextResolver",
    "TrustedActionInvocation",
    "VerifiedCatalogIndexArtifact",
    "VerifiedPluginArtifact",
    "VerifiedPluginCandidate",
    "VerifiedPluginDocumentation",
    "WorkflowActionBindingResolver",
    "WorkflowActionBindingService",
    "WorkloadImageAdmissionDecision",
    "WorkloadImageAdmissionEvaluator",
    "WorkloadImageAdmissionRequest",
    "build_container_workload_request",
    "build_runtime_context",
    "catalog_source_fingerprint",
    "plan_catalog_reconciliation",
    "runner_profile_revision_digest",
    "validate_installed_action_inputs",
]
