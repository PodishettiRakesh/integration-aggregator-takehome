import pytest

from app.core.errors import InvalidOAuthStateError
from app.services.oauth_state import OAuthStateStore


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_create_returns_unique_urlsafe_states() -> None:
    store = OAuthStateStore(ttl_seconds=60)
    first = await store.create("github", "alice")
    second = await store.create("github", "alice")
    assert first != second
    assert first in store
    assert second in store


@pytest.mark.asyncio
async def test_consume_returns_binding_once() -> None:
    store = OAuthStateStore(ttl_seconds=60)
    state = await store.create("github", "alice")
    pending = await store.consume(state)
    assert pending.provider == "github"
    assert pending.user_id == "alice"
    assert state not in store
    with pytest.raises(InvalidOAuthStateError):
        await store.consume(state)


@pytest.mark.asyncio
async def test_missing_and_unknown_state_are_invalid() -> None:
    store = OAuthStateStore(ttl_seconds=60)
    with pytest.raises(InvalidOAuthStateError):
        await store.consume("")
    with pytest.raises(InvalidOAuthStateError):
        await store.consume("no-such-state")


@pytest.mark.asyncio
async def test_expired_state_cannot_be_consumed() -> None:
    clock = FakeClock(now=10.0)
    store = OAuthStateStore(ttl_seconds=5, clock=clock)
    state = await store.create("github", "alice")
    clock.now = 16.0
    with pytest.raises(InvalidOAuthStateError, match="expired"):
        await store.consume(state)
    with pytest.raises(InvalidOAuthStateError):
        await store.consume(state)
