#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/.dev-logs"

WORKER_PID=""

cd "${ROOT_DIR}"

mkdir -p "${LOG_DIR}"

echo "========================================"
echo " IG Cloud Development — Upstash/RQ"
echo "========================================"

#
# Load base environment first.
#
if [[ -f ".env" ]]; then
    set -a
    source .env
    set +a
fi

#
# Cloud/local overrides win.
#
# Cloud execution is deliberately selected; never auto-load production dotenv files.
if [[ -z "${IG_CLOUD_ENV_FILE:-}" || ! -f "${IG_CLOUD_ENV_FILE}" ]]; then
    echo "Set IG_CLOUD_ENV_FILE to the intentionally selected cloud environment file."
    exit 1
fi
set -a
source "${IG_CLOUD_ENV_FILE}"
set +a

# Check before starting a worker, queue consumer, or application against this target.
"${IG_ADMIN_PYTHON:-${ROOT_DIR}/.venv/bin/python}" -m app.ops.database_safety "$@"

#
# Validate required configuration.
#
required_vars=(
    DATABASE_URL
    OPENAI_API_KEY
    REDIS_URL
    JOB_QUEUE_PROVIDER
    ARTIFACT_STORE_PROVIDER
)

for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: ${var} is not set."
        exit 1
    fi
done

if [[ "${JOB_QUEUE_PROVIDER}" != "rq" ]]; then
    echo "ERROR: JOB_QUEUE_PROVIDER must be 'rq'."
    echo "Current value: ${JOB_QUEUE_PROVIDER}"
    exit 1
fi

echo
echo "Environment:"
echo "  Queue provider:    ${JOB_QUEUE_PROVIDER}"
echo "  Artifact provider: ${ARTIFACT_STORE_PROVIDER}"
echo "  Redis:             configured"
echo "  Database:          configured"

#
# Stop old Docker RQ worker if still running.
#
if docker compose ps --services --status running \
    2>/dev/null \
    | grep -qx "ig-worker"; then

    echo
    echo "Stopping legacy Docker ig-worker..."
    docker compose stop ig-worker >/dev/null
fi

cleanup() {
    echo
    echo "Stopping IG cloud development..."

    if [[ -n "${WORKER_PID}" ]] \
        && kill -0 "${WORKER_PID}" 2>/dev/null; then

        kill "${WORKER_PID}" 2>/dev/null || true
    fi

    wait "${WORKER_PID:-}" 2>/dev/null || true

    echo "Stopped."
}

trap cleanup EXIT INT TERM

#
# Start RQ worker against external Redis.
#
echo
echo "Starting RQ worker..."

venv/bin/rq worker \
    --url "${REDIS_URL}" \
    ig \
    >"${LOG_DIR}/rq-worker.log" \
    2>&1 &

WORKER_PID=$!

sleep 1

if ! kill -0 "${WORKER_PID}" 2>/dev/null; then
    echo "ERROR: RQ worker exited during startup."
    cat "${LOG_DIR}/rq-worker.log"
    exit 1
fi

echo "  RQ worker running."

echo
echo "Logs:"
echo "  Worker: ${LOG_DIR}/rq-worker.log"

echo
echo "Starting Vercel application..."
echo "Press Ctrl-C to stop everything."
echo

#
# Start frontend + FastAPI.
#
vercel dev