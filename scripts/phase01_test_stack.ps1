# Runs only the isolated QA Compose project; does not load or write .env.
param([string[]]$ComposeArgs = @('ps'))
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:POSTGRES_USER = 'acos_test'
$env:POSTGRES_PASSWORD = 'isolated-phase01-test-only'
$env:POSTGRES_DB = 'acos_test'
$env:DATABASE_URL = 'postgresql+psycopg://acos_test:isolated-phase01-test-only@postgres:5432/acos_test'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:18081'
$env:WEB_PORT = '18081'
$env:WEB_BIND_ADDRESS = '127.0.0.1'
& docker compose --project-directory $repoRoot --env-file "$repoRoot/.env.example" -p acos-phase01-fixes -f "$repoRoot/docker-compose.yml" -f "$repoRoot/deploy/compose.phase01-test.yml" @ComposeArgs
exit $LASTEXITCODE
