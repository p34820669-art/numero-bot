@echo off
cd /d "%~dp0"
echo Open http://localhost:8765 in your browser. Stop with Ctrl+C.
python -m uvicorn web.server:app --host 127.0.0.1 --port 8765
