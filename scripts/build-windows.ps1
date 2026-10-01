$ErrorActionPreference = "Stop"

$webRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$cliRoot = Join-Path $webRoot "cli"
$backendRoot = Join-Path $webRoot "build\windows"
$backendPath = Join-Path $backendRoot "rh-workflow-desk-server.exe"

New-Item -ItemType Directory -Force -Path $backendRoot | Out-Null
if (Test-Path $backendPath) {
  Remove-Item -Force $backendPath
}

Write-Host "[1/3] 构建内置 Python 本地服务…"
$env:PYTHONPATH = "$(Join-Path $cliRoot 'src');$webRoot"
uv run --project $webRoot --with "pyinstaller>=6.0" pyinstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name rh-workflow-desk-server `
  --distpath $backendRoot `
  --workpath (Join-Path $backendRoot "work") `
  --specpath (Join-Path $backendRoot "spec") `
  --paths (Join-Path $cliRoot "src") `
  --paths $webRoot `
  --add-data "$(Join-Path $webRoot 'static');static" `
  (Join-Path $webRoot "backend\backend_entry.py")

if (-not (Test-Path $backendPath)) {
  throw "PyInstaller 未生成内置服务：$backendPath"
}

Write-Host "[2/3] 构建 Electron Windows 安装包…"
Push-Location $webRoot
try {
  npx electron-builder --win nsis --x64 --publish never
}
finally {
  Pop-Location
}

Write-Host "[3/3] 生成 SHA-256 校验文件…"
$artifacts = @(Get-ChildItem (Join-Path $webRoot "dist") -File | Where-Object { $_.Extension -eq ".exe" })
if ($artifacts.Length -gt 0) {
  $checksums = $artifacts | ForEach-Object {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    "$hash  $($_.Name)"
  }
  $checksums | Set-Content -Encoding ascii (Join-Path $webRoot "dist\SHA256SUMS.txt")
}

Write-Host "构建完成：$(Join-Path $webRoot 'dist')"
