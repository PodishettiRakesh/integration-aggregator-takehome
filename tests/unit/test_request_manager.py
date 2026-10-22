import pytest

from app.core.errors import RequestNotFoundError
from app.models.requests import RequestState
from app.models.tokens import TokenPayload
from app.services.request_manager import RequestManager


def _token() -> TokenPayload:
    return TokenPayload(access_token="tok-live", token_type="Bearer", expire_time="t")


@pytest.mark.asyncio
async def test_status_transitions_and_token_only_on_success() -> None:
    manager = RequestManager(ttl_seconds=60)
    record = await manager.create("github", "alice")
    assert record.status is RequestState.PENDING
    assert record.to_response().access_token is None

    assert await manager.mark_in_progress(record.request_id) is True
    in_progress = await manager.get(record.request_id)
    assert in_progress.status is RequestState.IN_PROGRESS
    assert in_progress.to_response().access_token is None

    await manager.mark_succeeded(record.request_id, _token())
    done = await manager.get(record.request_id)
    assert done.status is RequestState.SUCCEEDED
    assert done.to_response().access_token == "tok-live"
    assert "tok-live" not in repr(done)


@pytest.mark.asyncio
async def test_failed_hides_token_and_redacts_error() -> None:
    manager = RequestManager(ttl_seconds=60)
    record = await manager.create("github", "alice")
    await manager.mark_failed(record.request_id, "access_token=tok-live")
    response = (await manager.get(record.request_id)).to_response()
    assert response.status is RequestState.FAILED
    assert response.access_token is None
    assert response.error is not None
    assert "tok-live" not in response.error


@pytest.mark.asyncio
async def test_terminal_state_is_not_overwritten() -> None:
    manager = RequestManager(ttl_seconds=60)
    record = await manager.create("github", "alice")
    await manager.mark_succeeded(record.request_id, _token())
    assert await manager.mark_in_progress(record.request_id) is False
    await manager.mark_failed(record.request_id, "should-not-apply")
    done = await manager.get(record.request_id)
    assert done.status is RequestState.SUCCEEDED
    assert done.token is not None


@pytest.mark.asyncio
async def test_unknown_and_expired_records() -> None:
    clock = {"now": 0.0}
    manager = RequestManager(ttl_seconds=1, clock=lambda: clock["now"])
    with pytest.raises(RequestNotFoundError):
        await manager.get("missing")
    record = await manager.create("github", "alice")
    await manager.mark_failed(record.request_id, "boom")
    clock["now"] = 5.0
    with pytest.raises(RequestNotFoundError):
        await manager.get(record.request_id)
