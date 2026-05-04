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
- updates `chatLanguageModels.json` with an `AI Tunnel` OpenAI Compatible (`customoai`) provider and the repo-managed models
- removes stale `AI Tunnel`/`CustomOAI` provider entries and empty `OpenAI Compatible` scaffolds
- installs a tiny local VS Code extension that reads `../ai-tunnel-secrets/ollama-api-token` and stores it through VS Code's language-model SecretStorage path on reload
- optionally copies the API key from `../ai-tunnel-secrets/ollama-api-token` to your clipboard as a manual fallback
- clears the Copilot Chat CustomOAI BYOK migration flag (see "Why The Reset Is Necessary" below)

If you want to target the stable VS Code profile instead of Insiders, add `--channel stable`. Pass `--no-reset-byok-migration` if you want to keep the current Copilot BYOK state untouched.

1. Open VS Code Insiders.
2. Run the bootstrap helper or one of the `VS Code: Bootstrap User Space` tasks from this repo.
3. Reload the window if VS Code was already open.
4. Open the Chat view.
5. Select `AI Tunnel` from the chat model picker.
6. If you want to inspect the generated provider, run `Chat: Manage Language Models` from the Command Palette and open `AI Tunnel`.

If you are working from this repository, you can manage the workspace model entries with [scripts/modelctl.py](scripts/modelctl.py) or the `Models: Add Or Update Model` task in [.vscode/tasks.json](.vscode/tasks.json) instead of editing the JSON by hand.

VS Code still stores the credential in secure storage rather than plaintext JSON. The repo automates that by installing `ai-tunnel.byok-bootstrap`, a local startup extension under the VS Code user extensions directory. On activation it reads the token file, invokes VS Code's internal `lm.addLanguageModelsProviderGroup` command, and lets VS Code replace the plaintext token with a `${input:chat.lm.secret...}` placeholder in `chatLanguageModels.json`.

## Why The Reset Is Necessary

In Copilot Chat 0.47.x, `github.copilot.chat.customOAIModels` is registered in the extension's deprecated configuration namespace. On activation, the `CustomOAI` BYOK provider runs a one-time migration that copies entries from that setting into Copilot's internal BYOK provider list and gates the operation with a flag in the extension's `globalState`.

The bundled extension constructs that gate key by interpolating the *config object* itself, so it ends up as the literal string `copilot-byok-migration-CustomOAI-[object Object]`. Once that flag flips to `true`, the migration never runs again — even if you later edit `customOAIModels` to fix a URL, add a new model, or change capabilities. The result is that the `AI Tunnel` provider and its models do not appear in the chat model picker after a reload, even though the JSON in `settings.json` is correct.

By contrast, Ollama appears reliably because Copilot Chat ships a separate built-in BYOK provider that auto-discovers models from `chat.byok.ollamaEndpoint` (default `http://localhost:11434/api/tags`) on every reload — no migration, no settings entries, no API key. If you have `ollama` installed locally, it will always show up regardless of this repo's state.

The bootstrap helper now clears the buggy migration flag at the end of every run and writes the current `customoai` provider directly into `chatLanguageModels.json`. You can also run the reset on its own with the `VS Code: Reset Copilot BYOK Migration` task or `scripts/reset-byok-migration.py`. Close VS Code Insiders before running the reset so the SQLite database in `globalStorage/state.vscdb` is not locked.

The startup extension handles the secure-storage step automatically. If the extension cannot activate because Copilot BYOK is disabled by policy, `AI Tunnel` will remain visible in JSON but no token placeholder will be created.

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

1. Re-run `scripts/bootstrap-vscode-user.py --env-file .env` or the `VS Code: Bootstrap User Space` task.
2. Reload VS Code Insiders so the local BYOK bootstrap extension refreshes the provider token in SecretStorage.

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

Shared local catalog entries also ship for Gemma 4 weight profiles:

- `gemma4:e2b` as a lightweight edge profile
- `gemma4:e4b` as the pinned convenience Gemma profile for this repo
- `gemma4:26b` as the workstation MoE profile
- `gemma4:31b` as the workstation dense profile

Optional agent-mode profile:

- the repo now preconfigures `OLLAMA_AGENT_MODEL=milkey/deepseek-v2.5-1210:IQ1_S` as the default large agent profile for machines that can host the 47 GB Ollama package
- `DeepSeek V2 Lite` remains the default chat profile and is kept chat-only because the current Ollama package does not expose tools
- keep `OLLAMA_AGENT_MODEL_TOOL_CALLING=true` only for a model that actually passes the tool-calling smoke test
- use `Stack: Pull All Local Models` or `scripts/pull_all_models.py` if you want the entire shared local catalog pulled into Ollama before validation
- `gemma4:e2b` now passes the local tool-calling probe through the model-router compatibility shim and can be exposed in agent mode
- `gemma4:e4b` now passes the local tool-calling probe through the model-router compatibility shim and can be exposed in agent mode
- `gemma4:26b` now passes the local tool-calling probe through the model-router compatibility shim and can be exposed in agent mode on the current GPU-backed stack
- `gemma4:31b` now passes the local tool-calling probe through the model-router compatibility shim and can be exposed in agent mode on the current GPU-backed stack
- cloud-backed Ollama tags such as `:cloud` and cloud-only aliases such as `deepseek-v4-pro` are intentionally unsupported in this repo
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
    },
    "gemma4:e2b": {
      "name": "Gemma 4 E2B (Edge)",
      "url": "https://ollama-api.example.com/v1",
      "maxInputTokens": 131072,
      "maxOutputTokens": 8192,
      "toolCalling": true,
      "vision": true,
      "thinking": true,
      "streaming": true
    },
    "gemma4:e4b": {
      "name": "Gemma 4 E4B (Edge)",
      "url": "https://ollama-api.example.com/v1",
      "maxInputTokens": 131072,
      "maxOutputTokens": 8192,
      "toolCalling": true,
      "vision": true,
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
4. If you want a small known-good proof first, run `Models: Pull + Smoke Test Proof Model (qwen2.5:3b)`.
5. If you want the pinned Gemma profile locally, run `Models: Pull + Register Gemma 4 E4B` and `Models: Pull + Smoke Test Gemma 4 E4B`.
6. If you want every shared local catalog model available first, run `Stack: Pull All Local Models`.
7. Run `Models: Configure DeepSeek Math V2 Large Agent` for the repo default large-profile setup on a fresh machine, or use `py -3 scripts/check_tool_calling.py --env-file .env --model-id <agent-model-id>` before marking another model agent-capable.
8. If you want the pinned Gemma profile in agent mode, run `Models: Probe Gemma 4 E4B Tool Calling` as a local recheck and then select `gemma4:e4b` from the model picker.
9. If you want another Gemma 4 profile in agent mode, probe that exact tag first and only then promote it with `Models: Add Or Update Agent Model`.
10. If the model does not appear in agent mode, treat that as a tool-calling capability limitation rather than a tunnel failure.