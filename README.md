# TrueNAS — PegaProx Plugin

Monitors and controls one or more TrueNAS SCALE instances from the PegaProx
panel (TrueCommand-style), over the JSON-RPC 2.0 WebSocket API — REST v2.0
is deprecated in 25.10 and removed in TrueNAS 26, so this plugin never uses
it. See `PEGAPROX_PLUGIN_TRUENAS_BRIEF.md` (in the workspace, not this repo)
for the full architecture, phase plan and known gotchas.

**This is F0 (v0.1.0): an installable skeleton.** It ships the transport
layer, config, and UI shell only — no subsystem (pools, datasets, snapshots,
shares, replication, apps/VMs) is implemented yet. Every tab except
**Settings** is an empty placeholder. F1 adds read-only monitoring.

## What's in F0

- `src/core/ws_client.py` — a generic, reusable JSON-RPC 2.0 client over a
  persistent WebSocket: request/response framing, concurrent `id` handling,
  typed errors, lazy-connect, and automatic reconnection (exponential
  backoff + jitter) that re-logs-in and re-subscribes after a drop.
- `src/core/conn_manager.py` — one client per configured instance,
  lazy-connect, multi-instance from day one.
- `config.example.json` / `config.json` (chmod 600, never committed) —
  multi-tenant instance list (`client_id` + host/port/TLS/API keys/readonly)
  and the polling budget.
- `src/routes/api.py` + `src/routes/config_store.py` — the `ui`, `config`,
  `config/save` and `instances/test` routes, with masked-key round-tripping.
- `src/ui/plugin.html` — the UI shell: instance selector grouped by client,
  placeholder tabs, and a functional Settings tab.

## Multi-tenant (brief §3.1)

This plugin will eventually manage TrueNAS instances belonging to
**different clients** in the same panel (IDKmanager, SACEI, INGESA,
GeoSpace, ...), not just the operator's own infrastructure. Every instance
in `config.json` carries a free-form `client_id` field from F0 onward — the
Settings UI and the instance selector group by it. **F0 does not yet call
PegaProx's `check_cluster_access`** for per-client scoping — that mapping
(client_id ↔ Proxmox cluster) lands in F1+, once more than one real client
is on-boarded. Until then, every write in this plugin (config, instance
test) is gated on the admin role, same as the rest of the plugin's RBAC.

## RBAC

PegaProx's `PERMISSIONS` table is fixed — a plugin cannot register new
assignable permissions. This plugin reuses:

| Action | Permission |
|---|---|
| `ui` (the tab itself) | `storage.view` |
| `config`, `config/save`, `instances/test` (touch API keys) | admin role only |

`admin` always passes any `has_permission` check automatically.

## Configuration

Each entry in `instances[]`:

| Field | Meaning |
|---|---|
| `id` | Stable identifier, unique within this plugin's config |
| `name` | Display name |
| `client_id` | Free-form tenant namespace (`idkmanager`, `sacei`, ...) |
| `host` / `port` | TrueNAS UI host and port |
| `use_tls` | **Must be `true`** whenever an API key is set — TrueNAS auto-revokes a key used over plain HTTP. `config/save` rejects the combination. |
| `verify_tls` | Whether to verify the appliance's TLS certificate (usually self-signed → `false`) |
| `api_key_ro` / `api_key_rw` | Service-account keys (`svc-pegaprox-ro`/`svc-pegaprox-rw`); masked as `***` on every `GET config`, round-tripped on save |
| `readonly` | Server-evaluated kill-switch — stays effective even if `api_key_rw` is set |

`poll.{fast_s,slow_s,cold_s}` — the polling budget from the brief §4.3;
unused in F0 (no subsystem polls anything yet), validated and persisted for
F1 to consume.

## The only real TrueNAS interaction allowed in F0

`instances/test` connects the WebSocket and calls
`auth.login_with_api_key` — nothing else. It's used from the Settings tab's
"Probar conexión" button, works against either a saved instance (`id`) or an
unsaved draft from the form, and never persists anything to `config.json`.

## Design decisions taken where the brief was ambiguous

- **Reconnect trigger, not a background poller.** The client's automatic
  reconnection only runs after the WebSocket reader thread observes an
  *unexpected* disconnect (a `recv()` failure) — there is no separate
  keepalive/health-check thread pinging the socket. This satisfies "backoff
  + jitter reconnection" without adding a second timer to reason about in
  F0; F1's job-tracking loop can add liveness probing if TrueNAS's own
  socket timeout proves too silent in practice.
- **Relogin/resubscribe only on recovery, not on the very first connect.**
  An earlier draft called `_relogin_and_resubscribe()` unconditionally after
  every successful `connect()`. That recursed: `subscribe()` registers its
  callback *before* issuing the `core.subscribe` call, so the first-ever
  connect (triggered lazily by that same `subscribe()`) would re-enter
  `call()` for the same subscription while the outer call was still
  in-flight. Fixed by only running relogin/resubscribe from the
  `_background_reconnect` path (a genuine post-drop recovery), never from
  plain lazy-connect.
- **`log_audit(..., cluster=client_id)` not used as written in brief §3.1.**
  Without a live PegaProx host to confirm `log_audit`'s real keyword
  arguments, passing an unverified `cluster=` kwarg risked a runtime
  `TypeError` in production. `client_id` is instead folded into the
  `details` string of every audit call — same information, no dependency on
  an unconfirmed signature. Revisit once `pegaprox/utils/audit.py` is
  readable from this workspace.
- **`instances/test` accepts drafts, not just saved instances**, so the
  Settings "Probar conexión" button works before hitting Save (per the UI
  flow described in brief §6/§7) — it resolves a masked `api_key_ro` back to
  the stored value only when an `id` matching a saved instance is supplied.

## Pendiente de F0-deploy / F1 (explicitly out of scope here)

- **`websocket-client` vendoring.** CT119 has no external DNS/internet
  access. `requirements.txt` declares the dependency, but making it
  available offline (vendored into the plugin's cache dir, or pre-installed
  from a LAN-reachable mirror) is deploy work, not build work — F0 does not
  solve it.
- No subsystem collectors/writers (`src/subsystems/` is an empty package
  with a docstring) — pools/datasets/snapshots/shares/replication/apps_vms
  land in F1+.
- No `check_cluster_access` per-client RBAC — admin-only gate until a second
  real client (SACEI/INGESA/GeoSpace) is on-boarded.
- No installation on CT119, no API key of any real instance connected — both
  require explicit operator confirmation in a separate session (brief §0.5).

## Development

```
pip install -r requirements-dev.txt
pytest -q
```

`tests/conftest.py` stubs `flask` and `pegaprox.*` at the module level so
`__init__.py` imports standalone in CI without a live PegaProx host. `core/`
tests (`tests/unit/`) never touch a real socket — a `FakeTransport` drives
`send`/`recv` through an in-memory queue.

## Deploy

`install.sh` copies `__init__.py`, `manifest.json` and `src/` into
`/opt/PegaProx/plugins/truenas`, seeds an empty `config.json` (instances are
added afterwards from the Settings tab, never shipped in the repo), and
installs a systemd timer (`truenas-maintenance.sh`) that restores the plugin
from a cache outside `/opt/PegaProx` if a PegaProx upgrade ever wipes it.

`uninstall.sh` removes the plugin, its guard timer, and its config
(including any saved API keys).

**Deployment is NOT performed by this repository's automation.** Running
`install.sh` against production (CT119/pve1) requires explicit operator
confirmation in a separate session — see brief §0.5.
