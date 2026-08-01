param(
    [ValidateSet("A", "B", "C", "All")]
    [string]$Profile = "All"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:IDF_PYTHON_ENV_PATH) -or
    [string]::IsNullOrWhiteSpace($env:IDF_PATH)) {
    throw "ESP-IDF environment is not active. Run export.ps1 first."
}

$python = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
$idfPy = Join-Path $env:IDF_PATH "tools\idf.py"
if (-not (Test-Path -LiteralPath $python) -or
    -not (Test-Path -LiteralPath $idfPy)) {
    throw "ESP-IDF environment is not active. Run export.ps1 first."
}
$ninjaCommand = Get-Command ninja -ErrorAction SilentlyContinue
if ($null -eq $ninjaCommand) {
    throw "Ninja is not on PATH. Run the ESP-IDF export.ps1 first."
}
$ninja = $ninjaCommand.Source

$profilesToBuild = if ($Profile -eq "All") {
    @("A", "B", "C")
} else {
    @($Profile)
}

Push-Location -LiteralPath $PSScriptRoot
try {
    foreach ($profileName in $profilesToBuild) {
        $profileLower = $profileName.ToLowerInvariant()
        $buildDirectory = "build-gateway-$profileLower"
        $sdkconfigFile = "sdkconfig.gateway_${profileLower}_uart"
        $sdkconfigPath = Join-Path $PSScriptRoot $sdkconfigFile
        $defaults = "sdkconfig.gateway_uart.defaults;sdkconfig.gateway_${profileLower}.defaults"

        Write-Host "Building SmartBlock gateway $profileName..."
        # Profile defaults are authoritative. Regenerate the ignored sdkconfig
        # so changed radio settings cannot be masked by a stale generated file.
        if (Test-Path -LiteralPath $sdkconfigPath) {
            Remove-Item -LiteralPath $sdkconfigPath -Force
        }
        & $python $idfPy `
            -B $buildDirectory `
            -D "SDKCONFIG=$sdkconfigFile" `
            -D "SDKCONFIG_DEFAULTS=$defaults" `
            reconfigure

        if ($LASTEXITCODE -ne 0) {
            throw "Gateway $profileName configure failed with exit code $LASTEXITCODE."
        }

        # A single compiler job avoids a sporadic GCC internal error observed
        # in the Windows ESP-IDF 5.2.3 toolchain during parallel compilation.
        & $ninja -C $buildDirectory -j 1
        if ($LASTEXITCODE -ne 0) {
            throw "Gateway $profileName build failed with exit code $LASTEXITCODE."
        }
    }
} finally {
    Pop-Location
}
