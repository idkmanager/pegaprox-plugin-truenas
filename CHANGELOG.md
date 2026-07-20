# Changelog

## [0.1.0] - 2026-07-20

Initial release — F0 (installable skeleton).

- Generic, reusable JSON-RPC 2.0 client over a persistent WebSocket
  (`src/core/ws_client.py`) for the TrueNAS SCALE middleware
  (`wss://<host>:<port>/api/current`): request/response framing with
  concurrent `id` handling, typed timeouts/errors (`TrueNASConnectionError`,
  `TrueNASTimeoutError`, `TrueNASRPCError`, `TrueNASAuthError`), lazy-connect
  (no network I/O at import/construction time), and automatic reconnection
  with exponential backoff + jitter that re-logs-in and re-subscribes after
  an unexpected drop.
- `login(api_key)` (`auth.login_with_api_key`) — never logs the key itself,
  including on failure.
- Event subscription hook (`subscribe`/`unsubscribe`, wired to
  `core.subscribe`) prepared for F1's job tracking (`core.get_jobs`); not
  exercised by any F0 route.
- Per-instance connection manager (`src/core/conn_manager.py`), lazy-connect,
  multi-instance from day one.
- Multi-tenant config schema (`config.example.json`): every instance carries
  a free-form `client_id` (`idkmanager`, `sacei`, `ingesa`, `geospace`, ...)
  so the plugin can host TrueNAS instances belonging to different clients in
  the same PegaProx panel — the field is persisted and used to group the
  Settings UI and instance selector; the real `check_cluster_access` gate per
  client is deferred to F1+.
- Config round-trip masking (`***`) for `api_key_ro`/`api_key_rw`, atomic
  `config.json` writes (chmod 600), and a hard safety guard rejecting
  `use_tls: false` whenever an API key is configured (TrueNAS auto-revokes a
  key used over plain HTTP).
- Routes (`/api/plugins/truenas/api/*`): `ui`, `config` (GET, masked),
  `config/save` (POST), `instances/test` (POST — the only real interaction
  with a TrueNAS instance allowed in F0: connect + `auth.login_with_api_key`,
  nothing else, never persisted).
- RBAC via existing PegaProx builtin verbs (`storage.view` for the UI shell;
  admin role for config/instance-test, since they touch credentials) — the
  plugin cannot register new assignable permissions.
- UI shell (`src/ui/plugin.html`, vanilla HTML/CSS/JS, no build step, no
  CDN): instance selector grouped by client, empty placeholder tabs for
  Overview/Pools & Discos/Datasets/Snapshots/Shares/Replicación/Apps-VMs, and
  a functional Settings tab (instance CRUD + "Probar conexión"). Theme
  inherited via `?theme=cloud`.
- Install/uninstall scripts mirroring `pegaprox-plugin-wake-on-lan`'s proven
  pattern: cache outside `/opt/PegaProx` (`/usr/local/lib/truenas`) +
  `truenas-maintenance.timer` persistence guard, SQLCipher-safe enable
  fallback, systemd-user-aware chown.
- 50 unit/route tests, 90%+ line coverage on `core/`.

No subsystem (pools/datasets/snapshots/shares/replication/apps_vms) is
implemented yet — every non-Settings tab is empty chrome. See
`PEGAPROX_PLUGIN_TRUENAS_BRIEF.md` for the F1+ roadmap.
