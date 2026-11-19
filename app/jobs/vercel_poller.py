import asyncio

from ig_queue.backends.vercel import create_vercel_client

from app.jobs.vercel_queue import queue_config
from app.jobs.vercel_subscriber import process_job


def get_queue_client():
    return create_vercel_client(queue_config(all_deployments=True))


async def main() -> None:
    await get_queue_client().poll_and_handle(process_job, interval=1.0)


if __name__ == "__main__":
    asyncio.run(main())
