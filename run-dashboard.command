#!/bin/bash
# Double-click this to launch the sales dashboard and open it in your browser.
cd "$(dirname "$0")"
../.venv_orders/bin/python app.py &
SERVER=$!
sleep 1.5
open "http://localhost:8765"
echo ""
echo "Dashboard is running. Close this window (or press Ctrl+C) to stop it."
wait $SERVER
