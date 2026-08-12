"""
cache.py

Task 4.6: Provider Response Cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.services.ai.pii_sanitizer import SanitizationResult
from app.services.ai.FieldOpsAI.schemas.provider import UsageStats

logger = logging.getLogger(__name__)


# --- Custom Exceptions ---

class CacheError(Exception):
    """
    Base exception for all cache-related issues.
    """
    pass


# --- TTL Policy ---

class CacheTTLPolicy(str, Enum):
    """
    TTL configuration choices.
    """
    DYNAMIC = "dynamic"
    STATIC = "static"


# --- Schemas ---

class ProviderCacheConfig(BaseModel):
    """
    Configuration for the provider response cache.
    """
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    enabled: bool = True
    dynamic_ttl_seconds: int = Field(default=60, gt=0)
    static_ttl_seconds: int = Field(default=3600, gt=0)
    max_response_bytes: int = Field(default=10_485_760, gt=0)  # 10 MB default
    namespace_version: str = Field(default="v1", min_length=1)


class ProviderCacheRequest(BaseModel):
    """
    Typed cache request containing the context parameters and safety verification.
    """
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    tenant_id: Optional[str] = None
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    sanitized_messages: List[Dict[str, str]]
    temperature: float = Field(..., ge=0.0, le=1.0)
    max_tokens: int = Field(..., gt=0)
    ttl_policy: CacheTTLPolicy
    explicit_safety_verification: bool

    @field_validator("explicit_safety_verification")
    @classmethod
    def validate_safety(cls, v: bool) -> bool:
        if not v:
            raise ValueError("explicit_safety_verification must be True to use cache.")
        return v

    @field_validator("provider", "model", "tenant_id")
    @classmethod
    def validate_non_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not v or not v.strip():
                raise ValueError("Field must be non-blank.")
        return v

    @classmethod
    def from_sanitized_payload(
        cls,
        *,
        sanitized_result: SanitizationResult,
        provider: str,
        model: str,
        temperature: float,
        max_tokens: int,
        ttl_policy: CacheTTLPolicy,
        tenant_id: str | None = None,
    ) -> "ProviderCacheRequest":
        """
        Create a cache request only from PIISanitizer output.

        Raw strings, dictionaries and lists are not accepted.
        """

        if not isinstance(
            sanitized_result,
            SanitizationResult,
        ):
            raise TypeError(
                "sanitized_result must be a SanitizationResult "
                "produced by PIISanitizer."
            )

        sanitized_data = sanitized_result.sanitized_data

        if isinstance(sanitized_data, str):
            content = sanitized_data
        else:
            content = json.dumps(
                sanitized_data,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            )

        return cls(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            sanitized_messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            ttl_policy=ttl_policy,
            explicit_safety_verification=True,
        )

class CachedProviderResponse(BaseModel):
    """
    Structure of cached provider completion results.
    """
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    text: str = Field(min_length=1)
    usage: UsageStats


class ProviderCacheStats(BaseModel):
    """
    Statistics tracking hits, misses, writes, invalidations, and failures.
    """
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    hits: int = Field(default=0, ge=0)
    misses: int = Field(default=0, ge=0)
    writes: int = Field(default=0, ge=0)
    invalidations: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)


# --- Cache Manager ---

class ProviderCache:
    """
    Authoritative async provider response cache manager using Redis.
    Supports tenant-isolation, namespace invalidation, TTL distinction,
    and fail-open safety policies.
    """

    def __init__(
        self,
        redis_client: Any,
        config: ProviderCacheConfig = ProviderCacheConfig(),
    ) -> None:
        self.redis = redis_client
        self.config = config

        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._invalidations = 0
        self._errors = 0

    def _get_tenant_hash(self, tenant_id: str | None) -> str:
        if tenant_id is None:
            return "global"
        stripped = tenant_id.strip()
        if not stripped:
            raise ValueError("tenant_id must not be blank when supplied.")
        return hashlib.sha256(stripped.encode("utf-8")).hexdigest()

    def serialize_response(self, value: CachedProviderResponse) -> bytes:
        """
        Deterministic JSON serialization helper for payload byte calculation.
        """
        serialized = json.dumps(value.model_dump(), separators=(",", ":"))
        return serialized.encode("utf-8")

    def generate_cache_key(self, request: ProviderCacheRequest) -> str:
        """
        Generate a deterministic SHA-256 cache key using sorted JSON serialization.
        """
        tenant_hash = self._get_tenant_hash(request.tenant_id)
        provider_clean = request.provider.strip().lower()
        model_clean = request.model.strip().lower()

        # Build payload containing version, inputs, and settings
        payload = {
            "version": self.config.namespace_version,
            "provider": provider_clean,
            "model": model_clean,
            "messages": request.sanitized_messages,
            "temperature": float(request.temperature),
            "max_tokens": int(request.max_tokens),
            "ttl_policy": request.ttl_policy.value,
        }

        # Canonical JSON serialization (sorted keys, no spaces)
        serialized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()

        # Final key: fieldops:cache:{version}:{tenant_hash}:{provider}:{payload_hash}
        return f"fieldops:cache:{self.config.namespace_version}:{tenant_hash}:{provider_clean}:{payload_hash}"

    async def get(self, request: ProviderCacheRequest) -> Optional[CachedProviderResponse]:
        """
        Retrieve a cached response. Fails open on Redis errors.
        """
        if not self.config.enabled:
            return None

        # Verify request parameters implicitly validates explicit_safety_verification
        key = self.generate_cache_key(request)

        try:
            val = await self.redis.get(key)
            if val is None:
                self._misses += 1
                return None

            data = json.loads(val)
            cached_res = CachedProviderResponse(**data)
            self._hits += 1
            return cached_res
        except Exception:
            logger.warning("Redis operations in ProviderCache encountered an error (failing open).")
            self._errors += 1
            self._misses += 1
            return None

    async def set(
        self,
        request: ProviderCacheRequest,
        value: CachedProviderResponse,
    ) -> bool:
        """
        Store a verified response in the cache. Checks maximum response bytes and fails open.
        """
        if not self.config.enabled:
            return False

        key = self.generate_cache_key(request)

        try:
            payload_bytes = self.serialize_response(value)

            # Reject if payload exceeds max size (accepts exactly equal, rejects max + 1)
            if len(payload_bytes) > self.config.max_response_bytes:
                logger.warning(
                    "Cached payload size exceeds max limit."
                )
                return False

            serialized = payload_bytes.decode("utf-8")

            # Assign dynamic vs static TTL
            if request.ttl_policy == CacheTTLPolicy.DYNAMIC:
                ttl = self.config.dynamic_ttl_seconds
            else:
                ttl = self.config.static_ttl_seconds

            await self.redis.set(key, serialized, ex=ttl)
            self._writes += 1
            return True
        except Exception:
            logger.warning("Redis operations in ProviderCache encountered an error (failing open).")
            self._errors += 1
            return False

    async def delete(self, request: ProviderCacheRequest) -> bool:
        """
        Manually remove a cache key. Fails open.
        """
        if not self.config.enabled:
            return False

        key = self.generate_cache_key(request)

        try:
            res = await self.redis.delete(key)
            return bool(res)
        except Exception:
            logger.warning("Redis operations in ProviderCache encountered an error (failing open).")
            self._errors += 1
            return False

    async def invalidate_provider_namespace(
        self,
        provider: str,
        tenant_id: str | None = None,
    ) -> int:
        """
        Scan and invalidate all cache keys matching provider and tenant.
        
        Note:
        -----
        This operation is incremental (non-blocking) and NOT atomic.
        Keys are scanned and deleted in batches using SCAN.
        """
        if not self.config.enabled:
            return 0

        tenant_hash = self._get_tenant_hash(tenant_id)
        provider_clean = provider.strip().lower()

        pattern = f"fieldops:cache:{self.config.namespace_version}:{tenant_hash}:{provider_clean}:*"

        cursor = 0
        deleted_count = 0

        try:
            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await self.redis.delete(*keys)
                    deleted_count += len(keys)
                if cursor == 0:
                    break
            self._invalidations += deleted_count
            return deleted_count
        except Exception:
            logger.warning("Redis operations in ProviderCache encountered an error (failing open).")
            self._errors += 1
            return 0

    def statistics(self) -> ProviderCacheStats:
        """
        Return snapshot of hits, misses, writes, invalidations, and errors.
        """
        return ProviderCacheStats(
            hits=self._hits,
            misses=self._misses,
            writes=self._writes,
            invalidations=self._invalidations,
            errors=self._errors,
        )

class SyncProviderCache(ProviderCache):
    """
    Synchronous runtime adapter for ProviderCache.

    Uses the same:

    - cache configuration
    - cache key generation
    - serialization
    - TTL policies
    - response-size limits
    - statistics
    - privacy rules

    This adapter is used by the synchronous AIOrchestrator.
    """

    def get(
        self,
        request: ProviderCacheRequest,
    ) -> Optional[CachedProviderResponse]:
        """
        Retrieve a cached provider response.

        Cache infrastructure failures fail open and behave
        like a cache miss.
        """

        if not self.config.enabled:
            return None

        key = self.generate_cache_key(request)

        try:
            value = self.redis.get(key)

            if value is None:
                self._misses += 1
                return None

            data = json.loads(value)

            cached_response = CachedProviderResponse(
                **data
            )

            self._hits += 1

            return cached_response

        except Exception:
            logger.warning(
                "Provider cache read failed. "
                "Continuing without cached data."
            )

            self._errors += 1
            self._misses += 1

            return None

    def set(
        self,
        request: ProviderCacheRequest,
        value: CachedProviderResponse,
    ) -> bool:
        """
        Store a verified provider response synchronously.

        Cache infrastructure failures fail open and do not
        fail the provider request.
        """

        if not self.config.enabled:
            return False

        key = self.generate_cache_key(request)

        try:
            payload_bytes = self.serialize_response(
                value
            )

            if (
                len(payload_bytes)
                > self.config.max_response_bytes
            ):
                logger.warning(
                    "Cached payload size exceeds "
                    "the configured maximum."
                )

                return False

            serialized = payload_bytes.decode(
                "utf-8"
            )

            if (
                request.ttl_policy
                == CacheTTLPolicy.DYNAMIC
            ):
                ttl = (
                    self.config
                    .dynamic_ttl_seconds
                )
            else:
                ttl = (
                    self.config
                    .static_ttl_seconds
                )

            result = self.redis.set(
                key,
                serialized,
                ex=ttl,
            )

            if result is False or result is None:
                return False

            self._writes += 1

            return True

        except Exception:
            logger.warning(
                "Provider cache write failed. "
                "Continuing without caching."
            )

            self._errors += 1

            return False

    def delete(
        self,
        request: ProviderCacheRequest,
    ) -> bool:
        """
        Delete one cached provider response synchronously.
        """

        if not self.config.enabled:
            return False

        key = self.generate_cache_key(request)

        try:
            result = self.redis.delete(key)

            return bool(result)

        except Exception:
            logger.warning(
                "Provider cache deletion failed."
            )

            self._errors += 1

            return False

    def invalidate_provider_namespace(
        self,
        provider: str,
        tenant_id: str | None = None,
    ) -> int:
        """
        Delete all cache entries for one provider and tenant.

        Uses Redis SCAN rather than KEYS.
        """

        if not self.config.enabled:
            return 0

        tenant_hash = self._get_tenant_hash(
            tenant_id
        )

        if (
            not isinstance(provider, str)
            or not provider.strip()
        ):
            raise ValueError(
                "provider must be a non-blank string."
            )

        provider_clean = (
            provider.strip().lower()
        )

        pattern = (
            f"fieldops:cache:"
            f"{self.config.namespace_version}:"
            f"{tenant_hash}:"
            f"{provider_clean}:*"
        )

        cursor = 0
        deleted_count = 0

        try:
            while True:
                cursor, keys = self.redis.scan(
                    cursor,
                    match=pattern,
                    count=100,
                )

                if keys:
                    deleted = self.redis.delete(
                        *keys
                    )

                    if isinstance(deleted, int):
                        deleted_count += deleted

                if cursor == 0:
                    break

            self._invalidations += (
                deleted_count
            )

            return deleted_count

        except Exception:
            logger.warning(
                "Provider cache namespace "
                "invalidation failed."
            )

            self._errors += 1

            return 0