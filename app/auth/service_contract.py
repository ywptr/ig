"""Database-independent constants for service clients and token issuance."""
AUDIENCE = "ig-api"
TOKEN_TYPE = "ig-service+jwt"
MAX_TOKEN_SECONDS = 300
SCOPES = frozenset({"media:discover", "media:generate", "jobs:read", "artifacts:read", "editor:launch"})
