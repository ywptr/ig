"""Transitional preview worker imports and command entry point."""
from ig_core.artifacts.preview_worker import *  # noqa: F403
from ig_core.artifacts.preview_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
