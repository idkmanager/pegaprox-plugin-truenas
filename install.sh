#!/usr/bin/env bash
# Install the TrueNAS plugin into a local PegaProx instance.
# Run as root on the PegaProx host (e.g. LXC 119).
#
# F0 scope: this script installs the code skeleton only. It does NOT seed
# any real instance/API key — config.json starts with an empty instances
# list; the operator adds instances (and confirms host/port/TLS/API key)
# from the Settings tab after deploy, per PEGAPROX_PLUGIN_TRUENAS_BRIEF.md
# §0.5 ("NO push / NO deploy / NO writes ... sin confirmación explícita").
set -euo pipefail

PLUGIN_ID="truenas"
PEGAPROX_DIR="${PEGAPROX_DIR:-/opt/PegaProx}"
PLUGINS_DIR="$PEGAPROX_DIR/plugins"
DEST="$PLUGINS_DIR/$PLUGIN_ID"
DB="$PEGAPROX_DIR/config/pegaprox.db"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ITEMS="__init__.py manifest.json src"

echo "==> Installing $PLUGIN_ID into $DEST"
[ -d "$PEGAPROX_DIR" ] || { echo "PegaProx not found at $PEGAPROX_DIR"; exit 1; }

mkdir -p "$DEST"
for f in $RUNTIME_ITEMS; do
  # rm before cp -r: on a REdeploy (not a fresh install) "$DEST/$f" already
  # exists, and `cp -rf sourcedir destdir` with an existing destdir copies
  # INTO it (nests at $DEST/$f/$f) rather than replacing its contents.
  # Found live 2026-07-21: manifest.json (a plain file, copied fine either
  # way) showed the new version while src/ui/plugin.html kept serving the
  # OLD content from underneath the newly-nested src/src/ui/plugin.html —
  # a half-applied deploy with no error and a misleading version number.
  rm -rf "$DEST/$f"
  cp -rf "$SRC/$f" "$DEST/$f"
done

# Seed config.json on first install only (never clobber operator config).
if [ ! -f "$DEST/config.json" ]; then
  cp -f "$SRC/config.example.json" "$DEST/config.json"
  # Strip the example instance/key placeholders — the operator adds real
  # instances from the plugin's Settings tab (config/save) after deploy.
  python3 - "$DEST/config.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
cfg['instances'] = []
with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
PY
fi
chmod 600 "$DEST/config.json"

# Try to enable the plugin in plugin_state — but only if the DB is a *plain*
# SQLite file. Newer PegaProx encrypts the DB via dbcrypto/SQLCipher, where an
# external sqlite3 fails with "file is not a database (26)". Fallback: the
# API enable endpoint (never abort the install either way).
ENABLED_VIA_DB=0
if command -v sqlite3 >/dev/null 2>&1 && [ -f "$DB" ] \
   && sqlite3 "$DB" "PRAGMA schema_version;" >/dev/null 2>&1; then
  if sqlite3 "$DB" "INSERT OR REPLACE INTO plugin_state (plugin_id, enabled) VALUES ('$PLUGIN_ID', 1);" 2>/dev/null; then
    ENABLED_VIA_DB=1
    echo "==> Enabled in plugin_state (plain SQLite)"
  fi
fi
if [ "$ENABLED_VIA_DB" -eq 0 ]; then
  echo "!! Could not auto-enable via the DB (encrypted or locked — normal on SQLCipher builds)."
  echo "   Trying the API fallback: POST /api/plugins/$PLUGIN_ID/enable"
  curl -fsS -X POST "http://127.0.0.1:8006/api/plugins/$PLUGIN_ID/enable" >/dev/null 2>&1 \
    && echo "==> Enabled via API" \
    || echo "   Could not enable via API either — enable manually: PegaProx > Settings > Plugins > 'TrueNAS' > Enable"
fi

# Ownership must match the user the pegaprox *service* runs as (it writes
# config.json at runtime), NOT necessarily the owner of $PEGAPROX_DIR. Prefer
# the systemd User=, then the owner of an existing plugin, then the plugins dir.
SVC_USER="$(systemctl show -p User --value pegaprox 2>/dev/null)"
if [ -z "$SVC_USER" ] || [ "$SVC_USER" = "root" ]; then
  if [ -d "$PLUGINS_DIR/wake-on-lan" ]; then
    SVC_USER="$(stat -c '%U' "$PLUGINS_DIR/wake-on-lan")"
  elif [ -d "$PLUGINS_DIR/docker_swarm" ]; then
    SVC_USER="$(stat -c '%U' "$PLUGINS_DIR/docker_swarm")"
  else
    SVC_USER="$(stat -c '%U' "$PLUGINS_DIR" 2>/dev/null || echo pegaprox)"
  fi
fi
SVC_GROUP="$(id -gn "$SVC_USER" 2>/dev/null || echo "$SVC_USER")"
chown -R "$SVC_USER:$SVC_GROUP" "$DEST" 2>/dev/null || true
chmod 775 "$DEST" 2>/dev/null || true
chmod 600 "$DEST/config.json" 2>/dev/null || true
echo "==> Ownership set to $SVC_USER:$SVC_GROUP"

# --- Persistence guard (systemd timer) ---------------------------------------
# Cache lives OUTSIDE $PEGAPROX_DIR so it survives a PegaProx reinstall/upgrade.
CACHE_DIR="${CACHE_DIR:-/usr/local/lib/truenas}"
if command -v systemctl >/dev/null 2>&1; then
  echo "==> Installing persistence guard -> $CACHE_DIR"
  mkdir -p "$CACHE_DIR"
  for f in $RUNTIME_ITEMS; do rm -rf "$CACHE_DIR/$f"; cp -rf "$SRC/$f" "$CACHE_DIR/$f"; done
  cp -f "$SRC/truenas-maintenance.sh" "$CACHE_DIR/truenas-maintenance.sh"
  chmod +x "$CACHE_DIR/truenas-maintenance.sh"

  cat > /etc/truenas-plugin.conf <<CONF
# TrueNAS plugin — host maintenance config
PEGAPROX_DIR=$PEGAPROX_DIR
CACHE_DIR=$CACHE_DIR
SVC_USER=$SVC_USER
CONF

  cat > /etc/systemd/system/truenas-maintenance.service <<'UNIT'
[Unit]
Description=TrueNAS plugin - persistence guard
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/lib/truenas/truenas-maintenance.sh
UNIT

  cat > /etc/systemd/system/truenas-maintenance.timer <<'UNIT'
[Unit]
Description=Run TrueNAS plugin maintenance periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

  systemctl daemon-reload
  systemctl enable --now truenas-maintenance.timer >/dev/null 2>&1 \
    && echo "==> Guard timer active" \
    || echo "!! could not enable truenas-maintenance.timer"
else
  echo "!! systemctl not found — skipping persistence guard"
fi

echo "==> Restarting pegaprox"
systemctl restart pegaprox || echo "!! restart manually: systemctl restart pegaprox"
echo "==> Done."
if [ "$ENABLED_VIA_DB" -eq 1 ]; then
  echo "    Open the 'TrueNAS' tab in PegaProx, then add an instance from Settings"
  echo "    (host/port/TLS/api_key_ro) and click 'Probar conexión' before saving."
else
  echo "    Now enable it: PegaProx > Settings > Plugins > 'TrueNAS' > Enable."
fi
