#!/usr/bin/env bash
# Start de SlimmeDrone op de Raspberry Pi. Gebruik:  bash start.sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Nog niet geïnstalleerd. Draai eerst:  bash install-pi.sh"
  exit 1
fi
exec .venv/bin/python app.py
