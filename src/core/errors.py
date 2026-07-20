# -*- coding: utf-8 -*-
"""Typed exceptions for the TrueNAS JSON-RPC client.

Kept in their own module (no Flask/pegaprox imports) so both ``ws_client``
and ``conn_manager`` — and anything importing just the core layer in tests —
can catch specific failure modes without depending on the rest of the plugin.
"""


class TrueNASError(Exception):
    """Base class for every error raised by the TrueNAS core client."""


class TrueNASConnectionError(TrueNASError):
    """The WebSocket could not be opened, or dropped mid-call."""


class TrueNASTimeoutError(TrueNASError):
    """A call() did not get a matching response within its timeout."""


class TrueNASRPCError(TrueNASError):
    """The middleware answered with a JSON-RPC ``error`` object.

    ``error`` is the raw ``error`` dict from the response (may carry
    ``error['data']['reason']`` per the TrueNAS convention) — kept as-is
    (attrs passthrough, per the brief's Subsystem contract) rather than
    normalized, so callers can inspect whatever the middleware sent.
    """

    def __init__(self, method: str, error: dict):
        self.method = method
        self.error = error or {}
        reason = self._extract_reason(self.error)
        super().__init__(f"TrueNAS RPC error on '{method}': {reason}")

    @staticmethod
    def _extract_reason(error: dict) -> str:
        data = error.get('data') if isinstance(error, dict) else None
        if isinstance(data, dict) and data.get('reason'):
            return str(data['reason'])
        if isinstance(error, dict) and error.get('message'):
            return str(error['message'])
        return str(error)


class TrueNASAuthError(TrueNASRPCError):
    """auth.login_with_api_key failed (bad/revoked key). Never carries the
    key itself — only the middleware's error payload."""
