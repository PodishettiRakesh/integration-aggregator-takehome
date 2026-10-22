from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from app.core.errors import OpenBaoError, OpenBaoNotFoundError, OpenBaoUnavailableError
from app.integrations.openbao_oauth import OpenBaoOAuthClient
from app.models.requests import AsyncRequestAccepted
from app.services.oauth_service import creds_name
from app.services.provider_registry import ProviderRegistry
from app.services.request_manager import RequestManager

logger = logging.getLogger(__name__)


class TokenService:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        openbao: OpenBaoOAuthClient,
        requests: RequestManager,
    ) -> None:
        self._registry = registry
        self._openbao = openbao
        self._requests = requests
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, provider: str, user_id: str) -> AsyncRequestAccepted:
        meta = self._registry.get(provider)
        record = await self._requests.create(meta.name, user_id)
        self._spawn(self._fulfill(record.request_id, meta.name, user_id))
        logger.info("Accepted token request provider=%s", meta.name)
        return AsyncRequestAccepted(request_id=record.request_id)

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fulfill(self, request_id: str, provider: str, user_id: str) -> None:
        started = await self._requests.mark_in_progress(request_id)
        if not started:
            return
        try:
            token = await self._openbao.read_creds(creds_name(provider, user_id))
            await self._requests.mark_succeeded(request_id, token)
        except OpenBaoNotFoundError:
            await self._requests.mark_failed(request_id, "Credentials not found")
        except OpenBaoUnavailableError:
            await self._requests.mark_failed(request_id, "OpenBao is unavailable")
        except OpenBaoError:
            await self._requests.mark_failed(request_id, "OpenBao request failed")
        except Exception:
            logger.exception("Token request failed provider=%s", provider)
            await self._requests.mark_failed(request_id, "Token retrieval failed")
