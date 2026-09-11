#!/bin/zsh
# kill_orphaned_dev_servers.sh
# Kills Django development processes and the Uvicorn development reloader.

# Find matching processes (exclude the grep process itself via [] trick)
PIDS=$(ps aux | grep -E '[m]ain\.py (runserver|refresh|db_worker)|[m]anage\.py (runserver|refresh|db_worker)|[u]vicorn config\.asgi_dev:application|[g]unicorn config\.asgi' | awk '{print $2}')

if [[ -n "$PIDS" ]]; then
  echo "$PIDS"
  # Prefer gentle kill first
  echo "$PIDS" | xargs -n1 kill 2>/dev/null
  sleep 0.5
  # Force kill any that remain
  REMAIN=$(echo "$PIDS" | xargs -n1 | while read -r p; do ps -p "$p" >/dev/null 2>&1 && echo "$p"; done)
  if [[ -n "$REMAIN" ]]; then
    echo "Force killing: $REMAIN"
    echo "$REMAIN" | xargs -n1 kill -9 2>/dev/null
  fi
else
  echo "No Django processes found."
fi

echo "Cleanup complete."
