#!/bin/bash
set -e

echo "=== Starting Aria Voice Agent ==="

# Start the LiveKit agent worker in the background
echo "[1/2] Starting LiveKit agent worker..."
python agent.py start &
AGENT_PID=$!
echo "Agent worker started (PID: $AGENT_PID)"

# Start the web dashboard in the foreground (Railway health check listens here)
echo "[2/2] Starting web dashboard on port $PORT..."
uvicorn ui_server:app --host 0.0.0.0 --port ${PORT:-8000}
