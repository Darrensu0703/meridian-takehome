# Run once (right-click -> Run with PowerShell) to copy the launcher to your Desktop.
$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$src = Join-Path $repo "Meridian Streamlit.lnk"
if (-not (Test-Path -LiteralPath $src)) {
    throw "Missing: $src"
}
$desk = (New-Object -ComObject Shell.Application).Namespace("shell:Desktop").Self.Path
$dest = Join-Path $desk "Meridian Streamlit.lnk"
Copy-Item -LiteralPath $src -Destination $dest -Force
Write-Host "Desktop shortcut installed: $dest"
