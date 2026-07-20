# Troubleshooting (F0)

- **"Conexión OK" never appears / times out** — check `use_tls`: TrueNAS
  auto-revokes an API key the first time it's used over plain HTTP. If
  `.64`'s UI on `:81` doesn't actually serve HTTPS, fix that on the
  appliance (or mint a fresh key after enabling TLS) before testing.
- **`config/save` rejects with "use_tls debe ser true..."** — you have an
  `api_key_ro`/`api_key_rw` set with `use_tls: false`. This is a hard guard,
  not a bug — see the point above.
- **`instances/test` returns "host and api_key_ro are required"** — either
  fill both fields in the draft form, or reference a saved instance by `id`
  (its stored key is used automatically; a literal `"***"` in the form also
  resolves to the saved key).
- **Plugin doesn't show up after `install.sh`** — the PegaProx DB may be
  SQLCipher-encrypted; `install.sh` tries the API enable fallback, but if
  that also fails, enable manually: PegaProx > Settings > Plugins >
  "TrueNAS" > Enable.
- **`ModuleNotFoundError: websocket` on the real host** — `websocket-client`
  isn't vendored/installed yet on CT119 (no external DNS there). This is
  explicitly deferred to F0-deploy — see README.md "Pendiente de
  F0-deploy".
