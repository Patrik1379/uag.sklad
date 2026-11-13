#!/bin/sh
# Aktualizácia aplikácie na serveri z GitHubu.
# Spustenie (na serveri):  sh /opt/sklad-dielov/aktualizuj.sh
set -e
cd "$(dirname "$0")"
echo "==> Sťahujem novú verziu z GitHubu…"
git pull --ff-only
echo "==> Zostavujem a reštartujem aplikáciu…"
docker compose --profile server up -d --build
docker image prune -f >/dev/null
docker compose ps
echo "==> Hotovo."
