$ErrorActionPreference = "Stop"

# Meridian take-home — start Streamlit from the project folder (uses local .venv + .env).
Set-Location -Path $PSScriptRoot

# Open the app in your default browser automatically (no need to paste a URL).
# Paired with `.streamlit/config.toml` server.headless=true so you get one tab, not two.
if (-not $env:MERIDIAN_SKIP_BROWSER_LAUNCH) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "timeout /t 5 /nobreak >nul && start http://localhost:8501" -WindowStyle Hidden
}

& ".\.venv\Scripts\streamlit.exe" run streamlit_app.py
