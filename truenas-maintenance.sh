#!/usr/bin/env bash
# TrueNAS plugin — host-side persistence guard (run by a systemd timer).
#
# If a PegaProx upgrade wipes or downgrades the plugin in
# /opt/PegaProx/plugins/truenas, restore it from the cache that lives
# OUTSIDE /opt/PegaProx (so it survives PegaProx reinstalls), re-enable it
# and restart pegaprox. Runtime data (config.json with API keys) is never
# touched by this script — only the code files (__init__.py, manifest.json,
# src/).
#
# No auto-update: this plugin has no public update source configured, so it
# stays whatever version install.sh laid down until the operator re-runs
# install.sh manually.
set -uo pipefail

CONF=/etc/truenas-plugin.conf
[ -f "$CONF" ] && . "$CONF"

PLUGIN_ID=truenas
PEGAPROX_DIR="${PEGAPROX_DIR:-/opt/PegaProx}"
DEST="$PEGAPROX_DIR/plugins/$PLUGIN_ID"
DB="$PEGAPROX_DIR/config/pegaprox.db"
CACHE="${CACHE_DIR:-/usr/local/lib/truenas}"
SVC_USER="${SVC_USER:-pegaprox}"
RUNTIME_ITEMS="__init__.py manifest.json src"

log(){ logger -t truenas-maint "$*" 2>/dev/null || true; echo "[truenas-maint] $*"; }
ver(){ python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('version','0'))" "$1" 2>/dev/null || echo 0; }
vgt(){ python3 - "$1" "$2" <<'PY'
import sys
def t(v):
    o=[]
    for p in str(v).split('.'):
        d=''.join(c for c in p if c.isdigit()); o.append(int(d) if d else 0)
    return o
a,b=t(sys.argv[1]),t(sys.argv[2]); n=max(len(a),len(b)); a+=[0]*(n-len(a)); b+=[0]*(n-len(b))
sys.exit(0 if a>b else 1)
PY
}

changed=0
need_restore=0
if [ ! -f "$DEST/__init__.py" ]; then
  need_restore=1
elif [ -f "$CACHE/manifest.json" ] && vgt "$(ver "$CACHE/manifest.json")" "$(ver "$DEST/manifest.json")"; then
  need_restore=1
fi

if [ "$need_restore" = 1 ] && [ -f "$CACHE/__init__.py" ]; then
  mkdir -p "$DEST"
  for f in $RUNTIME_ITEMS; do rm -rf "$DEST/$f"; cp -rf "$CACHE/$f" "$DEST/$f"; done
  [ -f "$DEST/config.json" ] || echo '{ "instances": [], "poll": {"fast_s":10,"slow_s":60,"cold_s":900} }' > "$DEST/config.json"
  GRP="$(id -gn "$SVC_USER" 2>/dev/null || echo "$SVC_USER")"
  chown -R "$SVC_USER:$GRP" "$DEST" 2>/dev/null || true
  chmod 600 "$DEST/config.json" 2>/dev/null || true
  # Best-effort re-enable (plain SQLite only; encrypted DBs keep their row).
  if command -v sqlite3 >/dev/null 2>&1 && sqlite3 "$DB" "PRAGMA schema_version;" >/dev/null 2>&1; then
    sqlite3 "$DB" "INSERT OR REPLACE INTO plugin_state (plugin_id, enabled) VALUES ('$PLUGIN_ID',1);" 2>/dev/null || true
  fi
  changed=1
  log "restored plugin into $DEST (v$(ver "$DEST/manifest.json"))"
fi

if [ "$changed" = 1 ]; then
  if systemctl restart pegaprox 2>/dev/null; then log "pegaprox restarted"; else log "WARN: could not restart pegaprox"; fi
fi
exit 0
