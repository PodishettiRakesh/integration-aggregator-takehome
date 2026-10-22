from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class ProviderType(str, Enum):
    GITHUB = "github"
    GOOGLE = "google"
    GITLAB = "gitlab"
    OIDC = "oidc"


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    provider: ProviderType
    scopes: list[str] = Field(default_factory=list)
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("client_secret", when_used="json")
    def hide_secret(self, _value: str) -> str:
        return "[REDACTED]"


class ProviderPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    client_id: str
    provider: ProviderType
    scopes: list[str] = Field(default_factory=list)
    provider_options: dict[str, Any] = Field(default_factory=dict)
