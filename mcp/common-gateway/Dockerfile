FROM ghcr.io/supercorp-ai/supergateway@sha256:095acf4471e142553c1a5514aa5e480abbc5885d00549d4a3481cc70eac53889

ARG MCP_FETCH_SERVER_VERSION=2025.4.7
ARG MCP_GIT_SERVER_VERSION=2026.1.14

RUN apk add --no-cache git python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages \
        "mcp-server-fetch==${MCP_FETCH_SERVER_VERSION}" \
        "mcp-server-git==${MCP_GIT_SERVER_VERSION}" \
    && READABILIPY_JS_DIR=$(python3 -c "import inspect, os, readabilipy; print(os.path.join(os.path.dirname(inspect.getfile(readabilipy)), 'javascript'))") \
    && npm --prefix "$READABILIPY_JS_DIR" install --omit=dev \
    && git config --system --add safe.directory /projects/repo

ENTRYPOINT ["supergateway"]
