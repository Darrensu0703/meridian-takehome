$ErrorActionPreference = "Stop"

# Meridian take-home — start Streamlit from the project folder (uses local .venv + .env).
Set-Location -Path $PSScriptRoot

& ".\.venv\Scripts\streamlit.exe" run streamlit_app.py
