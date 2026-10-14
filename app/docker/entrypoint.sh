#!/usr/bin/env bash

set -e

echo "Start RAGFlow cluster, version: "
cat /ragflow/VERSION

# -----------------------------------------------------------------------------
# Usage and command-line argument parsing
# -----------------------------------------------------------------------------
function usage() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "  --disable-webserver                     Disables the web server (nginx + ragflow_server)."
    echo "  --enable-mcpserver                      Enables the MCP server."
    echo "  --init-model-provider-tables            Run model provider table migrations and exit."
    echo "  --init-superuser                        Initializes the superuser."
    echo "  --mcp-host=<string>                     Address the MCP server binds to (default: 127.0.0.1)."
    echo "  --mcp-port=<num>                        Port the MCP server listens on (default: 9382)."
    echo "  --mcp-base-url=<string>                 RAGFlow base URL the MCP server calls (default: http://127.0.0.1:9380)."
    echo "  --mcp-script-path=<path>                MCP server entry script (default: /ragflow/mcp/server/server.py)."
    echo "  --mcp-mode=<self-host|host>             MCP server mode (default: self-host)."
    echo "  --mcp-host-api-key=<string>             API key required when --mcp-mode=self-host."
    echo "  --no-transport-sse-enabled              Disables the MCP SSE transport."
    echo "  --no-transport-streamable-http-enabled  Disables the MCP streamable HTTP transport. Disabling"
    echo "                                          both transports re-enables this one, since the server"
    echo "                                          requires at least one."
    echo "  --no-json-response                      Disables JSON responses from the MCP server."
    echo
    echo "Examples:"
    echo "  $0 --enable-mcpserver"
    echo "  $0 --init-superuser"
    exit 1
}

# DocMind cloud ingestion runs TaskHandler inside the API. There are no
# standalone queue consumers or external connector sync daemons to launch.
ENABLE_WEBSERVER=1 # Default to enable web server
ENABLE_MCP_SERVER=0
INIT_SUPERUSER_ARGS="" # Default to not initialize superuser
INIT_MODEL_PROVIDER_TABLES=0

MCP_HOST="127.0.0.1"
MCP_PORT=9382
MCP_BASE_URL="http://127.0.0.1:9380"
MCP_SCRIPT_PATH="/ragflow/mcp/server/server.py"
MCP_MODE="self-host"
MCP_HOST_API_KEY=""
MCP_TRANSPORT_SSE_FLAG="--transport-sse-enabled"
MCP_TRANSPORT_STREAMABLE_HTTP_FLAG="--transport-streamable-http-enabled"
MCP_JSON_RESPONSE_FLAG="--json-response"

# Parse arguments
for arg in "$@"; do
  case $arg in
    --disable-webserver)
      ENABLE_WEBSERVER=0
      shift
      ;;
    --enable-mcpserver)
      ENABLE_MCP_SERVER=1
      shift
      ;;
    --init-model-provider-tables)
      INIT_MODEL_PROVIDER_TABLES=1
      shift
      ;;
    --init-superuser)
      INIT_SUPERUSER_ARGS="--init-superuser"
      shift
      ;;
    --mcp-host=*)
      MCP_HOST="${arg#*=}"
      shift
      ;;
    --mcp-port=*)
      MCP_PORT="${arg#*=}"
      shift
      ;;
    --mcp-base-url=*)
      MCP_BASE_URL="${arg#*=}"
      shift
      ;;
    --mcp-mode=*)
      MCP_MODE="${arg#*=}"
      shift
      ;;
    --mcp-host-api-key=*)
      MCP_HOST_API_KEY="${arg#*=}"
      shift
      ;;
    --mcp-script-path=*)
      MCP_SCRIPT_PATH="${arg#*=}"
      shift
      ;;
    --no-transport-sse-enabled)
      MCP_TRANSPORT_SSE_FLAG="--no-transport-sse-enabled"
      shift
      ;;
    --no-transport-streamable-http-enabled)
      MCP_TRANSPORT_STREAMABLE_HTTP_FLAG="--no-transport-streamable-http-enabled"
      shift
      ;;
    --no-json-response)
      MCP_JSON_RESPONSE_FLAG="--no-json-response"
      shift
      ;;
    *)
      usage
      ;;
  esac
done

# -----------------------------------------------------------------------------
# Replace env variables in the service_conf.yaml file
# -----------------------------------------------------------------------------
CONF_DIR="/ragflow/conf"
TEMPLATE_FILE="${CONF_DIR}/service_conf.yaml.template"
CONF_FILE="${CONF_DIR}/service_conf.yaml"

rm -f "${CONF_FILE}"
DEF_ENV_VALUE_PATTERN="\$\{([^:]+):-([^}]+)\}"
while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ DEF_ENV_VALUE_PATTERN ]]; then
        varname="${BASH_REMATCH[1]}"
        default="${BASH_REMATCH[2]}"

        if [ -n "${!varname}" ]; then
            eval "echo \"$line"\" >> "${CONF_FILE}"
        else
            echo "$line" | sed -E "s/\\\$\{[^:]+:-([^}]+)\}/\1/g" >> "${CONF_FILE}"
        fi
    else
        eval "echo \"$line\"" >> "${CONF_FILE}"
    fi
done < "${TEMPLATE_FILE}"

export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu/"
PY=python3

# -----------------------------------------------------------------------------
# Select Nginx Configuration based on API_PROXY_SCHEME
# -----------------------------------------------------------------------------
NGINX_CONF_DIR="/etc/nginx/conf.d"
if [ -n "$API_PROXY_SCHEME" ]; then
    if [[ "${API_PROXY_SCHEME}" == "hybrid" ]]; then
        cp -f "$NGINX_CONF_DIR/ragflow.conf.hybrid" "$NGINX_CONF_DIR/ragflow.conf"
        echo "Applied nginx config: ragflow.conf.hybrid"
    elif [[ "${API_PROXY_SCHEME}" == "go" ]]; then
        cp -f "$NGINX_CONF_DIR/ragflow.conf.golang" "$NGINX_CONF_DIR/ragflow.conf"
        echo "Applied nginx config: ragflow.conf.golang (default)"
    else
        cp -f "$NGINX_CONF_DIR/ragflow.conf.python" "$NGINX_CONF_DIR/ragflow.conf"
        echo "Applied nginx config: ragflow.conf.python"
    fi
else
    # Default to python backend
    cp -f "$NGINX_CONF_DIR/ragflow.conf.python" "$NGINX_CONF_DIR/ragflow.conf"
    echo "Default: applied nginx config: ragflow.conf.python"
fi

# -----------------------------------------------------------------------------
# Function(s)
# -----------------------------------------------------------------------------

function start_mcp_server() {
    echo "Starting MCP Server on ${MCP_HOST}:${MCP_PORT} with base URL ${MCP_BASE_URL}..."
    "$PY" "${MCP_SCRIPT_PATH}" \
        --host="${MCP_HOST}" \
        --port="${MCP_PORT}" \
        --base-url="${MCP_BASE_URL}" \
        --mode="${MCP_MODE}" \
        --api-key="${MCP_HOST_API_KEY}" \
        "${MCP_TRANSPORT_SSE_FLAG}" \
        "${MCP_TRANSPORT_STREAMABLE_HTTP_FLAG}" \
        "${MCP_JSON_RESPONSE_FLAG}" &
}

function ensure_docling() {
    [[ "${USE_DOCLING}" == "true" ]] || { echo "[docling] disabled by USE_DOCLING"; return 0; }
    DOCLING_PIN="${DOCLING_VERSION:-==2.71.0}"
    "$PY" -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('docling') else 1)" \
      || uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://pypi.org/simple --no-cache-dir "docling${DOCLING_PIN}"
}

function ensure_db_init() {
    echo "Initializing database tables..."
    "$PY" -c "from api.db.db_models import init_database_tables as init_web_db; init_web_db()"
    echo "Database tables initialized."
}

# -----------------------------------------------------------------------------
# Start components based on flags
# -----------------------------------------------------------------------------

run_with_restart() {
  local process_name="$1"
  shift

  while true; do
    echo "Attempt to start ${process_name}..."
    set +e
    "$@"
    local exit_code=$?
    set -e
    echo "${process_name} exited with code ${exit_code}. Restarting in 1 second..."
    sleep 1
  done
}

if [[ "${INIT_MODEL_PROVIDER_TABLES}" -eq 1 ]]; then
    DB_TYPE_NORMALIZED="${DB_TYPE:-mysql}"
    DB_TYPE_NORMALIZED="${DB_TYPE_NORMALIZED,,}"
    if [[ "${DB_TYPE_NORMALIZED}" == "gaussdb" || "${DB_TYPE_NORMALIZED}" == "gauss" ]]; then
        # This migration script contains MySQL-only SQL and cannot run against
        # a GaussDB metadata database.
        echo "Skipping MySQL-specific model provider table migrations for DB_TYPE=${DB_TYPE:-mysql}."
    else
        tools/scripts/run_migrations.sh
    fi
fi

if [[ "${ENABLE_WEBSERVER}" -eq 1 ]]; then
    ensure_docling
    ensure_db_init

    echo "Starting nginx..."
    /usr/sbin/nginx -c /etc/nginx/nginx.conf

    if [[ "${API_PROXY_SCHEME}" == "hybrid" ]] || [[ "${API_PROXY_SCHEME}" == "python" ]]; then
        echo "Attempt to start RAGFlow python server..."
        run_with_restart "RAGFlow python server" "$PY" api/ragflow_server.py ${INIT_SUPERUSER_ARGS} &
    fi

    if [[ "${API_PROXY_SCHEME}" == "hybrid" ]] || [[ "${API_PROXY_SCHEME}" == "go" ]]; then
        echo "Starting RAGFlow go server..."
        run_with_restart "RAGFlow go server" bin/ragflow_server --api &
    fi
fi

if [[ "${ENABLE_MCP_SERVER}" -eq 1 ]]; then
    start_mcp_server
fi

wait
