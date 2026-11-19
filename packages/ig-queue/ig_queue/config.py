from dataclasses import dataclass, field


@dataclass(frozen=True)
class RQRetry:
    max_retries: int
    interval_seconds: int = 0


@dataclass(frozen=True)
class RQConfig:
    redis_url: str = field(repr=False)
    queue_name: str
    handler: str | None = None
    job_timeout_seconds: int = 600
    continuation_retry: RQRetry | None = None
    tracking_prefix: str | None = None
    tracking_ttl_seconds: int = 7 * 24 * 3600


@dataclass(frozen=True)
class VercelQueueConfig:
    topic: str
    base_url: str | None = None
    oidc_token: str | None = field(default=None, repr=False)
    region: str | None = None
    all_deployments: bool = False
