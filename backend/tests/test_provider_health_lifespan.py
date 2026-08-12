"""
test_provider_health_lifespan.py

Unit test suite for FastAPI lifespan integration of ProviderHealthMonitor (Task 4.4C).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

import app.main
from app.main import lifespan
from app.services.ai.FieldOpsAI.runtime.orchestrator import ai_orchestrator


async def fake_gps_listener(client):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


@pytest.mark.anyio
async def test_health_monitor_starts_and_stops_in_lifespan() -> None:
    app_inst = FastAPI(lifespan=lifespan)

    mock_start = AsyncMock()
    mock_stop = AsyncMock()

    mock_scheduler_inst = MagicMock()
    mock_scheduler_inst.start = AsyncMock()
    mock_scheduler_inst.stop = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch.object(ai_orchestrator.provider_health_monitor, "start", mock_start), \
         patch.object(ai_orchestrator.provider_health_monitor, "stop", mock_stop), \
         patch("app.main.aioredis.Redis", return_value=mock_redis), \
         patch("app.main.BroadcastScheduler", return_value=mock_scheduler_inst), \
         patch("app.main.redis_gps_listener", side_effect=fake_gps_listener), \
         patch("app.main.start_scheduler"), \
         patch("app.main.stop_scheduler"), \
         patch("app.main.seed_default_templates"):

        async with lifespan(app_inst):
            mock_start.assert_awaited_once()
            mock_stop.assert_not_called()

        mock_stop.assert_awaited_once()
        mock_scheduler_inst.stop.assert_awaited_once()
        assert mock_redis.aclose.await_count >= 1


@pytest.mark.anyio
async def test_lifespan_idempotency_and_graceful_shutdown() -> None:
    app_inst = FastAPI(lifespan=lifespan)

    mock_start = AsyncMock(side_effect=Exception("Start failed gracefully"))
    mock_stop = AsyncMock()

    mock_scheduler_inst = MagicMock()
    mock_scheduler_inst.start = AsyncMock()
    mock_scheduler_inst.stop = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch.object(ai_orchestrator.provider_health_monitor, "start", mock_start), \
         patch.object(ai_orchestrator.provider_health_monitor, "stop", mock_stop), \
         patch("app.main.aioredis.Redis", return_value=mock_redis), \
         patch("app.main.BroadcastScheduler", return_value=mock_scheduler_inst), \
         patch("app.main.redis_gps_listener", side_effect=fake_gps_listener), \
         patch("app.main.start_scheduler"), \
         patch("app.main.stop_scheduler"), \
         patch("app.main.seed_default_templates"):

        async with lifespan(app_inst):
            mock_start.assert_awaited_once()

        mock_stop.assert_awaited_once()


@pytest.mark.anyio
async def test_monitor_stops_before_redis_close() -> None:
    app_inst = FastAPI(lifespan=lifespan)

    events = []

    async def mock_stop():
        events.append("monitor_stop")

    async def mock_aclose():
        events.append("redis_close")

    mock_scheduler_inst = MagicMock()
    mock_scheduler_inst.start = AsyncMock()
    mock_scheduler_inst.stop = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock(side_effect=mock_aclose)

    with patch.object(ai_orchestrator.provider_health_monitor, "start", new_callable=AsyncMock), \
         patch.object(ai_orchestrator.provider_health_monitor, "stop", side_effect=mock_stop), \
         patch("app.main.aioredis.Redis", return_value=mock_redis), \
         patch("app.main.BroadcastScheduler", return_value=mock_scheduler_inst), \
         patch("app.main.redis_gps_listener", side_effect=fake_gps_listener), \
         patch("app.main.start_scheduler"), \
         patch("app.main.stop_scheduler"), \
         patch("app.main.seed_default_templates"):

        async with lifespan(app_inst):
            pass

    assert "monitor_stop" in events
    assert "redis_close" in events
    assert events.index("monitor_stop") < events.index("redis_close")


@pytest.mark.anyio
async def test_no_raw_lifespan_exception_in_logs(caplog) -> None:
    app_inst = FastAPI(lifespan=lifespan)

    mock_scheduler_inst = MagicMock()
    mock_scheduler_inst.start = AsyncMock()
    mock_scheduler_inst.stop = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch.object(ai_orchestrator.provider_health_monitor, "start", side_effect=RuntimeError("Secret redis error")), \
         patch.object(ai_orchestrator.provider_health_monitor, "stop", side_effect=RuntimeError("Secret stop error")), \
         patch("app.main.aioredis.Redis", return_value=mock_redis), \
         patch("app.main.BroadcastScheduler", return_value=mock_scheduler_inst), \
         patch("app.main.redis_gps_listener", side_effect=fake_gps_listener), \
         patch("app.main.start_scheduler"), \
         patch("app.main.stop_scheduler"), \
         patch("app.main.seed_default_templates"):

        with caplog.at_level(logging.WARNING):
            async with lifespan(app_inst):
                pass

        assert "Secret redis error" not in caplog.text
        assert "Secret stop error" not in caplog.text


def test_no_health_task_starts_on_import() -> None:
    with patch("asyncio.create_task") as mock_create_task:
        importlib.reload(app.main)
        mock_create_task.assert_not_called()
