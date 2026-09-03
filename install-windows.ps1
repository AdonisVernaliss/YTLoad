$ErrorActionPreference = "Stop"
Write-Host "YTLoad - Windows setup`n"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Error "WinGet is required by this installer. Install/update 'App Installer' from Microsoft, then rerun."
}

function Install-Or-Upgrade($Id) {
    $installed = winget list --id $Id -e --accept-source-agreements 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Updating $Id..."
        winget upgrade --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    } else {
        Write-Host "Installing $Id..."
        winget install --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
    Install-Or-Upgrade "Python.Python.3.13"
}
Install-Or-Upgrade "yt-dlp.yt-dlp"
Install-Or-Upgrade "Gyan.FFmpeg"
Install-Or-Upgrade "DenoLand.Deno"

$Python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }
Write-Host "`nDiagnostics:"
& $Python "$PSScriptRoot\ytload.py" doctor
Write-Host "`nReady. Run: $Python `"$PSScriptRoot\ytload.py`""
