Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

param(
    [string]$EnvFile = '.env',
    [switch]$Chat,
    [switch]$ToolCalling,
    [string]$ModelId
)

$scriptRoot = Split-Path -Parent $PSCommandPath

if (-not (Test-Path -Path $EnvFile)) {
    throw "Missing env file: $EnvFile"
}

$values = @{}
Get-Content -Path $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) {
        return
    }

    $parts = $line -split '=', 2
    if ($parts.Count -ne 2) {
        return
    }

    $values[$parts[0]] = $parts[1]
}

foreach ($key in @('NGINX_LISTEN_PORT', 'OLLAMA_API_HOSTNAME', 'NGINX_API_TOKEN_FILE', 'OLLAMA_MODEL')) {
    if (-not $values.ContainsKey($key)) {
        throw "Missing required key in $EnvFile: $key"
    }
}

$token = (Get-Content -Path $values['NGINX_API_TOKEN_FILE'] -Raw).Trim()
$baseUrl = "http://127.0.0.1:$($values['NGINX_LISTEN_PORT'])"
$headers = @{
    Host = $values['OLLAMA_API_HOSTNAME']
    Authorization = "Bearer $token"
}

Write-Output 'Checking /v1/models through Nginx'
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/models" -Headers $headers | ConvertTo-Json -Depth 10

if ($Chat) {
    Write-Output 'Checking streaming /v1/chat/completions through Nginx'
    $body = @{ 
        model = $values['OLLAMA_MODEL']
        stream = $true
        messages = @(
            @{ role = 'user'; content = 'Reply with ok.' }
        )
    } | ConvertTo-Json -Depth 10 -Compress

    & curl.exe -fsS -N `
        -H "Host: $($values['OLLAMA_API_HOSTNAME'])" `
        -H "Authorization: Bearer $token" `
        -H 'Content-Type: application/json' `
        -d $body `
        "$baseUrl/v1/chat/completions"
}

if ($ToolCalling) {
    $probeModelId = $ModelId
    if (-not $probeModelId) {
        foreach ($candidate in @('OLLAMA_AGENT_MODEL_VSCODE_ID', 'OLLAMA_AGENT_MODEL', 'OLLAMA_MODEL_VSCODE_ID', 'OLLAMA_MODEL')) {
            if ($values.ContainsKey($candidate) -and $values[$candidate]) {
                $probeModelId = $values[$candidate]
                break
            }
        }
    }

    if (-not $probeModelId) {
        throw 'Unable to determine a model id for the tool-calling smoke test'
    }

    Write-Output "Checking tool calling for $probeModelId through Nginx"
    & py -3 (Join-Path $scriptRoot 'check_tool_calling.py') --env-file $EnvFile --model-id $probeModelId
}
