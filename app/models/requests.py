from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.tokens import TokenPayload


class RequestState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AsyncRequestAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)


class AsyncRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: RequestState
    access_token: str | None = None
    token_type: str | None = None
    expire_time: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def tokens_only_when_succeeded(self) -> "AsyncRequestResponse":
        if self.status is RequestState.SUCCEEDED:
            return self
        self.access_token = None
        self.token_type = None
        self.expire_time = None
        if self.status is not RequestState.FAILED:
            self.error = None
        return self

    @classmethod
    def from_token(cls, request_id: str, token: TokenPayload) -> "AsyncRequestResponse":
        return cls(
            request_id=request_id,
            status=RequestState.SUCCEEDED,
            access_token=token.access_token,
            token_type=token.token_type,
            expire_time=token.expire_time,
        )
