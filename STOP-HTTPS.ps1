$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot 'data\runtime\https-processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'Нет сохранённых процессов HTTPS'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
foreach ($entry in @(@{Pid=$state.tunnel_pid;Expected=$state.tunnel_executable;Role='tunnel'}, @{Pid=$state.api_pid;Expected=$state.api_executable;Role='api'})) {
    if (-not $entry.Pid) { continue }
    $ownedProcess = Get-Process -Id $entry.Pid -ErrorAction SilentlyContinue
    if (-not $ownedProcess) { continue }
    if ($ownedProcess.Path -ne $entry.Expected) { throw "PID $($entry.Pid) принадлежит другому приложению; остановка отменена" }
    if ($entry.Role -eq 'api' -and $state.api_launcher) {
        $apiInfo = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$entry.Pid)
        $launcherInfo = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$state.api_parent_pid)
        if (-not $apiInfo -or -not $launcherInfo -or $apiInfo.ParentProcessId -ne $state.api_parent_pid -or $launcherInfo.ExecutablePath -ne $state.api_launcher -or $apiInfo.CommandLine -notlike '*run_api.py*') {
            throw 'Не удалось подтвердить принадлежность API процессу venv этой CRM; остановка отменена'
        }
    }
    Stop-Process -Id $entry.Pid -ErrorAction Stop
}
Write-Host 'Процессы API и туннеля этого проекта остановлены'
