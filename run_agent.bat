@echo off
echo Starting NexusDesk Agent API Backend...
start cmd /k "python server.py"

echo Starting Premium Dashboard Frontend...
cd frontend
start cmd /k "npm install && npm run dev"

echo NexusDesk system is booting up!
echo The API will run on http://localhost:8000
echo The Dashboard will automatically launch in your browser via Vite (usually http://localhost:5173).
