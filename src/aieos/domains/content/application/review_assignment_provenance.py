"""Authoring provenance helpers for reviewer assignment authority."""

from __future__ import annotations

from typing import Mapping
from uuid import UUID

from aieos.domains.content.application.errors import ReviewAssignmentNotAllowed
from aieos.domains.content.domain.identities import ContentVersionId


def mandatory_authoring_class_ref(
    provenance: Mapping[str, object] | None,
) -> str:
    if provenance is None:
        raise ReviewAssignmentNotAllowed(
            "version lacks governed authoring provenance for class_ref"
        )
    raw = provenance.get("authoring_class_ref")
    if not isinstance(raw, str) or not raw.strip():
        raise ReviewAssignmentNotAllowed(
            "immutable authoring provenance class_ref is missing or invalid"
        )
    return raw.strip()


def provenance_authoring_principal_id(
    provenance: Mapping[str, object] | None,
) -> UUID:
    if provenance is None:
        raise ReviewAssignmentNotAllowed(
            "version lacks governed authoring provenance for creator"
        )
    raw = provenance.get("authoring_principal_id")
    if not isinstance(raw, str):
        raise ReviewAssignmentNotAllowed(
            "immutable authoring provenance creator is missing or invalid"
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ReviewAssignmentNotAllowed(
            "immutable authoring provenance creator is missing or invalid"
        ) from exc


def version_created_by_matches(
    *,
    provenance: Mapping[str, object] | None,
    created_by_principal_id: UUID,
) -> bool:
    try:
        return provenance_authoring_principal_id(provenance) == created_by_principal_id
    except ReviewAssignmentNotAllowed:
        return False