"""
app/services/broadcast_scheduler.py
────────────────────────────────────
BroadcastScheduler — 5-second loop that pushes live technician GPS positions
to all subscribed WebSocket clients.

Design:
  • Every 5 s query active technicians with recent GPS pings (< 60 s old)
  • Deduplicate via Redis key `broadcast:last:{tech_id}` (5-s TTL)
  • Fetch ETA from Redis cache `eta:{tech_id}:{job_id}`; fallback "calculating..."
  • Batch all updates into a single MessagePack-compressed blob
  • Publish batch to Redis channel `gps:updates` for cross-instance fan-out
  • Also broadcast directly to local ConnectionManager channels
  • Structured log per cycle: batch_size, skipped, latency_ms
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import msgpack

from ..logger import logger

if TYPE_CHECKING:
    from ..services.tracking_manager import ConnectionManager

# ── Constants ─────────────────────────────────────────────────────────────────
BROADCAST_INTERVAL_S = 5.0          # NFR-004
DEDUP_TTL_S = 5                     # seconds
GPS_STALENESS_S = 60                # GPS pings older than this are ignored
ACTIVE_JOB_STATUSES = {"ASSIGNED", "EN_ROUTE", "ON_SITE", "assigned", "en_route", "on_site"}
REDIS_GPS_CHANNEL = "gps:updates"


# ─────────────────────────────────────────────────────────────────────────────
# BroadcastScheduler
# ─────────────────────────────────────────────────────────────────────────────
class BroadcastScheduler:
    """
    Async scheduler that runs a 5-second position-broadcast loop.

    Parameters
    ----------
    db_factory : callable
        Zero-argument callable returning a new SQLAlchemy Session.
    redis_async :
        An ``redis.asyncio.Redis`` instance (async).
    manager : ConnectionManager
        The module-level ConnectionManager singleton.
    """

    def __init__(self, db_factory, redis_async, manager: "ConnectionManager") -> None:
        self.db_factory = db_factory
        self.redis = redis_async
        self.manager = manager
        self.running: bool = False
        self._task: asyncio.Task | None = None

        # Metrics (exposed via metrics endpoint)
        self.total_broadcasts: int = 0
        self.total_skipped: int = 0
        self.last_batch_size: int = 0
        self.last_latency_ms: float = 0.0

    async def start(self) -> None:
        """Start the background broadcast loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._broadcast_loop())
        logger.info("[scheduler] BroadcastScheduler started (interval=5 s)")

    async def stop(self) -> None:
        """Gracefully stop the broadcast loop."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[scheduler] BroadcastScheduler stopped")

    # ── Core Loop ─────────────────────────────────────────────────────────────

    async def _broadcast_loop(self) -> None:
        while self.running:
            t0 = time.monotonic()
            try:
                await self._run_cycle()
            except Exception as exc:
                logger.error(f"[scheduler] broadcast_cycle_failed: {exc}", exc_info=True)
            finally:
                elapsed = time.monotonic() - t0
                sleep_for = max(0.0, BROADCAST_INTERVAL_S - elapsed)
                await asyncio.sleep(sleep_for)

    async def _run_cycle(self) -> None:
        t0 = time.monotonic()
        batch: list[dict] = []
        skipped = 0

        db = self.db_factory()
        try:
            active_techs = self._query_active_technicians(db)
        finally:
            db.close()

        for row in active_techs:
            tech_id = str(row.tech_id)
            job_id = str(row.job_id)
            tenant_id = str(row.tenant_id)

            # Deduplication check
            dedup_key = f"broadcast:last:{tech_id}"
            try:
                last_raw = await self.redis.get(dedup_key)
                if last_raw:
                    skipped += 1
                    continue
            except Exception:
                pass  # Redis unavailable → allow broadcast

            # ETA from cache
            eta_data = await self._get_eta(tech_id, job_id)

            update = {
                "type": "position_update",
                "technician_id": tech_id,
                "technician_name": row.technician_name or f"Technician #{tech_id[:8]}",
                "job_id": job_id,
                "tenant_id": tenant_id,
                "latitude": float(row.latitude),
                "longitude": float(row.longitude),
                "accuracy": float(row.accuracy) if row.accuracy is not None else None,
                "altitude": float(row.altitude) if row.altitude is not None else None,
                "job_status": row.job_status,
                "eta": eta_data.get("eta", "calculating..."),
                "eta_duration_minutes": eta_data.get("duration_minutes"),
                "timestamp": row.last_ping.isoformat() if hasattr(row.last_ping, "isoformat") else str(row.last_ping),
                "broadcast_at": datetime.now(timezone.utc).isoformat(),
            }
            batch.append(update)

            # Set dedup key
            try:
                await self.redis.setex(dedup_key, DEDUP_TTL_S, "1")
            except Exception:
                pass

        latency_ms = (time.monotonic() - t0) * 1000

        if batch:
            await self._dispatch_batch(batch)

        # Update metrics
        self.total_broadcasts += len(batch)
        self.total_skipped += skipped
        self.last_batch_size = len(batch)
        self.last_latency_ms = round(latency_ms, 2)

        if batch:
            logger.info(
                f"[scheduler] cycle: batch_size={len(batch)} skipped={skipped} "
                f"latency_ms={self.last_latency_ms}"
            )
        else:
            logger.debug(
                f"[scheduler] cycle_empty: skipped={skipped} latency_ms={self.last_latency_ms}"
            )

    # ── DB Query ──────────────────────────────────────────────────────────────

    def _query_active_technicians(self, db):
        """
        Return rows for technicians that have:
          • An active job (ASSIGNED / EN_ROUTE / ON_SITE)
          • A GPS ping within the last GPS_STALENESS_S seconds

        Uses a plain SQL query via SQLAlchemy text() for flexibility across
        SQLite (tests) and PostgreSQL (production).
        """
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError, OperationalError

        # Use UTC-naive threshold for compatibility with both SQLite and PG
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=GPS_STALENESS_S)
        threshold_str = threshold.strftime("%Y-%m-%d %H:%M:%S")

        # We join on technician_id (FK) rather than tech_id because the
        # Job.assigned_technician_id is an Integer FK to technicians.technician_id.
        sql = text("""
            SELECT
                t.tech_id,
                t.technician_name,
                t.tenant_id,
                j.id            AS job_id,
                j.status        AS job_status,
                j.service_type  AS job_title,
                j.location      AS job_location,
                p.latitude,
                p.longitude,
                p.accuracy,
                p.altitude,
                MAX(p.timestamp) AS last_ping
            FROM technicians t
            JOIN jobs j
                ON j.assigned_technician_id = t.technician_id
                AND UPPER(j.status) IN ('ASSIGNED', 'EN_ROUTE', 'ON_SITE')
            JOIN gps_pings p
                ON CAST(p.technician_id AS VARCHAR) = CAST(t.tech_id AS VARCHAR)
                AND p.timestamp >= :threshold
            WHERE t.tech_id IS NOT NULL
                AND CAST(j.tenant_id AS VARCHAR) = CAST(t.tenant_id AS VARCHAR)
                AND CAST(p.tenant_id AS VARCHAR) = CAST(t.tenant_id AS VARCHAR)
            GROUP BY
                t.tech_id, t.technician_name, t.tenant_id,
                j.id, j.status, j.service_type, j.location,
                p.latitude, p.longitude, p.accuracy, p.altitude
        """)

        try:
            return db.execute(sql, {"threshold": threshold_str}).fetchall()
        except (ProgrammingError, OperationalError) as exc:
            if "gps_pings" in str(exc).lower() or "does not exist" in str(exc).lower() or "no such table" in str(exc).lower():
                logger.warning(f"[scheduler] Table 'gps_pings' does not exist yet: {exc}")
                return []
            raise

    # ── ETA Cache ─────────────────────────────────────────────────────────────

    async def _get_eta(self, tech_id: str, job_id: str) -> dict:
        """Fetch ETA from Redis cache; return fallback dict if unavailable."""
        try:
            raw = await self.redis.get(f"eta:{tech_id}:{job_id}")
            if raw:
                data = json.loads(raw)
                return {
                    "eta": data.get("eta", "calculating..."),
                    "duration_minutes": data.get("duration_minutes"),
                }
        except Exception:
            pass
        return {"eta": "calculating...", "duration_minutes": None}

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch_batch(self, batch: list[dict]) -> None:
        """Publish batch to Redis pub/sub AND broadcast directly to local channels."""
        # 1. Publish compressed batch to Redis (enables cross-instance sync)
        envelope = {
            "type": "position_batch",
            "count": len(batch),
            "updates": batch,
            "broadcast_cycle_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            compressed = msgpack.packb(envelope, use_bin_type=True)
            await self.redis.publish(REDIS_GPS_CHANNEL, compressed)
        except Exception as e:
            logger.warning(f"[scheduler] Redis publish failed: {e}")

        # 2. Broadcast to local WebSocket connections
        for update in batch:
            tenant_id = update["tenant_id"]
            tech_id = update["technician_id"]
            job_id = update["job_id"]

            tech_channel = f"tenant:{tenant_id}:technician:{tech_id}"
            job_channel = f"tenant:{tenant_id}:job:{job_id}"
            all_channel = f"tenant:{tenant_id}:all"

            await self.manager.broadcast(tech_channel, update)
            await self.manager.broadcast(job_channel, update)
            await self.manager.broadcast(all_channel, update)

    def get_metrics(self) -> dict:
        return {
            "total_broadcasts": self.total_broadcasts,
            "total_skipped": self.total_skipped,
            "last_batch_size": self.last_batch_size,
            "last_latency_ms": self.last_latency_ms,
            "broadcast_interval_s": BROADCAST_INTERVAL_S,
        }
