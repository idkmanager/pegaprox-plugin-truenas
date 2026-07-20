# Fixtures

Real, read-only JSON-RPC responses captured from `.64`'s `/api/docs/` go
here once F1 starts building subsystem collectors against confirmed
payloads. Empty in F0 — no subsystem calls the real appliance yet, so there
is nothing to capture beyond the transport-level framing already covered by
`tests/unit/test_ws_client.py`'s fakes.
