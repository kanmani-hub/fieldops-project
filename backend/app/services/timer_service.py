
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class TimerService:
    """
    Technician acceptance timers.

    Broadcast dispatch:
        One independent timer per (job_id, technician_id).

    Legacy dispatch:
        Existing job-only timer keys remain supported.

    Redis is used for fast timer lookup, countdown/TTL,
    and warning processing.

    The database remains authoritative for assignment state
    and whether an offer has actually expired.
    """

    ACCEPTANCE_DURATION_SECONDS = 5 * 60
    WARNING_BEFORE_SECONDS = 120

    TIMER_PREFIX = "job:timer:"
    WARNING_PREFIX = "job:timer_warning:"
    WARNED_PREFIX = "job:timer_warned:"

    # ---------------------------------------------------------
    # Redis key builders
    # ---------------------------------------------------------

    @classmethod
    def _scope(cls, job_id: str, technician_id=None) -> str:
        """
        Return a unique timer scope.

        Legacy:
            job:timer:105

        Broadcast:
            job:timer:105:tech:7
        """
        scope = str(job_id)

        if technician_id is not None:
            scope = f"{scope}:tech:{technician_id}"

        return scope

    @classmethod
    def _timer_key(cls, job_id: str, technician_id=None) -> str:
        return f"{cls.TIMER_PREFIX}{cls._scope(job_id, technician_id)}"

    @classmethod
    def _warning_key(cls, job_id: str, technician_id=None) -> str:
        return f"{cls.WARNING_PREFIX}{cls._scope(job_id, technician_id)}"

    @classmethod
    def _warned_key(cls, job_id: str, technician_id=None) -> str:
        return f"{cls.WARNED_PREFIX}{cls._scope(job_id, technician_id)}"

    # ---------------------------------------------------------
    # Start timer
    # ---------------------------------------------------------

    @classmethod
    def start_timer(
        cls,
        redis_client,
        job_id: str,
        tech_id: str,
        duration_seconds: int = ACCEPTANCE_DURATION_SECONDS,
    ) -> bool:
        """
        Start or restart one technician's acceptance timer.

        Each technician receives independent timer and warning keys.
        Starting another technician's timer does not overwrite this one.
        """

        if redis_client is None:
            logger.warning(
                "TimerService: Redis unavailable for job=%s technician=%s",
                job_id,
                tech_id,
            )
            return False

        try:
            duration_seconds = int(duration_seconds)
        except (TypeError, ValueError):
            duration_seconds = cls.ACCEPTANCE_DURATION_SECONDS

        duration_seconds = max(1, duration_seconds)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=duration_seconds)

        warning_seconds = max(
            0,
            duration_seconds - cls.WARNING_BEFORE_SECONDS,
        )

        warning_at = now + timedelta(seconds=warning_seconds)

        timer_data = {
            "job_id": str(job_id),
            "tech_id": str(tech_id) if tech_id is not None else None,
            "started_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "duration_seconds": duration_seconds,
        }

        warning_data = {
            "job_id": str(job_id),
            "tech_id": str(tech_id) if tech_id is not None else None,
            "warning_at": warning_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        timer_key = cls._timer_key(job_id, tech_id)
        warning_key = cls._warning_key(job_id, tech_id)
        warned_key = cls._warned_key(job_id, tech_id)

        try:
            redis_client.setex(
                timer_key,
                duration_seconds,
                json.dumps(timer_data),
            )

            if warning_seconds > 0:
                redis_client.setex(
                    warning_key,
                    warning_seconds,
                    json.dumps(warning_data),
                )
            else:
                redis_client.delete(warning_key)

            redis_client.delete(warned_key)

            logger.info(
                "TimerService: Started acceptance timer "
                "job=%s technician=%s duration=%ss expires=%s",
                job_id,
                tech_id,
                duration_seconds,
                expires_at.isoformat(),
            )

            return True

        except Exception:
            logger.exception(
                "TimerService: Failed to start timer "
                "job=%s technician=%s",
                job_id,
                tech_id,
            )
            return False

    # ---------------------------------------------------------
    # Cancel timer
    # ---------------------------------------------------------

    @classmethod
    def cancel_timer(
        cls,
        redis_client,
        job_id: str,
        tech_id=None,
    ) -> bool:
        """
        Cancel timer keys.

        If tech_id is supplied:
            Cancels only that technician's broadcast offer timer.

        If tech_id is omitted:
            Cancels the legacy job-only timer.

        IMPORTANT:
            Omitting tech_id does not cancel every broadcast timer.
        """

        if redis_client is None:
            return False

        keys = (
            cls._timer_key(job_id, tech_id),
            cls._warning_key(job_id, tech_id),
            cls._warned_key(job_id, tech_id),
        )

        try:
            deleted_count = 0

            # RedisCacheManager.delete accepts one key at a time.
            for key in keys:
                result = redis_client.delete(key)
                deleted_count += int(result or 0)

            logger.info(
                "TimerService: Cancelled timer "
                "job=%s technician=%s deleted=%s",
                job_id,
                tech_id,
                deleted_count,
            )

            return deleted_count > 0

        except Exception:
            logger.exception(
                "TimerService: Failed to cancel timer "
                "job=%s technician=%s",
                job_id,
                tech_id,
            )
            return False

    # ---------------------------------------------------------
    # Read timer
    # ---------------------------------------------------------

    @classmethod
    def get_timer(
        cls,
        redis_client,
        job_id: str,
        tech_id=None,
    ) -> Optional[dict]:
        """
        Read one timer payload.

        None means the Redis timer is missing or unavailable.
        It does not prove that the database offer has expired.
        """

        if redis_client is None:
            return None

        try:
            raw = redis_client.get(
                cls._timer_key(job_id, tech_id)
            )

            if raw is None:
                return None

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            data = json.loads(raw)

            if not isinstance(data, dict):
                return None

            return data

        except Exception:
            logger.exception(
                "TimerService: Failed to read timer "
                "job=%s technician=%s",
                job_id,
                tech_id,
            )
            return None

    # ---------------------------------------------------------
    # Remaining time
    # ---------------------------------------------------------

    @classmethod
    def get_remaining_seconds(
        cls,
        redis_client,
        job_id: str,
        tech_id=None,
    ) -> Optional[int]:
        """
        Return Redis TTL for one timer.

        None means the timer is unavailable or missing.
        """

        if redis_client is None:
            return None

        try:
            ttl = redis_client.ttl(
                cls._timer_key(job_id, tech_id)
            )

            if ttl is None or ttl < 0:
                return None

            return int(ttl)

        except Exception:
            logger.exception(
                "TimerService: Failed to read TTL "
                "job=%s technician=%s",
                job_id,
                tech_id,
            )
            return None

    # ---------------------------------------------------------
    # Active timer check
    # ---------------------------------------------------------

    @classmethod
    def is_timer_active(
        cls,
        redis_client,
        job_id: str,
        tech_id=None,
    ) -> bool:
        """
        Check whether one timer key exists in Redis.

        This is not an authoritative database expiration check.
        """

        if redis_client is None:
            return False

        try:
            return bool(
                redis_client.exists(
                    cls._timer_key(job_id, tech_id)
                )
            )

        except Exception:
            logger.exception(
                "TimerService: Failed checking timer "
                "job=%s technician=%s",
                job_id,
                tech_id,
            )
            return False
