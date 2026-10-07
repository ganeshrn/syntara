"""Project-scoped identities for later plugin integration connections.

This first record deliberately stores neither endpoint configuration nor a
credential reference. Those require separately approved endpoint, credential,
and broker boundaries. It only pins the immutable integration-type revision and
one endpoint kind that the installed plugin declared.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID  # noqa: TC003 - SQLModel resolves table annotations at class construction.

from sqlalchemy import Boolean, Column, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field

from syntara.core.models.base.base_resource import AuditLevel
from syntara.core.models.base.named import NamedResource
from syntara.core.models.base.user_owned import UserOwnedResource


class PluginConnection(NamedResource, UserOwnedResource, table=True):
    """A named project connection pinned to an installed integration type.

    The record is intentionally only a non-secret selection boundary. It is
    not an endpoint, credential, OAuth session, or executable runtime route.
    """

    __tablename__ = "plugin_connections"
    __auditable__: ClassVar[AuditLevel] = AuditLevel.META
    __auditable_fields__: ClassVar[list[str]] = [
        "name",
        "description",
        "project_id",
        "integration_type_revision_id",
        "endpoint_kind",
        "enabled",
        "created_by",
        "updated_by",
    ]
    __table_args__ = (
        UniqueConstraint("name", "project_id", name="uq_plugin_connections_name_project"),
        Index("ix_plugin_connections_project_enabled", "project_id", "enabled"),
    )

    project_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        description="Project that owns this connection identity.",
    )
    integration_type_revision_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey(
                "installed_plugin_integration_types.id",
                name="fk_plugin_connections_integration_type_revision",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        ),
        description="Exact immutable installed integration type selected by this connection.",
    )
    endpoint_kind: str = Field(
        min_length=1,
        max_length=63,
        sa_type=String(63),  # type: ignore[call-overload]
        description="One non-secret endpoint kind declared by the installed integration type.",
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
        description="Whether later trusted resolution may select this connection.",
    )

    __filterable_fields__: ClassVar[list[str]] = [
        *NamedResource.__filterable_fields__,
        *UserOwnedResource.__filterable_fields__,
        "project_id",
        "integration_type_revision_id",
        "endpoint_kind",
        "enabled",
    ]
    __sortable_fields__: ClassVar[list[str]] = [
        *NamedResource.__sortable_fields__,
        *UserOwnedResource.__sortable_fields__,
        "enabled",
    ]
