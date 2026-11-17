@echo off
cd /d "%~dp0"
echo Clipmaster  http://127.0.0.1:8787
echo Leave this window open. Ollama has to be running, with qwen2.5 pulled.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8787
pause
