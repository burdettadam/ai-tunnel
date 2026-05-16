FROM python:3.12-slim

ARG WORKSPACE_MEMORY_BRIDGE_REF=1114f5156e8dcdb8be6695ee790a77f95a179c53

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    "https://github.com/burdettadam/workspace-memory-bridge/archive/${WORKSPACE_MEMORY_BRIDGE_REF}.zip"

WORKDIR /workspace
EXPOSE 39741

ENTRYPOINT ["python", "-m", "workspace_memory_bridge.http_server"]
