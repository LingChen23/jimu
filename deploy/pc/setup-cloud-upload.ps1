param(
    [string]$CloudUrl = "http://39.102.212.53",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRoot = Join-Path $repoRoot "vue_backend\runtime"
$configPath = Join-Path $runtimeRoot "cloud_upload.json"

if ([string]::IsNullOrWhiteSpace($Token)) {
    $Token = Read-Host "请输入 ECS 云端上传 Token"
}
if ($Token.Length -lt 32) { throw "Token 至少需要 32 个字符" }
if (-not ($CloudUrl -match '^https?://[^/]+/?$')) { throw "云端地址必须是 http:// 或 https:// 地址" }

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$config = [ordered]@{
    enabled = $true
    base_url = $CloudUrl.TrimEnd('/')
    token = $Token
}
[System.IO.File]::WriteAllText(
    $configPath,
    ($config | ConvertTo-Json) + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)
& icacls.exe $configPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "无法限制上传配置文件权限" }

Write-Host "云端公网上传已配置：$CloudUrl"
Write-Host "PC 后端将直接通过公网地址上传，不再启动 SSH 隧道。"
