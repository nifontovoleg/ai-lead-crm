param([int]$Port = 8787, [switch]$EnableScoring, [switch]$KeepAlive)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$tunnelPath = Join-Path $PSScriptRoot '.tools\cloudflared.exe'
$runtimePath = Join-Path $PSScriptRoot 'data\runtime'
$statePath = Join-Path $runtimePath 'https-processes.json'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Сначала выполните SETUP.ps1' }
if (-not (Test-Path -LiteralPath $tunnelPath)) { throw 'Нужен официальный cloudflared.exe в .tools' }
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
if (Test-Path -LiteralPath $statePath) {
    $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ((Get-Process -Id $previous.api_pid -ErrorAction SilentlyContinue) -or (Get-Process -Id $previous.tunnel_pid -ErrorAction SilentlyContinue)) {
        throw 'Процессы прошлого HTTPS запуска ещё работают. Используйте STOP-HTTPS.ps1 перед перезапуском.'
    }
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $listener.Start() } finally { $listener.Stop() }
$oldMode = $env:CRM_MODE
$oldLive = $env:LIVE_PROCESSING_ENABLED
$env:CRM_MODE = 'live'
$env:LIVE_PROCESSING_ENABLED = if ($EnableScoring) { 'true' } else { 'false' }
$env:PYTHONUTF8 = '1'
try {
    $apiProcess = Start-Process -FilePath $pythonPath -WorkingDirectory $PSScriptRoot -ArgumentList @('-m','uvicorn','crm.api:app','--host','127.0.0.1','--port',"$Port",'--workers','1','--no-access-log') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'api.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'api.stderr.log')
} finally {
    $env:CRM_MODE = $oldMode
    $env:LIVE_PROCESSING_ENABLED = $oldLive
}
$ready = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($health.service -eq 'ai-lead-crm') { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
$state = @{api_pid=$apiProcess.Id; api_executable=$pythonPath; port=$Port; tunnel_pid=0; tunnel_executable=$tunnelPath; processing_enabled=[bool]$EnableScoring}
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
if (-not $ready) { throw 'API не запустился. Проверьте data/runtime/api.stderr.log; остановка: STOP-HTTPS.ps1' }
$tunnelLog = Join-Path $runtimePath ('cloudflared-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss') + '.log')
$tunnelProcess = Start-Process -FilePath $tunnelPath -WorkingDirectory $PSScriptRoot -ArgumentList @('tunnel','--no-autoupdate','--protocol','http2','--url',"http://127.0.0.1:$Port",'--logfile', ('"' + $tunnelLog + '"')) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'tunnel.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'tunnel.stderr.log')
$state.tunnel_pid = $tunnelProcess.Id
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
$publicUrl = $null
for ($attempt=0; $attempt -lt 45; $attempt++) {
    if (Test-Path -LiteralPath $tunnelLog) {
        $text = Get-Content -LiteralPath $tunnelLog -Raw
        $match = [regex]::Match($text, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) { $publicUrl = $match.Value; break }
    }
    Start-Sleep -Milliseconds 500
}
if (-not $publicUrl) { throw 'Туннель не выдал URL. Проверьте data/runtime/tunnel.stderr.log; остановка: STOP-HTTPS.ps1' }
$state.public_url = $publicUrl
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
Set-Content -LiteralPath (Join-Path $runtimePath 'public_url.txt') -Value $publicUrl -Encoding utf8
& $pythonPath -c 'from crm.config import Settings; from pathlib import Path; import sys; s=Settings.load(); p=s.data_dir/"runtime/apps-script-properties.txt"; p.write_text("CRM_API_URL="+sys.argv[1]+"\nCRM_TRIGGER_TOKEN="+s.trigger_token+"\n",encoding="utf-8"); print("Локальный файл настроек Apps Script создан; секрет не выводился")' $publicUrl
Write-Host "HTTPS адрес: $publicUrl"
Write-Host "AI обработка включена: $([bool]$EnableScoring)"
Write-Host 'Остановка обоих процессов: .\STOP-HTTPS.ps1'
if ($KeepAlive) {
    Wait-Process -Id @($apiProcess.Id, $tunnelProcess.Id)
}
