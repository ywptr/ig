from .config import RQConfig, VercelQueueConfig
from .contracts import QueueBackend


def create_queue_backend(config: RQConfig | VercelQueueConfig) -> QueueBackend:
    if isinstance(config, RQConfig):
        from .backends.rq import RQBackend

        return RQBackend(config)
    if isinstance(config, VercelQueueConfig):
        from .backends.vercel import VercelBackend

        return VercelBackend(config)
    raise TypeError(f"Unsupported queue configuration: {type(config).__name__}")
