# API (F0)

All routes are under `/api/plugins/truenas/api/<path>`.

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| GET | `ui` | `storage.view` | — | The UI shell (`text/html`) |
| GET | `config` | admin | — | `{"instances": [...masked], "instances_by_client": [...], "poll": {...}}` |
| POST | `config/save` | admin | `{"instances": [...], "poll": {...}}` | `{"ok": true, "instances": N}` or `{"error": "..."}` (400) |
| POST | `instances/test` | admin | `{"id"?, "host", "port", "use_tls"?, "verify_tls"?, "api_key_ro"}` | `{"ok": bool, "error": str|null}` |

`instances/test` is the only route that talks to a real TrueNAS instance in
F0 — `connect()` + `auth.login_with_api_key(["<key>"])`, nothing else. It
never persists to `config.json`.

No subsystem endpoints (pools/datasets/snapshots/shares/replication/
apps_vms) exist yet — those are F1+, following the `Subsystem` contract
described in `PEGAPROX_PLUGIN_TRUENAS_BRIEF.md` §2.
