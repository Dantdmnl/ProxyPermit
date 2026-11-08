param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$DemoRoot = Join-Path $RepoRoot ".demo\windows"
$DemoData = Join-Path $DemoRoot "data"
$WhitelistPath = Join-Path $DemoData "whitelist.txt"
$SyncSecretPath = Join-Path $DemoData "sync-secret.key"
$NonceStorePath = Join-Path $DemoData "sync-nonces.json"
$SessionStatePath = Join-Path $DemoData "session-state.json"
$ManualSyncStatePath = Join-Path $DemoData "manual-sync.json"
$SyncStatePath = Join-Path $DemoData "sync-state.json"
$WhitelistEntryLogPath = Join-Path $DemoData "whitelist_entry_log.json"
$TinyproxyConfigPath = Join-Path $DemoData "tinyproxy.conf"

New-Item -ItemType Directory -Path $DemoData -Force | Out-Null

if (-not (Test-Path $WhitelistPath)) {
    @(
        "# Demo whitelist"
        "^github\.com(:[0-9]+)?$"
        "^(.+\.)?microsoft\.com(:[0-9]+)?$"
        "^api\.example\.com(:[0-9]+)?$"
    ) | Set-Content -Path $WhitelistPath -Encoding UTF8
}

if (-not (Test-Path $SyncSecretPath)) {
    $secret = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
    Set-Content -Path $SyncSecretPath -Value $secret -Encoding ASCII
}

if (-not (Test-Path $TinyproxyConfigPath)) {
    $filterConfigPath = $WhitelistPath.Replace('\', '/')
    @(
        '# Windows demo: this configuration is not activated'
        'Port 3128'
        'Listen 127.0.0.1'
        'Allow 127.0.0.1'
        'ConnectPort 443'
        "Filter `"$filterConfigPath`""
        'FilterType ere'
        'FilterDefaultDeny Yes'
        'FilterURLs No'
    ) | Set-Content -Path $TinyproxyConfigPath -Encoding UTF8
}

$env:ENABLE_AUTH = "false"
$env:DEPLOYMENT_MODE = "standalone"
$env:TINYPROXY_CONFIG_PATH = $TinyproxyConfigPath
$env:FLASK_SECRET_KEY = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
$env:WHITELIST_PATH = $WhitelistPath
$env:TINYPROXY_SERVICE = "tinyproxy"
$env:SYNC_SECRET_PATH = $SyncSecretPath
$env:SYNC_SECRET_FILE = $SyncSecretPath
$env:NONCE_STORE_FILE = $NonceStorePath
$env:SESSION_STATE_FILE = $SessionStatePath
$env:MANUAL_SYNC_STATE_FILE = $ManualSyncStatePath
$env:SYNC_STATE_FILE = $SyncStatePath
$env:ALLOW_LOCALHOST_SYNC = "true"
$env:DEMO_MODE = "true"
$env:WHITELIST_ENTRY_LOG_PATH = $WhitelistEntryLogPath

$pythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

Write-Host "Starting ProxyPermit demo on Windows..."
Write-Host "URL: http://$HostAddress`:$Port"
Write-Host "Whitelist: $WhitelistPath"

Push-Location $RepoRoot
try {
    & $pythonExe -m flask --app wsgi:app run --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
