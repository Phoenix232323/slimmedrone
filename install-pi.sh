#!/usr/bin/env bash
# Installeert de SlimmeDrone op een Raspberry Pi. Gebruik:  bash install-pi.sh
# Je kunt dit gerust nog een keer draaien; wat al klaar is, wordt overgeslagen.
set -e
cd "$(dirname "$0")"

echo "== 1/5 Systeempakketten installeren (misschien vraagt hij je wachtwoord) =="
sudo apt update
sudo apt install -y python3-picamera2 python3-venv

echo "== 2/5 Python-omgeving maken =="
if [ ! -d .venv ]; then
  # --system-site-packages: zodat picamera2 en numpy van het systeem gebruikt worden
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install -r requirements-pi.txt

echo "== 3/5 Instellingen maken (.env) =="
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i 's/^CAMERA_SOURCE=.*/CAMERA_SOURCE=picamera/' .env
  sed -i 's/^PROCESS_WIDTH=.*/PROCESS_WIDTH=640/' .env
  sed -i 's/^OBJECT_EVERY=.*/OBJECT_EVERY=3/' .env
  echo "Gemaakt: .env (camera = Raspberry Pi-camera)"
else
  echo ".env bestaat al, overgeslagen"
fi

echo "== 4/5 AI-modellen downloaden =="
.venv/bin/python beheer.py modellen

echo "== 5/5 Account voor de website =="
if [ ! -f data/gebruikers.json ]; then
  until read -rp "Kies een gebruikersnaam: " NAAM && .venv/bin/python beheer.py gebruiker-toevoegen "$NAAM"; do
    echo "Dat lukte niet, probeer het nog eens."
  done
else
  echo "Er is al een account. Extra account: .venv/bin/python beheer.py gebruiker-toevoegen <naam>"
fi

echo
echo "Klaar! Test de camera met:  rpicam-hello --list-cameras"
echo "Start de SlimmeDrone met:   bash start.sh"
