# Start bmad-studio on 127.0.0.1:5024 (loopback only; external access goes through the reverse proxy).
# Settings are read from .env in this folder by bmad_studio.core.settings (see .env.example).
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) { $env:Path = $userPath + ';' + $env:Path }

# Build the web UI once if web/dist is missing (served by FastAPI at /).
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'web/dist/index.html'))) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'web/node_modules'))) {
        & npm --prefix web ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & npm --prefix web run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# "python -m uvicorn" == "uvicorn": the uvicorn.exe shim in .venv is blocked by Windows Application Control here.
& uv run python -m uvicorn bmad_studio.api.app:app --host 127.0.0.1 --port 5024 --workers 1
exit $LASTEXITCODE
