# VS Code Insiders Setup

This repository targets the VS Code Insiders bring-your-own-model flow for OpenAI-compatible chat endpoints.

## Prerequisites

- VS Code Insiders 1.104 or later
- GitHub Copilot access
- an active internet connection
- the stack exposed through Cloudflare Tunnel
- a valid bearer token stored in `../ai-tunnel-secrets/ollama-api-token`

Important limitations:

- this setup affects chat and inline chat, not inline suggestions
- if your Copilot Business or Enterprise policy disables BYOK, this path will not be available
- a model without tool calling may not be available in agent mode

## Recommended Setup Flow

Recommended fast path from this repo:

Windows PowerShell:

```powershell
py -3 scripts/bootstrap-vscode-user.py --env-file .env --copy-api-key
```

POSIX shell:

```sh
python3 scripts/bootstrap-vscode-user.py --env-file .env --copy-api-key
```

What this helper does:

- updates the user `github.copilot.chat.customOAIModels` entry in `settings.json`
- updates `chatLanguageModels.json` with an `AI Tunnel` provider that points at `OLLAMA_API_PUBLIC_URL`
- optionally copies the API key from `../ai-tunnel-secrets/ollama-api-token` to your clipboard

If you want to target the stable VS Code profile instead of Insiders, add `--channel stable`.

1. Open VS Code Insiders.
2. Run the bootstrap helper or one of the `VS Code: Bootstrap User Space` tasks from this repo.
3. Reload the window if VS Code was already open.
4. Open the Chat view.
5. Run `Chat: Manage Language Models` from the Command Palette.
6. Select the `AI Tunnel` provider if it already exists, or choose `Add Models` and `OpenAI Compatible` if you skipped the bootstrap helper.
7. Enter the API key as the contents of `../ai-tunnel-secrets/ollama-api-token`.
8. Add the model entry for the default profile described below if it is not already present.
9. Select the model from the chat model picker.

If you are working from this repository, you can manage the workspace model entries with [scripts/modelctl.py](scripts/modelctl.py) or the `Models: Add Or Update Model` task in [.vscode/tasks.json](.vscode/tasks.json) instead of editing the JSON by hand.

The remaining API key step is intentionally left in the UI. VS Code stores those credentials in secure storage, not in `settings.json` or `chatLanguageModels.json`, so the repo-supported automation boundary is provider registration plus clipboard handoff.

## Rotating The API Key Later

When you need a new bearer token for the tunneled endpoint:

Windows PowerShell:

```powershell
py -3 scripts/rotate-api-token.py --env-file .env --copy-to-clipboard
```

POSIX shell:

```sh
python3 scripts/rotate-api-token.py --env-file .env --copy-to-clipboard
```

What this does:

- rewrites `../ai-tunnel-secrets/ollama-api-token`
- restarts the `nginx` service so the generated auth include picks up the new token
- copies the new token to your clipboard when `--copy-to-clipboard` is used

After the helper finishes:

1. Run `Chat: Manage Language Models`.
2. Open the `AI Tunnel` provider.
3. Replace the stored API key with the new token.

## Optional Workspace Memory

This repo also supports `workspace-memory-bridge` as a local MCP memory server for Copilot.

Recommended setup from this workspace:

1. Run `Tasks: Run Task` and select `Workspace Memory: Bootstrap Bridge`.
2. If you want MemPalace initialized and mined immediately, run `Workspace Memory: Bootstrap Bridge + Palace` instead.
3. Reload the VS Code window after the bootstrap task completes.
4. Confirm `workspaceMemoryBridge` appears in the MCP server list.

Notes:

- the bridge endpoint is local to the current workspace, not part of the Cloudflare tunnel path
- the bootstrap task uses [scripts/bootstrap-workspace-memory.py](scripts/bootstrap-workspace-memory.py), which prefers a sibling checkout at `../workspace-memory-bridge` and can fall back to the public package
- [mempalace.yaml](mempalace.yaml) defines the default room layout for this repo

## Default Model Entry

Use this repo's default model profile:

- display name: `DeepSeek V2 Lite`
- model id: `deepseek-v2:16b-lite-chat-q4_K_M`
- current example Ollama tag behind the proxy: `deepseek-v2:16b-lite-chat-q4_K_M`

Optional agent-mode profile:

- the repo now preconfigures `OLLAMA_AGENT_MODEL=milkey/deepseek-v2.5-1210:IQ1_S` as the default large agent profile for machines that can host the 47 GB Ollama package
- `DeepSeek V2 Lite` remains the default chat profile and is kept chat-only because the current Ollama package does not expose tools
- keep `OLLAMA_AGENT_MODEL_TOOL_CALLING=true` only for a model that actually passes the tool-calling smoke test
- the bootstrap helper now mirrors both the default chat profile and the optional agent profile into VS Code user settings

The display label in VS Code can be friendlier than the backing package name, but the model id sent to the OpenAI-compatible endpoint needs to match the actual Ollama model id unless you add a dedicated translation layer in front of Ollama.

## Settings Snippet

For users who want a declarative model entry in user settings, this is the shape the repo should document:

```json
{
  "github.copilot.chat.customOAIModels": {
    "deepseek-v2:16b-lite-chat-q4_K_M": {
      "name": "DeepSeek V2 Lite",
      "url": "https://ollama-api.example.com/v1",
      "maxInputTokens": 32768,
      "maxOutputTokens": 8192,
      "toolCalling": false,
      "vision": false,
      "thinking": true,
      "streaming": true
    },
    "milkey/deepseek-v2.5-1210:IQ1_S": {
      "name": "DeepSeek Math V2 Large (IQ1_S)",
      "url": "https://ollama-api.example.com/v1",
      "maxInputTokens": 4096,
      "maxOutputTokens": 4096,
      "toolCalling": true,
      "vision": false,
      "thinking": true,
      "streaming": true
    }
  }
}
```

Notes:

- a base URL ending in `/v1` is appropriate here; VS Code resolves it to the chat-completions route for custom OpenAI-compatible models
- API key entry is still best handled through the Language Models editor so secrets do not end up in repo settings
- if you want to force a full API path instead of a base URL, use an explicit `/chat/completions` URL
- do not use a friendly alias as the model id unless you add a proxy layer that rewrites the request body before it reaches Ollama

## Suggested Validation

After configuration:

1. Confirm the model appears in the chat model picker.
2. Send a simple prompt and verify a response is returned.
3. Send a longer prompt and confirm streaming feels incremental instead of buffered.
4. Run `Models: Configure DeepSeek Math V2 Large Agent` for the repo default large-profile setup on a fresh machine, or use `py -3 scripts/check_tool_calling.py --env-file .env --model-id <agent-model-id>` before marking another model agent-capable.
5. If the model does not appear in agent mode, treat that as a tool-calling capability limitation rather than a tunnel failure.