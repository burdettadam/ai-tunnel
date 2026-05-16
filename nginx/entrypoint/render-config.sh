#!/bin/sh
set -eu

template_dir=/opt/nginx
include_dir=/etc/nginx/includes

require_file() {
    if [ ! -f "$1" ]; then
        echo "Missing required file: $1" >&2
        exit 1
    fi
}

is_true() {
    case "$(printf '%s' "${1:-false}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

require_file "$NGINX_API_TOKEN_FILE"

api_token=$(tr -d '\r\n' < "$NGINX_API_TOKEN_FILE")
if [ -z "$api_token" ]; then
    echo "NGINX API token file is empty: $NGINX_API_TOKEN_FILE" >&2
    exit 1
fi

escaped_api_token=$(printf '%s' "$api_token" | sed 's/[\\"]/\\&/g')

mkdir -p /etc/nginx/conf.d "$include_dir"

cat > "$include_dir/api-auth.inc" <<EOF
if (\$http_authorization != "Bearer $escaped_api_token") {
    return 401 '{"error":{"message":"Unauthorized","type":"invalid_request_error"}}';
}
EOF

if is_true "${ENABLE_ADMIN_BASIC_AUTH:-true}"; then
    require_file "$NGINX_BASIC_AUTH_FILE"
    cat > "$include_dir/admin-auth.inc" <<'EOF'
auth_basic "Restricted";
auth_basic_user_file /run/secrets/nginx-htpasswd;
EOF
else
    cat > "$include_dir/admin-auth.inc" <<'EOF'
# Admin basic auth disabled.
EOF
fi

cat > "$include_dir/proxy-common.inc" <<EOF
proxy_http_version 1.1;
proxy_set_header Host \$host;
proxy_set_header Authorization \$http_authorization;
proxy_set_header X-Real-IP \$remote_addr;
proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto \$scheme;
proxy_set_header Connection "";
proxy_buffering off;
proxy_request_buffering off;
proxy_read_timeout ${NGINX_PROXY_READ_TIMEOUT}s;
proxy_send_timeout ${NGINX_PROXY_SEND_TIMEOUT}s;
proxy_connect_timeout 30s;
EOF

openai_proxy_pass_target="http://model-router:${MODEL_ROUTER_PORT:-11436}"

if is_true "${ENABLE_OPENAI_COMPAT_API:-true}"; then
    cat > "$include_dir/openai-api-location.inc" <<EOF
location /v1/ {
    default_type application/json;
    limit_req zone=api_limit burst=${NGINX_RATE_LIMIT_BURST} nodelay;
    include /etc/nginx/includes/api-auth.inc;
    include /etc/nginx/includes/proxy-common.inc;
    set \$openai_upstream "${openai_proxy_pass_target}";
    proxy_pass \$openai_upstream;
}
EOF

    cat > "$include_dir/openai-admin-location.inc" <<EOF
location /v1/ {
    default_type application/json;
    limit_req zone=api_limit burst=${NGINX_RATE_LIMIT_BURST} nodelay;
    include /etc/nginx/includes/proxy-common.inc;
    set \$openai_upstream "${openai_proxy_pass_target}";
    proxy_pass \$openai_upstream;
}
EOF
else
    cat > "$include_dir/openai-api-location.inc" <<'EOF'
# OpenAI-compatible API disabled.
EOF

    cat > "$include_dir/openai-admin-location.inc" <<'EOF'
# OpenAI-compatible API disabled.
EOF
fi

raw_proxy_pass_target="http://ollama:${OLLAMA_PORT:-11434}"

if is_true "${ENABLE_RAW_OLLAMA_API:-true}"; then
    cat > "$include_dir/raw-api-location-api.inc" <<EOF
location /api/ {
    default_type application/json;
    limit_req zone=api_limit burst=${NGINX_RATE_LIMIT_BURST} nodelay;
    include /etc/nginx/includes/api-auth.inc;
    include /etc/nginx/includes/proxy-common.inc;
    set \$ollama_upstream "${raw_proxy_pass_target}";
    proxy_pass \$ollama_upstream;
}
EOF

    cat > "$include_dir/raw-api-location-admin.inc" <<EOF
location /api/ {
    default_type application/json;
    limit_req zone=api_limit burst=${NGINX_RATE_LIMIT_BURST} nodelay;
    include /etc/nginx/includes/proxy-common.inc;
    set \$ollama_upstream "${raw_proxy_pass_target}";
    proxy_pass \$ollama_upstream;
}
EOF
else
    cat > "$include_dir/raw-api-location-api.inc" <<'EOF'
# Raw Ollama API disabled.
EOF

    cat > "$include_dir/raw-api-location-admin.inc" <<'EOF'
# Raw Ollama API disabled.
EOF
fi

envsubst '${OLLAMA_PORT} ${NGINX_RATE_LIMIT_RPS}' \
    < "$template_dir/nginx.conf.template" \
    > /etc/nginx/nginx.conf

envsubst '${NGINX_LISTEN_PORT} ${OLLAMA_API_HOSTNAME} ${OLLAMA_ADMIN_HOSTNAME} ${NGINX_CLIENT_MAX_BODY_SIZE}' \
    < "$template_dir/ollama.conf.template" \
    > /etc/nginx/conf.d/ollama.conf

nginx -t
exec nginx -g 'daemon off;'
