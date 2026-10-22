from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.errors import InvalidOAuthStateError

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PendingOAuth:
    provider: str
    user_id: str
    expires_at: float


class OAuthStateStore:
    """In-memory OAuth `state` values. Consume-once, never written to disk."""

    def __init__(self, ttl_seconds: int, clock: Clock = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._states: dict[str, PendingOAuth] = {}

    async def create(self, provider: str, user_id: str) -> str:
        expires_at = self._clock() + self._ttl_seconds
        async with self._lock:
            self._purge_locked()
            state = self._unique_state()
            self._states[state] = PendingOAuth(
                provider=provider,
                user_id=user_id,
                expires_at=expires_at,
            )
            return state

    async def consume(self, state: str) -> PendingOAuth:
        if not state:
            raise InvalidOAuthStateError("OAuth state is missing")
        async with self._lock:
            pending = self._states.pop(state, None)
            if pending is None:
                raise InvalidOAuthStateError("OAuth state is invalid or already used")
            if pending.expires_at <= self._clock():
                raise InvalidOAuthStateError("OAuth state has expired")
            return pending

    def __contains__(self, state: str) -> bool:
        return state in self._states

    def _unique_state(self) -> str:
        while True:
            candidate = secrets.token_urlsafe(32)
            if candidate not in self._states:
                return candidate

    def _purge_locked(self) -> None:
        now = self._clock()
        expired = [key for key, value in self._states.items() if value.expires_at <= now]
        for key in expired:
            del self._states[key]
