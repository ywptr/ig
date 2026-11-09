#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/.dev-logs"
LOG_FILE="${LOG_DIR}/rq-worker.log"

WORKER_PID=""

cd "${ROOT_DIR}"

mkdir -p "${LOG_DIR}"

echo "========================================"
echo " IG Cloud Development — Redis/RQ Worker"
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

if [[ ! -x "venv/bin/rq" ]]; then
    echo "ERROR: venv/bin/rq was not found or is not executable."
    echo "Activate/install the Python environment before starting the worker."
    exit 1
fi

echo
echo "Environment:"
echo "  Queue provider:    ${JOB_QUEUE_PROVIDER}"
echo "  Artifact provider: ${ARTIFACT_STORE_PROVIDER}"
echo "  Redis:             configured"
echo "  Database:          configured"

#
# Stop legacy Docker RQ worker if Docker is available
# and the old worker is still running.
#
if command -v docker >/dev/null 2>&1; then
    if docker compose ps --services --status running 
        2>/dev/null | grep -qx "ig-worker"; then

        echo
        echo "Stopping legacy Docker ig-worker..."
        docker compose stop ig-worker >/dev/null
    fi
fi

cleanup() {
    local exit_code=$?

    echo
    echo "Stopping RQ worker..."

    if [[ -n "${WORKER_PID}" ]] \
        && kill -0 "${WORKER_PID}" 2>/dev/null; then

        kill "${WORKER_PID}" 2>/dev/null || true
        wait "${WORKER_PID}" 2>/dev/null || true
    fi

    echo "Stopped."

    exit "${exit_code}"
}

trap cleanup EXIT INT TERM

#
# Start RQ worker against external Redis.
#
echo
echo "Starting RQ worker..."
echo "  Queue: ig"
echo "  Log:   ${LOG_FILE}"

: > "${LOG_FILE}"

venv/bin/rq worker 
    --url "${REDIS_URL}" 
    ig 
    >>"${LOG_FILE}" 2>&1 &

WORKER_PID=$!

sleep 1

if ! kill -0 "${WORKER_PID}" 2>/dev/null; then
    echo
    echo "ERROR: RQ worker exited during startup."
    echo
    cat "${LOG_FILE}"
    exit 1
fi

echo
echo "RQ worker is running."
echo "PID: ${WORKER_PID}"
echo
echo "Press Ctrl+C to stop."
echo

#
# Keep this script attached to the worker.
#
wait "${WORKER_PID}"
