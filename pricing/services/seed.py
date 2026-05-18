from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProviderYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    provider_type: str
    data_source_tier: str
    pricing_url: str = ""
    has_api: bool = False
    api_endpoint: str = ""
    is_active: bool = True
