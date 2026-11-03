# Runs only the isolated Phase 03 acceptance Compose project; never loads .env.
#
# The project deliberately uses its own name, port, images and volume so it can
# coexist with the development stack without sharing any state.
param([string[]]$ComposeArgs = @('ps'))
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:POSTGRES_USER = 'acos_p3'
$env:POSTGRES_PASSWORD = 'isolated-phase03-test-only'
$env:POSTGRES_DB = 'acos_p3'
$env:DATABASE_URL = 'postgresql+psycopg://acos_p3:isolated-phase03-test-only@postgres:5432/acos_p3'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:18085'
$env:WEB_PORT = '18085'
$env:WEB_BIND_ADDRESS = '127.0.0.1'
$env:DEEPSEEK_API_KEY = ''
# ADR-0006 data key so the UI can save a provider key in this throwaway stack.
$env:SECRET_ENCRYPTION_KEY = 'cGhhc2UwMy10ZXN0LW9ubHktMzItYnl0ZS1rZXkhISE='
$env:WORKER_LEASE_SECONDS = '12'
$env:DEMO_SLOW_STEP_SECONDS = '20'
& docker compose --project-directory $repoRoot --env-file "$repoRoot/.env.example" -p acos-phase03-test -f "$repoRoot/docker-compose.yml" -f "$repoRoot/deploy/compose.phase03-test.yml" @ComposeArgs
exit $LASTEXITCODE
