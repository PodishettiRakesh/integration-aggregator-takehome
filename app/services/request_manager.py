from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.errors import RequestNotFoundError
from app.core.logging import redact_text
from app.models.requests import AsyncRequestResponse, RequestState
from app.models.tokens import TokenPayload

Clock = Callable[[], float]
_TERMINAL = {RequestState.SUCCEEDED, RequestState.FAILED}


@dataclass(slots=True)
class TokenRequestRecord:
    request_id: str
    provider: str
    user_id: str
    status: RequestState = RequestState.PENDING
    token: TokenPayload | None = field(default=None, repr=False)
    error: str | None = None
    completed_at: float | None = None

    def to_response(self) -> AsyncRequestResponse:
        if self.status is RequestState.SUCCEEDED and self.token is not None:
            return AsyncRequestResponse.from_token(self.request_id, self.token)
        return AsyncRequestResponse(
            request_id=self.request_id,
            status=self.status,
            error=self.error if self.status is RequestState.FAILED else None,
        )


class RequestManager:
    """In-memory async token requests. Tokens never leave this process or logs."""

    def __init__(self, ttl_seconds: int, clock: Clock = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._items: dict[str, TokenRequestRecord] = {}

    async def create(self, provider: str, user_id: str) -> TokenRequestRecord:
        async with self._lock:
            self._purge_locked()
            request_id = secrets.token_urlsafe(16)
            record = TokenRequestRecord(
                request_id=request_id,
                provider=provider,
                user_id=user_id,
            )
            self._items[request_id] = record
            return record

    async def get(self, request_id: str) -> TokenRequestRecord:
        async with self._lock:
            self._purge_locked()
            record = self._items.get(request_id)
            if record is None:
                raise RequestNotFoundError(f"Request {request_id} was not found")
            return record

    async def mark_in_progress(self, request_id: str) -> bool:
        async with self._lock:
            record = self._require_locked(request_id)
            if record.status in _TERMINAL:
                return False
            record.status = RequestState.IN_PROGRESS
            return True

    async def mark_succeeded(self, request_id: str, token: TokenPayload) -> None:
        async with self._lock:
            record = self._require_locked(request_id)
            if record.status in _TERMINAL:
                return
            record.status = RequestState.SUCCEEDED
            record.token = token
            record.error = None
            record.completed_at = self._clock()

    async def mark_failed(self, request_id: str, error: str) -> None:
        async with self._lock:
            record = self._require_locked(request_id)
            if record.status in _TERMINAL:
                return
            record.status = RequestState.FAILED
            record.token = None
            record.error = redact_text(error)
            record.completed_at = self._clock()

    def _require_locked(self, request_id: str) -> TokenRequestRecord:
        record = self._items.get(request_id)
        if record is None:
            raise RequestNotFoundError(f"Request {request_id} was not found")
        return record

    def _purge_locked(self) -> None:
        now = self._clock()
        expired = [
            key
            for key, record in self._items.items()
            if record.completed_at is not None
            and record.completed_at + self._ttl_seconds <= now
        ]
        for key in expired:
            del self._items[key]
