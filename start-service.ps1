# Start bmad-studio on 127.0.0.1:5024 (loopback only; external access goes through the reverse proxy).
# Settings are read from .env in this folder by bmad_studio.core.settings (see .env.example).
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) { $env:Path = $userPath + ';' + $env:Path }

# "python -m uvicorn" == "uvicorn": the uvicorn.exe shim in .venv is blocked by Windows Application Control here.
& uv run python -m uvicorn bmad_studio.api.app:app --host 127.0.0.1 --port 5024 --workers 1
exit $LASTEXITCODE
