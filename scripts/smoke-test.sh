#!/usr/bin/env sh
set -eu

env_file=.env
run_chat=false
run_tool_calling=false
model_id=

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

while [ "$#" -gt 0 ]; do
    case "$1" in
        --env-file)
            shift
            env_file="$1"
            ;;
        --chat)
            run_chat=true
            ;;
        --tool-calling)
            run_tool_calling=true
            ;;
        --model-id)
            shift
            model_id="$1"
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
done

if [ ! -f "$env_file" ]; then
    echo "Missing env file: $env_file" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

api_token=$(tr -d '\r\n' < "$NGINX_API_TOKEN_FILE")
base_url="http://127.0.0.1:${NGINX_LISTEN_PORT}"

echo "Checking /v1/models through Nginx"
curl -fsS \
    -H "Host: ${OLLAMA_API_HOSTNAME}" \
    -H "Authorization: Bearer ${api_token}" \
    "${base_url}/v1/models"
echo

if [ "$run_chat" = true ]; then
    echo "Checking streaming /v1/chat/completions through Nginx"
    curl -fsS -N \
        -H "Host: ${OLLAMA_API_HOSTNAME}" \
        -H "Authorization: Bearer ${api_token}" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${OLLAMA_MODEL}\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with ok.\"}]}" \
        "${base_url}/v1/chat/completions"
    echo
fi

if [ "$run_tool_calling" = true ]; then
    probe_model_id="$model_id"
    if [ -z "$probe_model_id" ]; then
        for candidate in "${OLLAMA_AGENT_MODEL_VSCODE_ID-}" "${OLLAMA_AGENT_MODEL-}" "${OLLAMA_MODEL_VSCODE_ID-}" "${OLLAMA_MODEL-}"; do
            if [ -n "$candidate" ]; then
                probe_model_id="$candidate"
                break
            fi
        done
    fi

    if [ -z "$probe_model_id" ]; then
        echo "Unable to determine a model id for the tool-calling smoke test" >&2
        exit 1
    fi

    echo "Checking tool calling for ${probe_model_id} through Nginx"
    python3 "${script_dir}/check_tool_calling.py" --env-file "$env_file" --model-id "$probe_model_id"
fi
