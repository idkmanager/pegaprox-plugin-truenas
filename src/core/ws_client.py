# -*- coding: utf-8 -*-
"""Generic, reusable JSON-RPC 2.0 client over a persistent WebSocket, for the
TrueNAS SCALE middleware API (``wss://<host>:<port>/api/current``).

Design constraints (see PEGAPROX_PLUGIN_TRUENAS_BRIEF.md §2/§4/§9):

- REST v2.0 is deprecated in 25.10 and removed in TrueNAS 26 — this client is
  WebSocket JSON-RPC 2.0 ONLY, from F0.
- Envelope: ``{"jsonrpc": "2.0", "id": N, "method": ..., "params": [...]}``.
  Errors come back as ``response["error"]`` (often nested under
  ``error.data.reason``) — never normalized here (attrs passthrough).
- Auth is a *method call* after the socket is open:
  ``auth.login_with_api_key(["<key>"])``. Never retried automatically and
  never logged.
- Reconnection uses exponential backoff + jitter. A dropped socket does not
  crash anything at import time or at construction time — this module is
  lazy-connect: no network I/O happens until the first ``call()``.
- Retries are for READS only. Callers decide idempotency; this client never
  silently retries a ``call()`` that already reached the server — a timeout
  or connection error is raised to the caller, who is responsible for
  deciding whether to retry a write.

The real transport (``websocket-client``) is imported lazily inside
``_default_transport_factory`` so this module can be imported (and its pure
logic unit-tested) even in an environment where the dependency is not yet
installed — matching the "no external DNS on CT119" constraint from the
brief: importing the plugin must never explode just because a dependency
isn't vendored yet.
"""

import itertools
import json
import logging
import random
import ssl
import threading
import time

from .errors import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASRPCError,
    TrueNASTimeoutError,
)

log = logging.getLogger('plugin.truenas.ws_client')

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RECONNECT_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE_S = 1.0
DEFAULT_BACKOFF_CAP_S = 30.0


def _default_transport_factory(url, verify_tls, timeout):
    """Open a real WebSocket connection using ``websocket-client``.

    Imported lazily so importing this module never requires the dependency
    to be installed (only actually connecting does).
    """
    import websocket  # intentional lazy import — see module docstring

    sslopt = None if verify_tls else {'cert_reqs': ssl.CERT_NONE}
    return websocket.create_connection(url, timeout=timeout, sslopt=sslopt)


class TrueNASWSClient:
    """Persistent JSON-RPC 2.0 client for one TrueNAS instance's WebSocket.

    Not thread-hostile: ``call()`` may be invoked concurrently from multiple
    threads — each gets its own ``id`` and waits only for its own response.
    """

    def __init__(self, host, port, use_tls=True, verify_tls=False,
                 timeout=DEFAULT_TIMEOUT, transport_factory=None,
                 auto_reconnect=True,
                 max_reconnect_attempts=DEFAULT_MAX_RECONNECT_ATTEMPTS,
                 backoff_base_s=DEFAULT_BACKOFF_BASE_S,
                 backoff_cap_s=DEFAULT_BACKOFF_CAP_S,
                 sleep_fn=time.sleep):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self._sleep = sleep_fn
        self._transport_factory = transport_factory or _default_transport_factory

        self._ws = None
        self._connected = False
        self._connect_lock = threading.RLock()
        self._send_lock = threading.Lock()

        self._id_seq = itertools.count(1)
        self._id_lock = threading.Lock()

        self._pending = {}          # req_id -> {'event': Event, 'response': dict|None}
        self._pending_lock = threading.Lock()

        self._subscriptions = {}    # event_name -> [callback, ...]
        self._subscriptions_lock = threading.Lock()

        self._api_key = None        # kept only to support relogin after reconnect
        self._last_error = None

        self._reader_thread = None
        self._stop_reader = threading.Event()

    # -- public state -------------------------------------------------------

    @property
    def is_connected(self):
        return self._connected

    @property
    def last_error(self):
        return self._last_error

    def url(self):
        scheme = 'wss' if self.use_tls else 'ws'
        return f'{scheme}://{self.host}:{self.port}/api/current'

    # -- connection lifecycle ------------------------------------------------

    def connect(self):
        """Open the WebSocket if not already connected. Idempotent.

        Raises ``TrueNASConnectionError`` on failure; never raises on an
        already-open connection.
        """
        with self._connect_lock:
            if self._connected:
                return
            try:
                self._ws = self._transport_factory(self.url(), self.verify_tls, self.timeout)
            except Exception as e:
                self._last_error = str(e)
                raise TrueNASConnectionError(f'could not connect to {self.host}:{self.port}: {e}') from e
            self._connected = True
            self._last_error = None
            self._stop_reader.clear()
            self._reader_thread = threading.Thread(
                target=self._read_loop, name=f'truenas-ws-{self.host}', daemon=True)
            self._reader_thread.start()
            log.info(f'[truenas] connected to {self.host}:{self.port}')

    def close(self):
        with self._connect_lock:
            self._stop_reader.set()
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
            self._connected = False
            self._fail_all_pending('connection closed')

    def _ensure_connected(self):
        if self._connected:
            return
        self._connect_with_backoff()

    def _connect_with_backoff(self, max_attempts=None):
        """Try ``connect()`` repeatedly with exponential backoff + jitter.

        Raises ``TrueNASConnectionError`` (the last failure) once
        ``max_attempts`` is exhausted. Backoff delay is
        ``min(cap, base * 2**attempt) * (0.5 + random())`` — capped jitter,
        never a thundering herd against a struggling appliance.
        """
        attempts = max_attempts if max_attempts is not None else self.max_reconnect_attempts
        last_exc = None
        for attempt in range(attempts):
            try:
                self.connect()
                return
            except TrueNASConnectionError as e:
                last_exc = e
                if attempt < attempts - 1:
                    delay = min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                    delay *= (0.5 + random.random())
                    log.warning(f'[truenas] reconnect attempt {attempt + 1}/{attempts} '
                                f'failed, retrying in {delay:.1f}s: {e}')
                    self._sleep(delay)
        raise last_exc or TrueNASConnectionError('reconnect failed for unknown reason')

    def _relogin_and_resubscribe(self):
        """After an automatic reconnect, re-login (if we had a session) and
        re-subscribe to every previously-registered event name — otherwise
        jobs/notifications the UI is watching would go silently orphaned."""
        if self._api_key:
            try:
                self._do_login(self._api_key)
            except TrueNASError as e:
                log.error(f'[truenas] relogin after reconnect failed: {e}')
                return
        with self._subscriptions_lock:
            names = list(self._subscriptions.keys())
        for name in names:
            try:
                self.call('core.subscribe', [name])
            except TrueNASError as e:
                log.warning(f'[truenas] re-subscribe to {name} failed: {e}')

    def _handle_unexpected_disconnect(self, reason):
        with self._connect_lock:
            self._connected = False
            self._last_error = reason
        self._fail_all_pending(reason)
        log.warning(f'[truenas] socket to {self.host}:{self.port} dropped: {reason}')
        if self.auto_reconnect:
            threading.Thread(target=self._background_reconnect,
                              name=f'truenas-reconnect-{self.host}', daemon=True).start()

    def _background_reconnect(self):
        """Recovery path after an *unexpected* drop (see
        ``_handle_unexpected_disconnect``) — reconnects and then, unlike a
        plain lazy first connect, re-logs-in and re-subscribes so an
        already-established session doesn't silently lose its jobs feed.
        NOT used by ``_ensure_connected()``'s ordinary lazy-connect path —
        calling ``_relogin_and_resubscribe`` there would recurse into
        ``call()`` from inside the very ``login()``/``subscribe()`` that
        triggered the first connect."""
        try:
            self._connect_with_backoff()
            self._relogin_and_resubscribe()
        except TrueNASConnectionError as e:
            log.error(f'[truenas] gave up reconnecting to {self.host}:{self.port}: {e}')

    # -- id / pending bookkeeping ---------------------------------------------

    def _next_id(self):
        with self._id_lock:
            return next(self._id_seq)

    def _fail_all_pending(self, reason):
        with self._pending_lock:
            items = list(self._pending.items())
            self._pending.clear()
        for _req_id, entry in items:
            entry['response'] = None
            entry['error_reason'] = reason
            entry['event'].set()

    # -- request/response -----------------------------------------------------

    def call(self, method, params=None, timeout=None):
        """Issue a JSON-RPC 2.0 call and block for its matching response.

        Returns ``response['result']``. Raises:
          - ``TrueNASConnectionError`` if the socket cannot be (re)established
            or drops before a response arrives.
          - ``TrueNASTimeoutError`` if no response for this ``id`` arrives
            within ``timeout`` seconds.
          - ``TrueNASRPCError`` if the response carries a JSON-RPC ``error``.
        """
        timeout = self.timeout if timeout is None else timeout
        self._ensure_connected()

        req_id = self._next_id()
        entry = {'event': threading.Event(), 'response': None, 'error_reason': None}
        with self._pending_lock:
            self._pending[req_id] = entry

        envelope = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params or []}
        try:
            self._send(json.dumps(envelope))
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise TrueNASConnectionError(f'send failed for {method}: {e}') from e

        if not entry['event'].wait(timeout):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise TrueNASTimeoutError(f"timeout ({timeout}s) waiting for '{method}' response")

        response = entry['response']
        if response is None:
            raise TrueNASConnectionError(
                entry['error_reason'] or f"connection lost before '{method}' answered")

        error = response.get('error')
        if error:
            raise TrueNASRPCError(method, error)
        return response.get('result')

    def _send(self, raw):
        with self._send_lock:
            if not self._connected or self._ws is None:
                raise TrueNASConnectionError('not connected')
            self._ws.send(raw)

    def _read_loop(self):
        while not self._stop_reader.is_set():
            try:
                raw = self._ws.recv()
            except Exception as e:
                if not self._stop_reader.is_set():
                    self._handle_unexpected_disconnect(str(e))
                return
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                log.warning('[truenas] discarding non-JSON frame from socket')
                continue
            if isinstance(msg, dict) and msg.get('id') is not None:
                self._dispatch_response(msg)
            else:
                self._dispatch_notification(msg)

    def _dispatch_response(self, msg):
        req_id = msg.get('id')
        with self._pending_lock:
            entry = self._pending.pop(req_id, None)
        if entry is None:
            log.debug(f'[truenas] response for unknown/expired id={req_id}, dropping')
            return
        entry['response'] = msg
        entry['event'].set()

    def _dispatch_notification(self, msg):
        """Route an unsolicited server message (job/event notification) to
        subscribers. TrueNAS's exact collection_update envelope shape is
        deferred to F1 (jobs.py) — F0 only guarantees callbacks are invoked
        with the raw decoded message for whichever ``method``/collection
        name they subscribed to, never crashing the reader loop if a
        callback misbehaves."""
        name = msg.get('method') or (msg.get('params') or {}).get('msg')
        with self._subscriptions_lock:
            callbacks = list(self._subscriptions.get(name, []))
        for cb in callbacks:
            try:
                cb(msg)
            except Exception as e:
                log.error(f'[truenas] subscriber callback for {name!r} raised: {e}')

    # -- auth -----------------------------------------------------------------

    def login(self, api_key):
        """Authenticate the just-opened socket. Never logs the key itself."""
        return self._do_login(api_key)

    def _do_login(self, api_key):
        try:
            result = self.call('auth.login_with_api_key', [api_key])
        except TrueNASRPCError as e:
            raise TrueNASAuthError('auth.login_with_api_key', e.error) from e
        if result is False:
            raise TrueNASAuthError('auth.login_with_api_key', {'message': 'rejected'})
        self._api_key = api_key
        return result

    # -- events (hook for F1: core.get_jobs) -----------------------------------

    def subscribe(self, name, callback=None):
        """Subscribe to a server-side event/collection name (e.g.
        ``core.get_jobs``). Registers ``callback`` for later dispatch by
        ``_dispatch_notification`` and issues ``core.subscribe`` on the wire.
        Not exercised by any F0 route — the interface is prepared for F1's
        job-tracking, per the brief."""
        with self._subscriptions_lock:
            self._subscriptions.setdefault(name, [])
            if callback is not None:
                self._subscriptions[name].append(callback)
        return self.call('core.subscribe', [name])

    def unsubscribe(self, name, callback=None):
        with self._subscriptions_lock:
            if name not in self._subscriptions:
                return
            if callback is None:
                self._subscriptions.pop(name, None)
            else:
                self._subscriptions[name] = [c for c in self._subscriptions[name] if c != callback]
