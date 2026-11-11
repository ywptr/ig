"""Trusted worker execution identity, never taken from caller-provided input."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionIdentity:
    job_id: str
    execution_id: str


_current = ContextVar("ig_execution_identity", default=None)


def current_execution() -> ExecutionIdentity | None:
    return _current.get()


@contextmanager
def execution_scope(job_id: str, execution_id: str):
    token = _current.set(ExecutionIdentity(job_id, execution_id))
    try:
        yield
    finally:
        _current.reset(token)
