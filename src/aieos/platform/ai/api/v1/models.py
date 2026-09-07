"""Pydantic HTTP DTOs for the read-only Provider Aggregator projection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProviderCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    configured: bool
    active: bool
    model_id: str | None = Field(default=None, max_length=128)
    development_only: bool


class CapabilityRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)


class ProviderAggregatorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_provider_id: str = Field(min_length=1, max_length=64)
    active_model_id: str = Field(min_length=1, max_length=128)
    mode: str = Field(min_length=1, max_length=64)
    providers: list[ProviderCandidateResponse]
    capability_routes: list[CapabilityRouteResponse]
