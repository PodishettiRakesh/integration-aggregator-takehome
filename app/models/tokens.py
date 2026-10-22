from pydantic import BaseModel, ConfigDict, Field


class TokenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1)
    token_type: str = "Bearer"
    expire_time: str | None = None
