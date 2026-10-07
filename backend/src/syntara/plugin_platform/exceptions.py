"""Safe domain errors for the installed-plugin registry boundary."""

from syntara.core.exception_registry import fastapi_exception
from syntara.core.exceptions import SyntaraError


class PluginRegistryError(SyntaraError):
    """Base error for trusted installed-plugin registry operations."""


class PluginArtifactVerificationError(PluginRegistryError):
    """An OCI plugin artifact failed bounded, pre-persistence verification."""

    def __init__(self, code: str) -> None:
        """Return a stable, non-reflective reason safe for an API caller."""
        super().__init__(f"Plugin artifact verification failed: {code}")
        self.code = code


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_index_artifact_verification_handler")
class CatalogIndexArtifactVerificationError(PluginRegistryError):
    """An OCI catalog-index artifact failed bounded, pre-persistence verification."""

    def __init__(self, code: str) -> None:
        """Return a stable, non-reflective reason safe for an API caller."""
        super().__init__(f"Catalog index artifact verification failed: {code}")
        self.code = code


class CatalogRegistryClientError(PluginRegistryError):
    """A safe OCI Distribution catalog-request failure."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        """Preserve only a stable classification, never upstream content or credentials."""
        super().__init__(f"Catalog registry request failed: {code}")
        self.code = code
        self.retryable = retryable


class CatalogIndexTrustError(PluginRegistryError):
    """The configured catalog trust policy did not admit an immutable index."""

    def __init__(self, code: str) -> None:
        """Expose a stable denial code without reflecting verifier details."""
        super().__init__(f"Catalog index trust verification failed: {code}")
        self.code = code


class PluginArtifactTrustError(PluginRegistryError):
    """The configured source policy did not admit a plugin artifact digest."""

    def __init__(self, code: str) -> None:
        """Expose a stable denial code without reflecting verifier details."""
        super().__init__(f"Plugin artifact trust verification failed: {code}")
        self.code = code


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_plugin_installation_unavailable_handler")
class CatalogPluginInstallationUnavailableError(PluginRegistryError):
    """Trusted installation adapters have not been configured for this deployment."""

    def __init__(self) -> None:
        """Avoid exposing which adapter or secret configuration is absent."""
        super().__init__("Plugin installation is not configured for this environment")


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_source_reconciliation_unavailable_handler")
class CatalogSourceReconciliationUnavailableError(PluginRegistryError):
    """No trusted source-reconciliation composition is configured for this deployment."""

    def __init__(self) -> None:
        """Do not disclose deployment topology or trust-adapter details."""
        super().__init__("Catalog source reconciliation is not configured for this environment")


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_documentation_hydration_unavailable_handler")
class CatalogDocumentationHydrationUnavailableError(PluginRegistryError):
    """No trusted documentation-hydration composition is configured for this deployment."""

    def __init__(self) -> None:
        """Avoid exposing deployment topology or trust-adapter details."""
        super().__init__("Catalog documentation hydration is not configured for this environment")


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_source_not_found_handler")
class CatalogSourceNotFoundError(PluginRegistryError):
    """The requested local catalog source is absent."""

    def __init__(self) -> None:
        """Return a safe non-enumerating source lookup error."""
        super().__init__("The requested catalog source is not available")


class CatalogSourceDisabledError(PluginRegistryError):
    """A disabled source cannot create a new reconciliation observation."""

    def __init__(self) -> None:
        """Return a safe disabled-source error."""
        super().__init__("The requested catalog source is disabled")


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_source_configuration_handler")
class CatalogSourceConfigurationError(PluginRegistryError):
    """Stored source state or a trusted handoff breaks a service invariant."""

    def __init__(self, message: str = "Catalog source configuration is invalid") -> None:
        """Return a safe default without exposing validation or upstream internals."""
        super().__init__(message)


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_source_conflict_handler")
class CatalogSourceIdentityConflictError(PluginRegistryError):
    """Another source already owns the requested stable catalog-source identity."""

    def __init__(self, source_id: str) -> None:
        """Expose only the non-secret identity that an administrator supplied."""
        super().__init__(f"Catalog source identity '{source_id}' already exists")


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_plugin_not_found_handler")
class CatalogPluginNotFoundError(PluginRegistryError):
    """No visible active catalog projection matched the requested plugin identity."""

    def __init__(self) -> None:
        """Return a safe non-enumerating catalog lookup error."""
        super().__init__("The requested catalog plugin is not available")


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.catalog_plugin_ambiguous_handler")
class CatalogPluginIdentityAmbiguousError(PluginRegistryError):
    """More than one enabled source publishes the same visible plugin version."""

    def __init__(self) -> None:
        """Require an explicit non-secret source selector for deterministic inspection."""
        super().__init__("More than one catalog source provides this plugin version; specify source_id")


class PluginRegistryConfigurationError(PluginRegistryError):
    """The required singleton registry pointer is absent or invalid."""


class PluginInstallConflictError(PluginRegistryError):
    """A different artifact already occupies the requested plugin version."""


@fastapi_exception(handler="syntara.plugin_platform.error_handlers.installed_plugin_not_found_handler")
class InstalledPluginNotFoundError(PluginRegistryError):
    """The requested immutable plugin revision is absent from the active registry."""

    def __init__(self) -> None:
        """Initialize a safe, intentionally non-enumerating lifecycle error."""
        super().__init__("The requested installed plugin is not available")


class InstalledActionNotFoundError(PluginRegistryError):
    """No enabled action in the active registry has the requested revision."""

    def __init__(self) -> None:
        """Initialize a safe, intentionally non-enumerating lookup error."""
        super().__init__("The requested installed action is not enabled")


class RuntimeContextConfigurationError(PluginRegistryError):
    """An installed action cannot produce a safe runner runtime context."""

    def __init__(self, code: str) -> None:
        """Expose only a stable composition classification, never action data."""
        super().__init__(f"Installed action runtime context is invalid: {code}")
        self.code = code


class WorkloadRequestCompositionError(PluginRegistryError):
    """Trusted installed state cannot form a valid generic container request."""

    def __init__(self, code: str) -> None:
        """Expose only a stable composition classification, never execution data."""
        super().__init__(f"External action request composition is invalid: {code}")
        self.code = code


class WorkflowActionBindingError(PluginRegistryError):
    """A workflow revision cannot safely pin one installed action route."""

    def __init__(self, code: str, *, node_id: str | None = None) -> None:
        """Return a stable failure classification without reflecting workflow input."""
        super().__init__(f"Workflow action binding is invalid: {code}")
        self.code = code
        self.node_id = node_id


class PluginConnectionError(PluginRegistryError):
    """A project plugin-connection identity cannot be safely created or resolved."""

    def __init__(self, code: str) -> None:
        """Expose only a stable non-secret reason to trusted callers."""
        super().__init__(f"Plugin connection is invalid: {code}")
        self.code = code
