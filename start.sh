#!/bin/bash
set -e

echo "=== Starting Aria Voice Agent ==="
echo "LIVEKIT_URL: ${LIVEKIT_URL:0:30}..."

# Start the LiveKit agent worker with explicit credentials passed as CLI args
echo "[1/2] Starting LiveKit agent worker..."
python agent.py start \
    --url "$LIVEKIT_URL" \
    --api-key "$LIVEKIT_API_KEY" \
    --api-secret "$LIVEKIT_API_SECRET" &
AGENT_PID=$!
echo "Agent worker started (PID: $AGENT_PID)"

# Start the web dashboard in the foreground
echo "[2/2] Starting web dashboard on port $PORT..."
uvicorn ui_server:app --host 0.0.0.0 --port "${PORT:-8000}"
