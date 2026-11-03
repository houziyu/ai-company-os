# Runs only the isolated Phase 02 QA Compose project; does not load or write .env.
param([string[]]$ComposeArgs = @('ps'))
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:POSTGRES_USER = 'acos_p2'
$env:POSTGRES_PASSWORD = 'isolated-phase02-test-only'
$env:POSTGRES_DB = 'acos_p2'
$env:DATABASE_URL = 'postgresql+psycopg://acos_p2:isolated-phase02-test-only@postgres:5432/acos_p2'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:18083'
$env:WEB_PORT = '18083'
$env:WEB_BIND_ADDRESS = '127.0.0.1'
$env:DEEPSEEK_API_KEY = ''
# ADR-0006 data key so the UI can save a provider key in this throwaway stack.
$env:SECRET_ENCRYPTION_KEY = 'cGhhc2UwMy10ZXN0LW9ubHktMzItYnl0ZS1rZXkhISE='
& docker compose --project-directory $repoRoot --env-file "$repoRoot/.env.example" -p acos-phase02-test -f "$repoRoot/docker-compose.yml" -f "$repoRoot/deploy/compose.phase02-test.yml" @ComposeArgs
exit $LASTEXITCODE
