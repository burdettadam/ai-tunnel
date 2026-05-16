import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_config(name: str) -> str:
    return (REPO_ROOT / "librechat" / name).read_text(encoding="utf-8")


def test_librechat_configs_fetch_filtered_router_models() -> None:
    for name in ["librechat.yaml", "librechat.mcp-common.yaml", "librechat.mcp-playwright.yaml"]:
        config = read_config(name)
        assert "fetch: true" in config
        assert '          - "qwen2.5:3b"' in config
        assert '"${OLLAMA_MODEL}"' not in config
        assert '"${OLLAMA_AGENT_MODEL}"' not in config


def test_smoke_model_is_not_in_default_router_profile() -> None:
    catalog = json.loads((REPO_ROOT / "models" / "catalog.json").read_text(encoding="utf-8"))
    smoke_model = next(model for model in catalog["models"] if model["id"] == "qwen2.5:0.5b")

    assert "local-small" not in smoke_model["profiles"]
    assert "local-medium" not in smoke_model["profiles"]
    assert "server-gpu" not in smoke_model["profiles"]
    assert "local-smoke" in smoke_model["profiles"]


def test_base_librechat_config_keeps_core_mcp_servers_only() -> None:
    config = read_config("librechat.yaml")

    assert "mcp-fetch:8002" not in config
    assert "mcp-git:8003" not in config
    assert "mcp-github:8004" not in config
    assert "mcp-playwright:8005" not in config


def test_common_librechat_config_adds_fetch_and_git_only() -> None:
    config = read_config("librechat.mcp-common.yaml")

    assert 'url: "http://mcp-fetch:${MCP_FETCH_PORT}/sse"' in config
    assert 'url: "http://mcp-git:${MCP_GIT_PORT}/sse"' in config
    assert '    - "mcp-fetch:8002"' in config
    assert '    - "mcp-git:8003"' in config
    assert "mcp-github:8004" not in config
    assert "mcp-playwright:8005" not in config


def test_playwright_librechat_config_adds_playwright_without_github() -> None:
    config = read_config("librechat.mcp-playwright.yaml")

    assert 'url: "http://mcp-fetch:${MCP_FETCH_PORT}/sse"' in config
    assert 'url: "http://mcp-git:${MCP_GIT_PORT}/sse"' in config
    assert 'url: "http://mcp-playwright:${MCP_PLAYWRIGHT_PORT}/mcp"' in config
    assert '    - "mcp-playwright:8005"' in config
    assert "mcp-github:8004" not in config
    assert "github-readonly" not in config


def test_token_backed_github_mcp_configs_are_not_present() -> None:
    removed_paths = [
        "compose.mcp-github.yaml",
        "compose.mcp-all.yaml",
        "librechat/librechat.mcp-github.yaml",
        "librechat/librechat.mcp-all.yaml",
    ]

    for removed_path in removed_paths:
        assert not (REPO_ROOT / removed_path).exists()
