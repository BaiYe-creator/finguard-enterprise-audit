$ErrorActionPreference = "Stop"

$psql = "D:\postgresql\18\bin\psql.exe"
$secretsPath = "D:\FinGuard_Project\current\.streamlit\secrets.toml"
if (-not (Test-Path -LiteralPath $psql)) {
    throw "psql.exe not found: $psql"
}

Write-Host "FinGuard PostgreSQL initialization" -ForegroundColor Cyan
Write-Host "Enter the password for the local PostgreSQL 'postgres' administrator."
$secureAdmin = Read-Host "postgres password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureAdmin)
try {
    $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
if ([string]::IsNullOrWhiteSpace($adminPassword)) {
    throw "postgres password cannot be empty"
}

$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $bytes = New-Object byte[] 24
    $rng.GetBytes($bytes)
    $appPassword = [Convert]::ToBase64String($bytes).Replace("+", "A").Replace("/", "B").Replace("=", "")
}
finally {
    $rng.Dispose()
}

try {
    $env:PGPASSWORD = $adminPassword
    $roleSql = @'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'finguard_app') THEN
        CREATE ROLE finguard_app LOGIN PASSWORD '__APP_PASSWORD__';
    ELSE
        ALTER ROLE finguard_app WITH LOGIN PASSWORD '__APP_PASSWORD__';
    END IF;
END
$$;
'@.Replace("__APP_PASSWORD__", $appPassword)
    & $psql -h localhost -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c $roleSql
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the finguard_app role"
    }

    $databaseOutput = & $psql -h localhost -p 5432 -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='finguard'"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to check the finguard database"
    }
    $databaseExists = if ($null -eq $databaseOutput) {
        ""
    }
    else {
        ([string]$databaseOutput).Trim()
    }
    if ($databaseExists -ne "1") {
        & $psql -h localhost -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE finguard OWNER finguard_app"
    }
    else {
        & $psql -h localhost -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE finguard OWNER TO finguard_app"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create or update the finguard database"
    }

    $env:PGPASSWORD = $appPassword
    $connectionOutput = & $psql -h localhost -p 5432 -U finguard_app -d finguard -tAc "SELECT current_user"
    if ($LASTEXITCODE -ne 0) {
        throw "finguard_app connection test failed"
    }
    $connectionTest = if ($null -eq $connectionOutput) {
        ""
    }
    else {
        ([string]$connectionOutput).Trim()
    }
    if ($connectionTest -ne "finguard_app") {
        throw "finguard_app connection test failed"
    }

    $connectionUrl = "postgresql://finguard_app:$appPassword@localhost:5432/finguard"
    $lines = @()
    if (Test-Path -LiteralPath $secretsPath) {
        $lines = Get-Content -LiteralPath $secretsPath -Encoding utf8 | Where-Object {
            $_ -notmatch "^\s*FINGUARD_DATABASE_URL\s*="
        }
    }
    $lines += "FINGUARD_DATABASE_URL = `"$connectionUrl`""
    [IO.File]::WriteAllLines(
        $secretsPath,
        $lines,
        (New-Object Text.UTF8Encoding($false))
    )

    Write-Host ""
    Write-Host "PostgreSQL initialization succeeded." -ForegroundColor Green
    Write-Host "Database: finguard"
    Write-Host "User: finguard_app"
    Write-Host "Connection URL written to the local Streamlit Secrets file."
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    $adminPassword = $null
    $appPassword = $null
}
