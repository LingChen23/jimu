param(
    [string]$OutputRoot = "",
    [string]$CloudUrl = "http://39.102.212.53",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path (Split-Path $repoRoot -Parent) "JimuMotion-PC"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$sourceConfig = Join-Path $repoRoot "vue_backend\runtime\cloud_upload.json"
$frontendRoot = Join-Path $repoRoot "vue_frontend"
$frontendDist = Join-Path $frontendRoot "dist"
$defaultsRoot = Join-Path $repoRoot "vue_backend\defaults"
$entryPoint = Join-Path $repoRoot "vue_backend\desktop_entry.py"
$buildRoot = Join-Path $env:TEMP ("jimu-exe-build-" + [guid]::NewGuid().ToString("N"))

if (Test-Path -LiteralPath $OutputRoot) {
    throw "Output directory already exists; refusing to overwrite it: $OutputRoot"
}

if (-not $Token -and (Test-Path -LiteralPath $sourceConfig)) {
    $source = Get-Content -LiteralPath $sourceConfig -Raw | ConvertFrom-Json
    $Token = [string]$source.token
}
if ($Token.Length -lt 32) {
    throw "No valid token found. Configure vue_backend\runtime\cloud_upload.json or pass -Token."
}
if ($CloudUrl -notmatch '^https?://[^/]+/?$') {
    throw "CloudUrl must use the http(s)://host format."
}

try {
    Push-Location $frontendRoot
    try {
        npm run build
    }
    finally {
        Pop-Location
    }

    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    $distRoot = Join-Path $buildRoot "dist"
    $workRoot = Join-Path $buildRoot "work"
    $specRoot = Join-Path $buildRoot "spec"

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name JimuMotion `
        --paths (Join-Path $repoRoot "vue_backend") `
        --add-data "${frontendDist};static" `
        --add-data "${defaultsRoot};defaults" `
        --distpath $distRoot `
        --workpath $workRoot `
        --specpath $specRoot `
        $entryPoint

    $builtApp = Join-Path $distRoot "JimuMotion"
    if (-not (Test-Path -LiteralPath (Join-Path $builtApp "JimuMotion.exe"))) {
        throw "PyInstaller did not create JimuMotion.exe."
    }

    Move-Item -LiteralPath $builtApp -Destination $OutputRoot
    $runtimeRoot = Join-Path $OutputRoot "runtime"
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    $cloudConfig = @{
        enabled = $true
        base_url = $CloudUrl.TrimEnd('/')
        token = $Token
    } | ConvertTo-Json
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        (Join-Path $runtimeRoot "cloud_upload.json"),
        $cloudConfig,
        $utf8WithoutBom
    )

    @"
Jimu Motion PC

Double-click JimuMotion.exe to start. The application opens the browser automatically.
The application keeps running in the Windows system tray after the browser is closed.
Use the tray menu to open the interface or exit the application and release the serial port.
Application data is stored in runtime. Do not move or delete that directory while the app is running.
Warnings and errors are kept in runtime/logs, merged by cause, and uploaded automatically when cloud access is available.
Cloud upload uses the configured public URL and token. SSH is not required.
"@ | Set-Content -LiteralPath (Join-Path $OutputRoot "README.txt") -Encoding UTF8

    Write-Host "Package created: $OutputRoot"
}
finally {
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
}
