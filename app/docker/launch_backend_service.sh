#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

usage() {
    local exit_code=${1:-1}
    echo "Usage: $0 [ragflow]"
    echo
    echo "Without arguments, starts ragflow."
    echo "Available service types:"
    echo "  ragflow         Start the Python RAGFlow server"
    echo
    echo "Examples:"
    echo "  $0"
    echo "  $0 ragflow"
    exit "$exit_code"
}

# Function to load environment variables from .env file
load_env_file() {
    # Get the directory of the current script
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local env_file="$script_dir/.env"

    # Check if .env file exists
    if [ -f "$env_file" ]; then
        echo "Loading environment variables from: $env_file"
        # Source the .env file
        set -a
        source "$env_file"
        set +a
    else
        echo "Warning: .env file not found at: $env_file"
    fi
}

# Load environment variables
load_env_file

case "${API_PROXY_SCHEME-python}" in
    python) ;;
    *) echo "Unsupported API_PROXY_SCHEME: ${API_PROXY_SCHEME}; only python is supported" >&2; exit 1 ;;
esac
case "${DB_TYPE-mysql}" in
    mysql|MYSQL) ;;
    *) echo "Unsupported DB_TYPE: ${DB_TYPE}; only mysql is supported" >&2; exit 1 ;;
esac
case "${DOC_ENGINE-elasticsearch}" in
    elasticsearch) ;;
    *) echo "Unsupported DOC_ENGINE: ${DOC_ENGINE}; only elasticsearch is supported" >&2; exit 1 ;;
esac
case "${STORAGE_IMPL-MINIO}" in
    MINIO|minio) ;;
    *) echo "Unsupported STORAGE_IMPL: ${STORAGE_IMPL}; only MINIO is supported" >&2; exit 1 ;;
esac

# Unset HTTP proxies that might be set by Docker daemon
export http_proxy=""; export https_proxy=""; export no_proxy=""; export HTTP_PROXY=""; export HTTPS_PROXY=""; export NO_PROXY=""
export PYTHONPATH=$(pwd)

export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/

PY=python3

# Maximum number of retries for each server
MAX_RETRIES=5

# Flag to control termination
STOP=false

# Array to keep track of child PIDs
PIDS=()

# Set the path to the NLTK data directory
# download_deps.py downloads NLTK data into ragflow_deps/nltk_data (see
# ragflow_deps/download_deps.py); point NLTK_DATA there directly instead of a
# stale top-level ./nltk_data so this matches what's actually populated.
export NLTK_DATA="$(pwd)/ragflow_deps/nltk_data"

# Function to handle termination signals
cleanup() {
  echo "Termination signal received. Shutting down..."
  STOP=true
  # Terminate all child processes
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "Killing process $pid"
      kill "$pid"
    fi
  done
  exit 0
}

# Trap SIGINT and SIGTERM to invoke cleanup
trap cleanup SIGINT SIGTERM

# Function to execute ragflow_server with retry logic
run_server(){
    local server_name="ragflow_server.py"
    local -a server_cmd=("$PY" "api/ragflow_server.py")
    local retry_count=0
    while ! $STOP && [ $retry_count -lt $MAX_RETRIES ]; do
        echo "Starting $server_name (Attempt $((retry_count+1)))"
        "${server_cmd[@]}"
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 0 ]; then
            echo "$server_name exited successfully."
            break
        else
            echo "$server_name failed with exit code $EXIT_CODE. Retrying..." >&2
            retry_count=$((retry_count + 1))
            sleep 2
        fi
    done

    if [ $retry_count -ge $MAX_RETRIES ]; then
        echo "$server_name failed after $MAX_RETRIES attempts. Exiting..." >&2
        cleanup
    fi
}

ensure_db_init() {
    echo "Initializing database tables..."
    "$PY" -c "from api.db.db_models import init_database_tables as init_web_db; init_web_db()"
    echo "Database tables initialized."
}

run_mysql_migrations() {
    tools/scripts/run_migrations.sh
}

START_RAGFLOW=0

if [ $# -eq 0 ]; then
  START_RAGFLOW=1
fi

for arg in "$@"; do
  case $arg in
    ragflow|server|webserver)
      START_RAGFLOW=1
      ;;
    -h|--help)
      usage 0
      ;;
    *)
      echo "Unknown service type: $arg" >&2
      usage
      ;;
  esac
done

if [[ "$START_RAGFLOW" -eq 1 ]]; then
  ensure_db_init
  run_mysql_migrations
fi

# Start the RAGFlow server
if [[ "$START_RAGFLOW" -eq 1 ]]; then
  run_server &
  PIDS+=($!)
fi

# Wait for all background processes to finish
wait
