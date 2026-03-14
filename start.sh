#!/bin/bash
set -e

echo "Starting Sports Picks..."

# Build frontend
cd frontend && npm run build && cd ..

# Start pipeline in background
python -m backend.pipeline.scheduler &
PIPELINE_PID=$!

# Start web server
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 &
WEB_PID=$!

echo "Pipeline PID: $PIPELINE_PID"
echo "Web server PID: $WEB_PID"
echo "Dashboard: http://localhost:8000"

trap "kill $PIPELINE_PID $WEB_PID 2>/dev/null; exit 0" SIGINT SIGTERM
wait
