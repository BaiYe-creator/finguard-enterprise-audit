$ErrorActionPreference = "Stop"

$psql = "D:\postgresql\18\bin\psql.exe"
$secretsPath = "D:\FinGuard_Project\current\.streamlit\secrets.toml"
$clipboard = Get-Clipboard -Raw
if ([string]::IsNullOrWhiteSpace($clipboard)) {
    throw "Clipboard is empty. Copy the Supabase connection string again."
}
$match = [regex]::Match($clipboard, 'postgres(?:ql)?://[^\s"''<>]+')
if (-not $match.Success) {
    throw "No PostgreSQL connection string was found in the clipboard."
}
$template = $match.Value
if ($template -notmatch '\[YOUR-PASSWORD\]') {
    throw "The clipboard connection string does not contain [YOUR-PASSWORD]."
}

Write-Host "FinGuard Supabase configuration" -ForegroundColor Cyan
Write-Host "Enter the Supabase database password."
$securePassword = Read-Host "Supabase database password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $databasePassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
if ([string]::IsNullOrWhiteSpace($databasePassword)) {
    throw "Database password cannot be empty."
}

$encodedPassword = [Uri]::EscapeDataString($databasePassword)
$connectionUrl = $template.Replace("[YOUR-PASSWORD]", $encodedPassword)
$uri = [Uri]$connectionUrl
$parts = $uri.UserInfo.Split(":", 2)
$username = [Uri]::UnescapeDataString($parts[0])
$password = [Uri]::UnescapeDataString($parts[1])

try {
    $env:PGPASSWORD = $password
    $test = & $psql `
        -h $uri.Host `
        -p $uri.Port `
        -U $username `
        -d $uri.AbsolutePath.Trim("/") `
        -tAc "SELECT current_database(), current_user"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($test)) {
        throw "Supabase connection test failed."
    }

    $lines = Get-Content -LiteralPath $secretsPath -Encoding utf8 |
        Where-Object {
            $_ -notmatch "^\s*FINGUARD_DATABASE_URL\s*=" -and
            $_ -notmatch "^\s*FINGUARD_USE_POSTGRES\s*="
        }
    $lines += "FINGUARD_DATABASE_URL = `"$connectionUrl`""
    $lines += "FINGUARD_USE_POSTGRES = true"
    [IO.File]::WriteAllLines(
        $secretsPath,
        $lines,
        (New-Object Text.UTF8Encoding($false))
    )
    Write-Host ""
    Write-Host "Supabase connection succeeded and Secrets were updated." -ForegroundColor Green
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    $databasePassword = $null
    $password = $null
}
