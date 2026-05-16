FROM ghcr.io/supercorp-ai/supergateway@sha256:095acf4471e142553c1a5514aa5e480abbc5885d00549d4a3481cc70eac53889

ARG MCP_FILESYSTEM_SERVER_VERSION=2026.1.14

RUN npm install -g "@modelcontextprotocol/server-filesystem@${MCP_FILESYSTEM_SERVER_VERSION}"

ENTRYPOINT ["supergateway"]
