@echo off
setlocal
cd /d "%~dp0"
set "H3_WEB_PYTHON=python"
if exist "H3Studio\.venv\Scripts\python.exe" set "H3_WEB_PYTHON=%~dp0H3Studio\.venv\Scripts\python.exe"
if exist "ComfyUI\.venv\Scripts\python.exe" set "H3_WEB_PYTHON=%~dp0ComfyUI\.venv\Scripts\python.exe"
echo Start the host Studio and its Shared Gateway before connecting users.
echo Company web API port: 8795
"%H3_WEB_PYTHON%" "%~dp0H3Studio\web_server.py" %*
if errorlevel 1 pause
