#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUEUE_PORT="${VERCEL_QUEUE_DEV_PORT:-4790}"
QUEUE_URL="http://127.0.0.1:${QUEUE_PORT}"
LOG_DIR="${ROOT_DIR}/.dev-logs"

QUEUE_PID=""
POLLER_PID=""

cd "${ROOT_DIR}"

mkdir -p "${LOG_DIR}"

echo "========================================"
echo " IG Cloud Development"
echo "========================================"

#
# Load legacy/local variables first,
# then allow Vercel cloud variables to win.
#
if [[ -f ".env" ]]; then
    set -a
    source .env
    set +a
fi

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

export JOB_QUEUE_PROVIDER="vercel"

#
# Explicit local queue endpoint.
#
# export VERCEL_QUEUE_BASE_URL="${QUEUE_URL}"
export IG_QUEUE_BASE_URL="${QUEUE_URL}"

#
# Basic environment validation.
#
required_vars=(
    DATABASE_URL
    OPENAI_API_KEY
    VERCEL_REGION
    JOB_QUEUE_PROVIDER
    ARTIFACT_STORE_PROVIDER
)

for var in "${required_vars[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: ${var} is not set."
        exit 1
    fi
done

echo
echo "Environment:"
echo "  Queue provider:    ${JOB_QUEUE_PROVIDER}"
echo "  Artifact provider: ${ARTIFACT_STORE_PROVIDER}"
echo "  Vercel region:     ${VERCEL_REGION}"
echo "  Queue URL:         ${IG_QUEUE_BASE_URL}"

if [[ "${JOB_QUEUE_PROVIDER}" != "vercel" ]]; then
    echo "ERROR: JOB_QUEUE_PROVIDER must be 'vercel'."
    echo "Current value: ${JOB_QUEUE_PROVIDER}"
    exit 1
fi

#
# Avoid accidentally allowing the old RQ worker
# to consume jobs during cloud-path development.
#
if docker compose ps --services --status running \
    2>/dev/null \
    | grep -qx "ig-worker"; then

    echo
    echo "Stopping legacy ig-worker..."
    docker compose stop ig-worker >/dev/null
fi

#
# Make sure our standalone queue port is free.
#
if ss -ltn 2>/dev/null \
    | grep -q ":${QUEUE_PORT} "; then

    echo
    echo "ERROR: Port ${QUEUE_PORT} is already in use."
    echo "Check with:"
    echo "  ss -ltnp | grep ':${QUEUE_PORT}'"
    exit 1
fi

cleanup() {
    echo
    echo "Stopping IG cloud development..."

    if [[ -n "${POLLER_PID}" ]] \
        && kill -0 "${POLLER_PID}" 2>/dev/null; then
        kill "${POLLER_PID}" 2>/dev/null || true
    fi

    if [[ -n "${QUEUE_PID}" ]] \
        && kill -0 "${QUEUE_PID}" 2>/dev/null; then
        kill "${QUEUE_PID}" 2>/dev/null || true
    fi

    wait "${POLLER_PID:-}" 2>/dev/null || true
    wait "${QUEUE_PID:-}" 2>/dev/null || true

    echo "Stopped."
}

trap cleanup EXIT INT TERM

#
# 1. Standalone Vercel Queue
#
echo
echo "Starting standalone Vercel Queue..."

venv/bin/python \
    -m vercel.queue.devserver \
    --port "${QUEUE_PORT}" \
    >"${LOG_DIR}/queue.log" \
    2>&1 &

QUEUE_PID=$!

#
# Wait briefly for the queue port to become available.
#
for _ in {1..50}; do
    if (
        echo >/dev/tcp/127.0.0.1/"${QUEUE_PORT}"
    ) >/dev/null 2>&1; then
        break
    fi

    if ! kill -0 "${QUEUE_PID}" 2>/dev/null; then
        echo "ERROR: Queue devserver exited."
        cat "${LOG_DIR}/queue.log"
        exit 1
    fi

    sleep 0.1
done

if ! (
    echo >/dev/tcp/127.0.0.1/"${QUEUE_PORT}"
) >/dev/null 2>&1; then
    echo "ERROR: Queue devserver did not become ready."
    cat "${LOG_DIR}/queue.log"
    exit 1
fi

echo "  Queue ready at ${QUEUE_URL}"

#
# 2. IG Vercel Queue poller
#
echo "Starting IG queue poller..."

venv/bin/python \
    -m app.jobs.vercel_poller \
    >"${LOG_DIR}/poller.log" \
    2>&1 &

POLLER_PID=$!

sleep 1

if ! kill -0 "${POLLER_PID}" 2>/dev/null; then
    echo "ERROR: Poller exited during startup."
    cat "${LOG_DIR}/poller.log"
    exit 1
fi

echo "  Poller running."

echo
echo "Logs:"
echo "  Queue:  ${LOG_DIR}/queue.log"
echo "  Poller: ${LOG_DIR}/poller.log"
echo
echo "Starting Vercel application..."
echo "Press Ctrl-C to stop everything."
echo

#
# 3. Frontend + FastAPI
#
vercel dev
