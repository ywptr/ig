"""IG#0001 wire constants, checked against the server contract in tests."""
AUDIENCE = "ig-api"
TOKEN_TYPE = "ig-service+jwt"
MAX_TOKEN_SECONDS = 300
SCOPES = frozenset({"media:discover", "media:generate", "jobs:read", "artifacts:read"})
ACTIVE_STATES = frozenset({"queued", "running", "waiting", "polling", "cancel_requested"})
TERMINAL_STATES = frozenset({"completed", "failed", "canceled"})
