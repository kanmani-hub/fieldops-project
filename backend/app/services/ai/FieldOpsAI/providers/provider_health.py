"""
provider_health.py

Task 4.4A: Provider Health Monitor and Redis-backed provider health state.

Provides distributed, Redis-backed provider health monitoring, status tracking
(HEALTHY, DEGRADED, UNHEALTHY), recovery probing, privacy-preserving state snapshots,
and asynchronous monitoring loop support.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.ai.FieldOpsAI.providers.base_provider import (
    BaseAIProvider,
)
from app.services.ai.FieldOpsAI.providers.provider_factory import ProviderFactory
from app.services.ai.FieldOpsAI.schemas.provider import ProviderHealth

logger = logging.getLogger(__name__)

SAFE_ERROR_CODE_REGEX = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


# --- Infrastructure Exception ---

class ProviderHealthInfrastructureError(Exception):
    """
    Raised when Redis or database infrastructure failures occur.
    Fails closed according to system policy.
    """
    pass


# --- Schemas ---

class ProviderHealthConfig(BaseModel):
    """
    Validated, immutable provider health configuration.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    enabled: bool = True
    check_interval_seconds: int = Field(default=30, gt=0)
    recovery_probe_seconds: int = Field(default=300, gt=0)
    degraded_after_failures: int = Field(default=1, ge=1)
    unhealthy_after_failures: int = Field(default=3, gt=0)
    state_ttl_seconds: int = Field(default=900, gt=0)
    namespace_version: str = Field(default="v1", min_length=1)

    @field_validator("namespace_version")
    @classmethod
    def validate_namespace_version(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("namespace_version must be non-blank.")
        return v.strip()

    @model_validator(mode="after")
    def validate_thresholds(self) -> ProviderHealthConfig:
        if self.unhealthy_after_failures <= self.degraded_after_failures:
            raise ValueError(
                "unhealthy_after_failures must be greater than degraded_after_failures."
            )
        if self.state_ttl_seconds <= self.recovery_probe_seconds:
            raise ValueError(
                "state_ttl_seconds must be greater than recovery_probe_seconds."
            )
        return self

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any]) -> ProviderHealthConfig:
        return cls(**mapping)


class ProviderHealthSnapshot(BaseModel):
    """
    Validated, immutable snapshot of provider health state stored in Redis.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    provider_name: str
    status: ProviderHealth
    checked_at: datetime
    last_healthy_at: Optional[datetime] = None
    last_state_change_at: Optional[datetime] = None
    next_recovery_probe_at: Optional[datetime] = None
    consecutive_successes: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    total_checks: int = Field(default=0, ge=0)
    total_successes: int = Field(default=0, ge=0)
    total_failures: int = Field(default=0, ge=0)
    total_recoveries: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    safe_error_code: Optional[str] = None

    @field_validator("provider_name")
    @classmethod
    def validate_provider_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("provider_name must be a non-blank string.")
        return v.strip().lower()

    @field_validator("latency_ms")
    @classmethod
    def validate_latency_ms(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0.0:
            raise ValueError("latency_ms must be a finite non-negative float.")
        return v

    @field_validator("checked_at", "last_healthy_at", "last_state_change_at", "next_recovery_probe_at")
    @classmethod
    def validate_utc_datetime(cls, dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None or dt.utcoffset() is None or dt.utcoffset() != timedelta(0):
            raise ValueError("Timestamps must be timezone-aware UTC datetimes with zero offset.")
        return dt

    @field_validator("safe_error_code")
    @classmethod
    def validate_safe_error_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v_clean = v.strip()
        if not v_clean:
            return None
        if not SAFE_ERROR_CODE_REGEX.match(v_clean):
            raise ValueError(
                "safe_error_code must be uppercase alphanumeric identifier with underscores up to 64 chars."
            )
        return v_clean


class ProviderHealthMetrics(BaseModel):
    """
    Aggregated performance and transition metrics for provider health monitor.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    total_checks: int = Field(default=0, ge=0)
    successful_checks: int = Field(default=0, ge=0)
    failed_checks: int = Field(default=0, ge=0)
    healthy_transitions: int = Field(default=0, ge=0)
    degraded_transitions: int = Field(default=0, ge=0)
    unhealthy_transitions: int = Field(default=0, ge=0)
    recoveries: int = Field(default=0, ge=0)
    average_latency: float = Field(default=0.0, ge=0.0)
    last_checked_time: Optional[datetime] = None

    @field_validator("last_checked_time")
    @classmethod
    def validate_last_checked_time(cls, dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None or dt.utcoffset() is None or dt.utcoffset() != timedelta(0):
            raise ValueError("last_checked_time must be timezone-aware UTC datetime with zero offset.")
        return dt


# --- Provider Health Monitor ---

class ProviderHealthMonitor:
    """
    Service responsible for checking provider health, tracking health states,
    persisting Redis snapshots, and controlling recovery probes.
    """

    def __init__(
        self,
        redis_client: Any,
        config: Optional[ProviderHealthConfig | Dict[str, Any] | Any] = None,
        clock: Optional[Callable[[], datetime | float] | Any] = None,
        provider_factory: Any = ProviderFactory,
        alert_callback: Optional[Callable[[ProviderHealthSnapshot], None]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.redis = redis_client
        if isinstance(config, ProviderHealthConfig):
            self.config = config
        elif isinstance(config, dict):
            self.config = ProviderHealthConfig.from_mapping(config)
        elif config is not None and hasattr(config, "provider_health"):
            self.config = ProviderHealthConfig.from_mapping(config.provider_health)
        else:
            self.config = ProviderHealthConfig()

        self._clock = clock
        self.provider_factory = provider_factory
        self.alert_callback = alert_callback
        self.sleep_fn = sleep_fn

        self._lock = threading.RLock()
        self._metrics_checks = 0
        self._metrics_successful = 0
        self._metrics_failed = 0
        self._metrics_healthy_transitions = 0
        self._metrics_degraded_transitions = 0
        self._metrics_unhealthy_transitions = 0
        self._metrics_recoveries = 0
        self._metrics_total_latency_ms = 0.0
        self._metrics_last_checked_time: Optional[datetime] = None

        self._provider_metrics: Dict[str, Dict[str, Any]] = {}

        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    def _now_dt(self) -> datetime:
        """
        Return current UTC datetime using injected clock if provided.
        """
        if self._clock is not None:
            if callable(self._clock):
                val = self._clock()
                if isinstance(val, datetime):
                    return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
                if isinstance(val, (int, float)):
                    return datetime.fromtimestamp(float(val), tz=timezone.utc)
            elif hasattr(self._clock, "now"):
                val = self._clock.now()
                if isinstance(val, datetime):
                    return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
                if isinstance(val, (int, float)):
                    return datetime.fromtimestamp(float(val), tz=timezone.utc)
        return datetime.now(timezone.utc)

    def _get_provider_hash(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Provider name must be a non-blank string.")
        norm_name = name.strip().lower()
        return hashlib.sha256(norm_name.encode("utf-8")).hexdigest()

    def _get_redis_key(self, name: str) -> str:
        p_hash = self._get_provider_hash(name)
        return f"fieldops:provider-health:{self.config.namespace_version}:{p_hash}"

    def get_snapshot(self, name: str) -> Optional[ProviderHealthSnapshot]:
        """
        Retrieve existing ProviderHealthSnapshot from Redis.
        Raises ProviderHealthInfrastructureError on Redis failure or malformed payload.
        """
        key = self._get_redis_key(name)
        try:
            raw_data = self.redis.get(key)
            if raw_data is None:
                return None
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8")
            return ProviderHealthSnapshot.model_validate_json(raw_data)
        except ProviderHealthInfrastructureError:
            raise
        except Exception:
            logger.warning("Redis failure or invalid snapshot structure for provider health.")
            raise ProviderHealthInfrastructureError(
                "Failed to read provider health snapshot from Redis."
            ) from None

    def _save_snapshot(self, snapshot: ProviderHealthSnapshot) -> None:
        key = self._get_redis_key(snapshot.provider_name)
        try:
            payload = snapshot.model_dump_json()
            res = self.redis.setex(key, self.config.state_ttl_seconds, payload)
            if res is False:
                raise ProviderHealthInfrastructureError("Redis setex returned failure.")
        except ProviderHealthInfrastructureError:
            raise
        except Exception:
            logger.warning("Redis failure while persisting provider health snapshot.")
            raise ProviderHealthInfrastructureError(
                "Failed to persist provider health snapshot to Redis."
            ) from None

    def should_probe(self, name: str) -> bool:
        """
        Determine whether a health probe should be executed for the provider.
        """
        norm_name = name.strip().lower()
        snapshot = self.get_snapshot(norm_name)
        if snapshot is None:
            return True

        if snapshot.status in (ProviderHealth.HEALTHY, ProviderHealth.DEGRADED):
            return True

        if snapshot.status == ProviderHealth.UNHEALTHY:
            if snapshot.next_recovery_probe_at is None:
                return True
            now_dt = self._now_dt()
            return now_dt >= snapshot.next_recovery_probe_at

        return True

    def check_provider(
        self,
        name: str,
        provider: Optional[BaseAIProvider] = None,
    ) -> ProviderHealthSnapshot:
        """
        Perform a health check for the given provider, transition state, update metrics,
        and save snapshot to Redis.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Provider name must be a non-blank string.")
        norm_name = name.strip().lower()
        now_dt = self._now_dt()

        # If provider is UNHEALTHY and recovery probe timer is active and not due, skip checking
        existing_snapshot = self.get_snapshot(norm_name)
        if (
            existing_snapshot is not None
            and existing_snapshot.status == ProviderHealth.UNHEALTHY
            and existing_snapshot.next_recovery_probe_at is not None
            and now_dt < existing_snapshot.next_recovery_probe_at
        ):
            return existing_snapshot

        # Instantiate provider if not passed
        target_provider = provider
        if target_provider is None:
            try:
                target_provider = self.provider_factory.create_provider(name=norm_name)
            except Exception:
                target_provider = None

        # Execute health check and measure latency
        is_healthy = False
        start_t = time.perf_counter()
        if target_provider is not None:
            try:
                is_healthy = bool(target_provider.health_check())
            except Exception:
                is_healthy = False
        elapsed_ms = max(0.0, (time.perf_counter() - start_t) * 1000.0)

        # Retrieve prior counters if available
        prev_snapshot = existing_snapshot
        if prev_snapshot is None:
            prev_total_checks = 0
            prev_total_successes = 0
            prev_total_failures = 0
            prev_total_recoveries = 0
            prev_consec_successes = 0
            prev_consec_failures = 0
            prev_status = None
            prev_last_healthy = None
            prev_last_state_change = None
        else:
            prev_total_checks = prev_snapshot.total_checks
            prev_total_successes = prev_snapshot.total_successes
            prev_total_failures = prev_snapshot.total_failures
            prev_total_recoveries = prev_snapshot.total_recoveries
            prev_consec_successes = prev_snapshot.consecutive_successes
            prev_consec_failures = prev_snapshot.consecutive_failures
            prev_status = prev_snapshot.status
            prev_last_healthy = prev_snapshot.last_healthy_at
            prev_last_state_change = prev_snapshot.last_state_change_at

        new_total_checks = prev_total_checks + 1
        transition_type: Optional[str] = None
        is_recovery = False

        if is_healthy:
            new_total_successes = prev_total_successes + 1
            new_total_failures = prev_total_failures
            new_consec_successes = prev_consec_successes + 1
            new_consec_failures = 0
            new_status = ProviderHealth.HEALTHY
            last_healthy = now_dt
            next_probe = None
            safe_error_code = None

            if prev_status in (ProviderHealth.DEGRADED, ProviderHealth.UNHEALTHY):
                new_total_recoveries = prev_total_recoveries + 1
                is_recovery = True
            else:
                new_total_recoveries = prev_total_recoveries

            if prev_status != ProviderHealth.HEALTHY:
                last_state_change = now_dt
                transition_type = "healthy"
            else:
                last_state_change = prev_last_state_change or now_dt
        else:
            new_total_successes = prev_total_successes
            new_total_failures = prev_total_failures + 1
            new_total_recoveries = prev_total_recoveries
            new_consec_successes = 0
            new_consec_failures = prev_consec_failures + 1
            last_healthy = prev_last_healthy
            safe_error_code = "PROVIDER_HEALTH_CHECK_FAILED"

            if new_consec_failures >= self.config.unhealthy_after_failures:
                new_status = ProviderHealth.UNHEALTHY
                next_probe = now_dt + timedelta(seconds=self.config.recovery_probe_seconds)
            elif new_consec_failures >= self.config.degraded_after_failures:
                new_status = ProviderHealth.DEGRADED
                next_probe = None
            else:
                new_status = ProviderHealth.HEALTHY
                next_probe = None

            if prev_status != new_status:
                last_state_change = now_dt
                if new_status == ProviderHealth.DEGRADED:
                    transition_type = "degraded"
                elif new_status == ProviderHealth.UNHEALTHY:
                    transition_type = "unhealthy"
                elif new_status == ProviderHealth.HEALTHY:
                    transition_type = "healthy"
            else:
                last_state_change = prev_last_state_change or now_dt

        snapshot = ProviderHealthSnapshot(
            provider_name=norm_name,
            status=new_status,
            checked_at=now_dt,
            last_healthy_at=last_healthy,
            last_state_change_at=last_state_change,
            next_recovery_probe_at=next_probe,
            consecutive_successes=new_consec_successes,
            consecutive_failures=new_consec_failures,
            total_checks=new_total_checks,
            total_successes=new_total_successes,
            total_failures=new_total_failures,
            total_recoveries=new_total_recoveries,
            latency_ms=elapsed_ms,
            safe_error_code=safe_error_code,
        )

        # Persist snapshot to Redis
        self._save_snapshot(snapshot)

        # Update global & per-provider metrics atomically under lock
        with self._lock:
            self._metrics_checks += 1
            if is_healthy:
                self._metrics_successful += 1
            else:
                self._metrics_failed += 1
            if is_recovery:
                self._metrics_recoveries += 1
            if transition_type == "healthy":
                self._metrics_healthy_transitions += 1
            elif transition_type == "degraded":
                self._metrics_degraded_transitions += 1
            elif transition_type == "unhealthy":
                self._metrics_unhealthy_transitions += 1
            self._metrics_total_latency_ms += elapsed_ms
            self._metrics_last_checked_time = now_dt

            # Per-provider metrics
            pm = self._provider_metrics.setdefault(
                norm_name,
                {
                    "total_checks": 0,
                    "successful_checks": 0,
                    "failed_checks": 0,
                    "healthy_transitions": 0,
                    "degraded_transitions": 0,
                    "unhealthy_transitions": 0,
                    "recoveries": 0,
                    "cumulative_latency_ms": 0.0,
                    "last_checked_time": None,
                },
            )
            pm["total_checks"] += 1
            if is_healthy:
                pm["successful_checks"] += 1
            else:
                pm["failed_checks"] += 1
            if is_recovery:
                pm["recoveries"] += 1
            if transition_type == "healthy":
                pm["healthy_transitions"] += 1
            elif transition_type == "degraded":
                pm["degraded_transitions"] += 1
            elif transition_type == "unhealthy":
                pm["unhealthy_transitions"] += 1
            pm["cumulative_latency_ms"] += elapsed_ms
            pm["last_checked_time"] = now_dt

        # Log safe message
        logger.info(
            "Provider health check completed for '%s': status=%s",
            norm_name,
            new_status.value,
        )

        # Invoke alert callback ONLY on meaningful state transitions (degraded, unhealthy, recovery)
        if self.alert_callback is not None:
            should_alert = (
                transition_type in ("degraded", "unhealthy") or is_recovery
            )
            if should_alert:
                try:
                    self.alert_callback(snapshot)
                except Exception:
                    logger.warning("Error executing provider health alert callback.")

        return snapshot

    def check_registered_providers(self) -> List[ProviderHealthSnapshot]:
        """
        Check health for all registered providers in ProviderFactory.
        Fails closed on ProviderHealthInfrastructureError.
        Individual non-infrastructure provider errors do not halt checking of other providers.
        """
        if not self.config.enabled:
            return []

        try:
            registered_names = self.provider_factory.registered_names()
        except ProviderHealthInfrastructureError:
            raise
        except Exception:
            logger.warning("ProviderFactory registered_names failed.")
            raise ProviderHealthInfrastructureError("Provider registry lookup failed.") from None

        snapshots: List[ProviderHealthSnapshot] = []
        for name in registered_names:
            try:
                if self.should_probe(name):
                    snap = self.check_provider(name)
                    snapshots.append(snap)
                else:
                    existing = self.get_snapshot(name)
                    if existing is not None:
                        snapshots.append(existing)
            except ProviderHealthInfrastructureError:
                raise
            except Exception:
                logger.warning("Provider health check failed for registered provider.")

        return snapshots

    def list_snapshots(self) -> List[ProviderHealthSnapshot]:
        """
        Return snapshots for all registered providers.
        Fails closed on ProviderHealthInfrastructureError.
        """
        try:
            registered_names = self.provider_factory.registered_names()
        except ProviderHealthInfrastructureError:
            raise
        except Exception:
            logger.warning("ProviderFactory registered_names failed.")
            raise ProviderHealthInfrastructureError("Provider registry lookup failed.") from None

        snapshots: List[ProviderHealthSnapshot] = []
        for name in registered_names:
            snap = self.get_snapshot(name)
            if snap is not None:
                snapshots.append(snap)

        return snapshots

    def clear(self, name: str) -> bool:
        """
        Remove snapshot from Redis for given provider.
        """
        if not isinstance(name, str) or not name.strip():
            return False
        norm_name = name.strip().lower()
        key = self._get_redis_key(norm_name)
        try:
            res = self.redis.delete(key)
            with self._lock:
                self._provider_metrics.pop(norm_name, None)
            return bool(res)
        except Exception:
            logger.warning("Redis failure while clearing provider health snapshot.")
            raise ProviderHealthInfrastructureError(
                "Failed to clear provider health snapshot from Redis."
            ) from None

    def get_metrics(self, name: Optional[str] = None) -> ProviderHealthMetrics:
        """
        Return aggregated or per-provider health metrics without making Redis calls inside lock.
        """
        with self._lock:
            if name is not None:
                norm_name = name.strip().lower() if isinstance(name, str) else ""
                pm = self._provider_metrics.get(norm_name)
                if pm is None:
                    return ProviderHealthMetrics()

                avg_lat = (
                    pm["cumulative_latency_ms"] / pm["total_checks"]
                    if pm["total_checks"] > 0
                    else 0.0
                )
                return ProviderHealthMetrics(
                    total_checks=pm["total_checks"],
                    successful_checks=pm["successful_checks"],
                    failed_checks=pm["failed_checks"],
                    healthy_transitions=pm["healthy_transitions"],
                    degraded_transitions=pm["degraded_transitions"],
                    unhealthy_transitions=pm["unhealthy_transitions"],
                    recoveries=pm["recoveries"],
                    average_latency=avg_lat,
                    last_checked_time=pm["last_checked_time"],
                )

            avg_lat = (
                self._metrics_total_latency_ms / self._metrics_checks
                if self._metrics_checks > 0
                else 0.0
            )

            return ProviderHealthMetrics(
                total_checks=self._metrics_checks,
                successful_checks=self._metrics_successful,
                failed_checks=self._metrics_failed,
                healthy_transitions=self._metrics_healthy_transitions,
                degraded_transitions=self._metrics_degraded_transitions,
                unhealthy_transitions=self._metrics_unhealthy_transitions,
                recoveries=self._metrics_recoveries,
                average_latency=avg_lat,
                last_checked_time=self._metrics_last_checked_time,
            )

    # --- Async Scheduler API ---

    async def run_once(self) -> List[ProviderHealthSnapshot]:
        """
        Execute a single monitoring cycle across registered providers.
        """
        if not self.config.enabled:
            return []
        return await asyncio.to_thread(self.check_registered_providers)

    async def start(self) -> None:
        """
        Start the background monitoring loop idempotently.
        """
        if not self.config.enabled:
            return

        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event = asyncio.Event()
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """
        Stop the background monitoring loop idempotently.
        """
        task_to_cancel = None
        with self._lock:
            if not self._running:
                return
            self._running = False
            if self._stop_event is not None:
                self._stop_event.set()
            if self._monitor_task is not None:
                task_to_cancel = self._monitor_task
                self._monitor_task = None

        if task_to_cancel is not None:
            task_to_cancel.cancel()
            try:
                await task_to_cancel
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        """
        Internal background loop executing health checks at configured intervals.
        """
        while self._running:
            try:
                await self.run_once()
            except Exception:
                logger.warning("Error during provider health monitor loop cycle.")

            interval = float(self.config.check_interval_seconds)
            if self._stop_event is not None:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    continue
            else:
                await asyncio.sleep(interval)
