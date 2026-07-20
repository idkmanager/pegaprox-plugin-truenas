#!/usr/bin/env bash
# Remove the TrueNAS plugin from a local PegaProx instance.
set -euo pipefail
PLUGIN_ID="truenas"
PEGAPROX_DIR="${PEGAPROX_DIR:-/opt/PegaProx}"
DEST="$PEGAPROX_DIR/plugins/$PLUGIN_ID"
DB="$PEGAPROX_DIR/config/pegaprox.db"

read -rp "Remove $PLUGIN_ID and its config (incl. saved TrueNAS API keys)? [y/N] " ans
[ "${ans:-N}" = "y" ] || { echo "Aborted."; exit 0; }

# Remove the persistence guard first so it can't restore the plugin.
if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now truenas-maintenance.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/truenas-maintenance.timer \
        /etc/systemd/system/truenas-maintenance.service
  systemctl daemon-reload || true
fi
rm -rf /usr/local/lib/truenas /etc/truenas-plugin.conf

rm -rf "$DEST"
if command -v sqlite3 >/dev/null 2>&1 && [ -f "$DB" ]; then
  sqlite3 "$DB" "DELETE FROM plugin_state WHERE plugin_id = '$PLUGIN_ID';" || true
fi
systemctl restart pegaprox || echo "!! restart manually: systemctl restart pegaprox"
echo "==> Removed $PLUGIN_ID (plugin, cache, guard timer, config with API keys)"
