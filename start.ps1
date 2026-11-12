$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run .\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'dist\index.html')) { throw 'Run .\setup.ps1 first to build the frontend.' }
Write-Host 'Presence: http://127.0.0.1:8000  |  Press Ctrl+C to stop.'
& .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
