param(
    [string]$EnvFile = '.env',
    [switch]$Chat,
    [switch]$ToolCalling,
    [string]$ModelId,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$pendingArgs = @()
if ($EnvFile.StartsWith('--')) {
    $pendingArgs += $EnvFile
    $EnvFile = '.env'
}

if ($ModelId) {
    if ($ModelId.StartsWith('--') -or $pendingArgs.Count -gt 0) {
        $pendingArgs += $ModelId
        $ModelId = ''
    }
}

if ($CliArgs) {
    $pendingArgs += @($CliArgs | Where-Object { $_ })
}

if ($pendingArgs.Count -gt 0) {
    $index = 0
    while ($index -lt $pendingArgs.Count) {
        $arg = $pendingArgs[$index]
        switch ($arg) {
            '--env-file' {
                if ($index + 1 -ge $pendingArgs.Count) {
                    throw 'Missing value for --env-file'
                }

                $EnvFile = $pendingArgs[$index + 1]
                $index += 2
                continue
            }
            '--chat' {
                $Chat = $true
                $index += 1
                continue
            }
            '--tool-calling' {
                $ToolCalling = $true
                $index += 1
                continue
            }
            '--model-id' {
                if ($index + 1 -ge $pendingArgs.Count) {
                    throw 'Missing value for --model-id'
                }

                $ModelId = $pendingArgs[$index + 1]
                $index += 2
                continue
            }
            default {
                throw "Unknown argument: $arg"
            }
        }
    }
}

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
        throw "Missing required key in ${EnvFile}: $key"
    }
}

$token = (Get-Content -Path $values['NGINX_API_TOKEN_FILE'] -Raw).Trim()
$baseUrl = "http://127.0.0.1:$($values['NGINX_LISTEN_PORT'])"
$chatModelId = $ModelId
if (-not $chatModelId) {
    $chatModelId = $values['OLLAMA_MODEL']
}
$headers = @{
    Host = $values['OLLAMA_API_HOSTNAME']
    Authorization = "Bearer $token"
}

Write-Output 'Checking /v1/models through Nginx'
Invoke-RestMethod -Method Get -Uri "$baseUrl/v1/models" -Headers $headers | ConvertTo-Json -Depth 10

if ($Chat) {
    Write-Output 'Checking streaming /v1/chat/completions through Nginx'
    $body = @{ 
        model = $chatModelId
        stream = $true
        messages = @(
            @{ role = 'user'; content = 'Reply with ok.' }
        )
    } | ConvertTo-Json -Depth 10 -Compress

    $bodyPath = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($bodyPath, $body, (New-Object System.Text.UTF8Encoding $false))

        & curl.exe -fsS -N `
            -H "Host: $($values['OLLAMA_API_HOSTNAME'])" `
            -H "Authorization: Bearer $token" `
            -H 'Content-Type: application/json' `
            --data-binary "@$bodyPath" `
            "$baseUrl/v1/chat/completions"
        if ($LASTEXITCODE -ne 0) {
            throw "Streaming chat smoke test failed with curl exit code $LASTEXITCODE"
        }
    }
    finally {
        if (Test-Path -Path $bodyPath) {
            Remove-Item -Path $bodyPath -Force
        }
    }
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
