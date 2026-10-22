from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_url: str
    state: str


class CallbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["connected"] = "connected"
    provider: str
    user_id: str = Field(min_length=1)
