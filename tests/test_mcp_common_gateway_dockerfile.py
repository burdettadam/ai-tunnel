from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_common_gateway_dockerfile_preinstalls_readabilipy_js_dependencies() -> None:
    dockerfile = (REPO_ROOT / "mcp" / "common-gateway" / "Dockerfile").read_text(encoding="utf-8")

    assert 'mcp-server-fetch==${MCP_FETCH_SERVER_VERSION}' in dockerfile
    assert 'readabilipy' in dockerfile
    assert 'READABILIPY_JS_DIR=' in dockerfile
    assert 'npm --prefix "$READABILIPY_JS_DIR" install --omit=dev' in dockerfile