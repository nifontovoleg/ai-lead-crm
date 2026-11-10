$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Сначала запустите SETUP.ps1'
}
& $pythonPath -m crm.cli demo
if ($LASTEXITCODE -ne 0) { throw 'Демонстрация не завершена' }
Start-Process -FilePath (Join-Path $PSScriptRoot 'data\demo\dashboard.html')
