#!/bin/sh
# Pripraví priečinok s dátami (aj keď je pripojený z hostiteľa) a spustí aplikáciu
# pod neprivilegovaným používateľom "app".
set -e

mkdir -p "$DATA_DIR/dokumenty"
if [ "$(id -u)" = "0" ] && command -v setpriv >/dev/null 2>&1; then
    chown -R app:app "$DATA_DIR" 2>/dev/null || true
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
