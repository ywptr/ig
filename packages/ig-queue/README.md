# ig-queue

Internal Python package for work delivery and delayed scheduling over Redis/RQ and Vercel Queue. The consuming application's database is the durable source of truth. Commit the work record before publishing, then let the worker reload it and claim it atomically. Applications own reconciliation after a failed publish, duplicate delivery, or ambiguous provider response.

## Install

From the repository root, `pip install ./packages/ig-queue`. The package can also be built into a wheel for private distribution. It requires Python 3.11 or later. IG uses Python 3.13.

## Publish and consume

```python
from ig_queue import Delivery, RQConfig, RQRetry, VercelQueueConfig, create_queue_backend

rq_queue = create_queue_backend(RQConfig(
    redis_url=redis_url,
    queue_name="orders",
    handler="orders.worker:handle_delivery",
    job_timeout_seconds=600,
    continuation_retry=RQRetry(max_retries=3, interval_seconds=30),
))
vercel_queue = create_queue_backend(VercelQueueConfig(
    topic="orders",
    base_url=queue_api_url,
    oidc_token=oidc_token,
    region=region,
))

# After committing the corresponding application state.
receipt = rq_queue.enqueue(Delivery.initial(order_id))
vercel_queue.schedule(Delivery.continuation(order_id, checkpoint), delay_seconds=30)
```

Both backends accept the same `Delivery` and return a `DeliveryReceipt` with the work ID, delivery ID, and optional backend transport ID. Delays use whole seconds, with positive fractional values rounded up. The Vercel SDK can return no transport ID for a deferred or deduplicated publish. A receipt describes publication and carries no durable work status.

The RQ handler is an importable synchronous function taking a `Delivery`. Install the worker application and package in the worker environment and start RQ with its scheduler enabled, for example `rq worker --with-scheduler --url "$ORDERS_REDIS_URL" orders`. This also enables interval-based transport retries. The application controls worker startup.

Vercel consumers register their own subscriber and pass the message and handler to the package. Deployment registration and retry limits live with the consuming application. [Vercel Python SDK](https://vercel.com/docs/queues/python-sdk)

```python
from ig_queue.backends.vercel import dispatch_vercel
from vercel.queue import subscribe
from orders.worker import handle_delivery

@subscribe(topic="orders", consumer_group="orders-worker", max_attempts=3)
async def process_order(message):
    await dispatch_vercel(message, handle_delivery)
```

`dispatch_vercel` validates the message and runs the synchronous handler in a worker thread. Exceptions propagate to the SDK for transport retry. A synchronous publisher can use either backend from a worker thread. Async callers can use `VercelBackend.schedule_async`. The package reads no application environment variables. Omitted Vercel connection settings delegate to the SDK's platform defaults.

## Identity and retries

The generic envelope contains `work_id`, `delivery_id`, `kind` and an optional `continuation_token`. The token is an opaque application checkpoint locator. Keep provider state, credentials and media in application-controlled storage.

`Delivery.initial` uses the work ID as a stable transport key. `Delivery.continuation` and `Delivery.recovery` allocate fresh keys for deliberate republishing. Reuse a `Delivery` to preserve its transport identity across publish attempts. Vercel uses it for transport deduplication subject to the service window. RQ assigns an independent job ID per enqueue and may deliver duplicates. Client submission idempotency, stale work detection, checkpoint validation, and provider retry or resubmission policy remain application decisions.

## Capabilities and cancellation

| Capability | Redis/RQ | Vercel Queue |
| --- | --- | --- |
| Delayed delivery | RQ scheduler | SDK publish delay |
| Pending retraction by work ID | Best effort via Redis tracking | Unsupported |
| Running termination | Unsupported | Unsupported |
| Publish deduplication by delivery ID | No | Service window |

`cancel(work_id)` returns a status and count. RQ reports `retracted`, `already_running`, `not_found`, or `error` for a persistent concurrent update. If some tracked deliveries were retracted, the count reflects those removals. Tracking expires and publication may race retraction. Infrastructure failures can raise. Vercel reports `unsupported`. Commit an application cancellation fence first and check it at execution and completion.

IG uses the package through compatibility adapters for its existing queue names, message shapes, and worker entrypoints. Sandcastle V2 can use the generic envelope and configure its own queue, worker, durable work records and recovery policy. The standalone consumer test models this integration with a durable-record fake and each backend's SDK boundary mocked.
