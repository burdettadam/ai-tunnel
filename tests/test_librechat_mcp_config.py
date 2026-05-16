from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_config(name: str) -> str:
    return (REPO_ROOT / "librechat" / name).read_text(encoding="utf-8")


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


def test_github_librechat_config_adds_github_without_playwright() -> None:
    config = read_config("librechat.mcp-github.yaml")

    assert 'url: "http://mcp-fetch:${MCP_FETCH_PORT}/sse"' in config
    assert 'url: "http://mcp-git:${MCP_GIT_PORT}/sse"' in config
    assert 'url: "http://mcp-github:${MCP_GITHUB_PORT}/mcp"' in config
    assert '    - "mcp-github:8004"' in config
    assert "mcp-playwright:8005" not in config
    assert "browser-playwright" not in config


def test_all_librechat_config_adds_github_and_playwright() -> None:
    config = read_config("librechat.mcp-all.yaml")

    assert 'url: "http://mcp-fetch:${MCP_FETCH_PORT}/sse"' in config
    assert 'url: "http://mcp-git:${MCP_GIT_PORT}/sse"' in config
    assert 'url: "http://mcp-github:${MCP_GITHUB_PORT}/mcp"' in config
    assert 'url: "http://mcp-playwright:${MCP_PLAYWRIGHT_PORT}/mcp"' in config
    assert '    - "mcp-github:8004"' in config
    assert '    - "mcp-playwright:8005"' in config
