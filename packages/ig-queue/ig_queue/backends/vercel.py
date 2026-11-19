from collections.abc import Callable, Mapping
from functools import partial

import anyio
from vercel.queue import ALL_DEPLOYMENTS, QueueClient

from ..capabilities import QueueCapabilities
from ..config import VercelQueueConfig
from ..contracts import CancelResult, CancelStatus, DeliveryReceipt, whole_second_delay
from ..delivery import Delivery, DeliveryDecoder, DeliveryHandler


def create_vercel_client(config: VercelQueueConfig) -> QueueClient:
    options = {
        key: value
        for key, value in (
            ("base_url", config.base_url),
            ("region", config.region),
            ("token", config.oidc_token),
        )
        if value is not None
    }
    if config.all_deployments:
        options["deployment"] = ALL_DEPLOYMENTS
    return QueueClient(**options)


async def dispatch_vercel(
    message: Mapping[str, object],
    handler: DeliveryHandler,
    *,
    decode: DeliveryDecoder = Delivery.from_message,
) -> None:
    # Let synchronous handlers publish further work while the SDK renews its lease.
    await anyio.to_thread.run_sync(handler, decode(message))


class VercelBackend:
    capabilities = QueueCapabilities(
        delayed_delivery=True,
        retract_pending=False,
        terminate_running=False,
        track_by_work_id=False,
        deduplicate_delivery_id=True,
    )

    def __init__(
        self,
        config: VercelQueueConfig,
        *,
        client_factory: Callable[[], QueueClient] | None = None,
        encode: Callable[[Delivery], Mapping[str, object]] = Delivery.to_message,
    ):
        self.config = config
        self.client_factory = client_factory or partial(create_vercel_client, config)
        self.encode = encode

    def enqueue(self, delivery: Delivery) -> DeliveryReceipt:
        return self.schedule(delivery, delay_seconds=0)

    def schedule(self, delivery: Delivery, *, delay_seconds: float) -> DeliveryReceipt:
        return anyio.run(partial(self.schedule_async, delivery, delay_seconds=delay_seconds))

    async def schedule_async(self, delivery: Delivery, *, delay_seconds: float = 0) -> DeliveryReceipt:
        delay = whole_second_delay(delay_seconds)
        transport_id = await self.client_factory().send(
            self.config.topic,
            self.encode(delivery),
            idempotency_key=delivery.delivery_id,
            delay=delay,
        )
        return DeliveryReceipt(delivery.work_id, delivery.delivery_id, transport_id)

    def cancel(self, work_id: str) -> CancelResult:
        return CancelResult(CancelStatus.UNSUPPORTED)
