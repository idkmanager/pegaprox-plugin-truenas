# -*- coding: utf-8 -*-
"""Unit tests for core.ws_client.TrueNASWSClient — request/response framing,
concurrent ``id`` handling, timeout, JSON-RPC error propagation and
reconnection backoff. No real network: a FakeTransport drives ``send``/
``recv`` through an in-memory queue."""

import json
import queue
import threading
import time

import pytest

from core.errors import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASRPCError,
    TrueNASTimeoutError,
)
from core.ws_client import TrueNASWSClient


class FakeTransport:
    """Stand-in for the object ``websocket.create_connection`` returns."""

    def __init__(self):
        self._inbox = queue.Queue()
        self.sent = []
        self.closed = False

    def send(self, raw):
        self.sent.append(raw)

    def recv(self):
        item = self._inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True

    def push(self, obj):
        self._inbox.put(json.dumps(obj) if not isinstance(obj, str) else obj)

    def push_error(self, exc):
        self._inbox.put(exc)


def _wait_for(predicate, timeout=2.0):
    """Poll ``predicate`` until true or timeout — condition-based waiting,
    never an arbitrary sleep."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _shutdown(client, ft):
    """Unblock a FakeTransport stuck in queue.get() so the reader daemon
    thread can observe stop_reader and exit cleanly."""
    client.close()
    ft.push({'jsonrpc': '2.0', 'method': 'noop', 'params': {}})


def _client_with(ft, **kwargs):
    return TrueNASWSClient('truenas.example', 443,
                            transport_factory=lambda *a, **k: ft, **kwargs)


# ---------------------------------------------------------------------------
# Framing: request envelope + matching response by id
# ---------------------------------------------------------------------------

def test_call_sends_correct_jsonrpc_envelope():
    ft = FakeTransport()
    client = _client_with(ft)
    result_box = {}

    def do_call():
        result_box['result'] = client.call('system.info', ['a', 'b'])

    t = threading.Thread(target=do_call)
    t.start()
    assert _wait_for(lambda: ft.sent)
    req = json.loads(ft.sent[0])
    assert req['jsonrpc'] == '2.0'
    assert req['method'] == 'system.info'
    assert req['params'] == ['a', 'b']
    assert isinstance(req['id'], int)

    ft.push({'jsonrpc': '2.0', 'id': req['id'], 'result': {'ok': True}})
    t.join(timeout=2)
    assert result_box['result'] == {'ok': True}
    _shutdown(client, ft)


def test_call_defaults_params_to_empty_list():
    ft = FakeTransport()
    client = _client_with(ft)

    def do_call():
        client.call('core.get_jobs')

    t = threading.Thread(target=do_call)
    t.start()
    assert _wait_for(lambda: ft.sent)
    req = json.loads(ft.sent[0])
    assert req['params'] == []
    ft.push({'jsonrpc': '2.0', 'id': req['id'], 'result': []})
    t.join(timeout=2)
    _shutdown(client, ft)


def test_concurrent_calls_get_matched_to_their_own_id():
    ft = FakeTransport()
    client = _client_with(ft)
    results = {}

    def do_call(name):
        results[name] = client.call(f'method.{name}', [name])

    threads = [threading.Thread(target=do_call, args=(n,)) for n in ('a', 'b', 'c')]
    for t in threads:
        t.start()
    assert _wait_for(lambda: len(ft.sent) == 3)

    # Answer out of order to prove dispatch is by id, not send order.
    reqs = [json.loads(s) for s in ft.sent]
    for req in reversed(reqs):
        ft.push({'jsonrpc': '2.0', 'id': req['id'], 'result': f"result-for-{req['params'][0]}"})

    for t in threads:
        t.join(timeout=2)
    assert results == {'a': 'result-for-a', 'b': 'result-for-b', 'c': 'result-for-c'}
    _shutdown(client, ft)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

def test_call_raises_timeout_when_no_response_arrives():
    ft = FakeTransport()
    client = _client_with(ft)
    with pytest.raises(TrueNASTimeoutError):
        client.call('system.info', timeout=0.05)
    _shutdown(client, ft)


# ---------------------------------------------------------------------------
# JSON-RPC error propagation
# ---------------------------------------------------------------------------

def test_call_raises_rpc_error_when_response_has_error_field():
    ft = FakeTransport()
    client = _client_with(ft)
    result_box = {}

    def do_call():
        try:
            client.call('pool.query')
        except TrueNASRPCError as e:
            result_box['exc'] = e

    t = threading.Thread(target=do_call)
    t.start()
    assert _wait_for(lambda: ft.sent)
    req = json.loads(ft.sent[0])
    ft.push({'jsonrpc': '2.0', 'id': req['id'],
              'error': {'data': {'reason': 'Pool not found'}}})
    t.join(timeout=2)
    assert isinstance(result_box.get('exc'), TrueNASRPCError)
    assert 'Pool not found' in str(result_box['exc'])
    _shutdown(client, ft)


def test_login_failure_raises_auth_error_without_leaking_key():
    ft = FakeTransport()
    client = _client_with(ft)
    result_box = {}

    def do_login():
        try:
            client.login('super-secret-api-key')
        except TrueNASAuthError as e:
            result_box['exc'] = e

    t = threading.Thread(target=do_login)
    t.start()
    assert _wait_for(lambda: ft.sent)
    req = json.loads(ft.sent[0])
    assert req['method'] == 'auth.login_with_api_key'
    assert req['params'] == ['super-secret-api-key']
    ft.push({'jsonrpc': '2.0', 'id': req['id'],
              'error': {'message': 'invalid api key'}})
    t.join(timeout=2)
    exc = result_box.get('exc')
    assert isinstance(exc, TrueNASAuthError)
    assert 'super-secret-api-key' not in str(exc)
    _shutdown(client, ft)


# ---------------------------------------------------------------------------
# Reconnection: exponential backoff + jitter
# ---------------------------------------------------------------------------

def test_connect_with_backoff_retries_then_succeeds():
    ft = FakeTransport()
    attempts = {'n': 0}

    def factory(url, verify_tls, timeout):
        attempts['n'] += 1
        if attempts['n'] < 3:
            raise RuntimeError('connection refused (simulated)')
        return ft

    client = TrueNASWSClient('truenas.example', 443, transport_factory=factory,
                              sleep_fn=lambda s: None)  # skip real backoff delay
    client._connect_with_backoff(max_attempts=5)
    assert client.is_connected
    assert attempts['n'] == 3
    _shutdown(client, ft)


def test_connect_with_backoff_raises_after_exhausting_attempts():
    def factory(url, verify_tls, timeout):
        raise RuntimeError('connection refused (simulated)')

    client = TrueNASWSClient('truenas.example', 443, transport_factory=factory,
                              sleep_fn=lambda s: None)
    with pytest.raises(TrueNASConnectionError):
        client._connect_with_backoff(max_attempts=3)
    assert not client.is_connected


def test_lazy_connect_never_touches_network_at_construction():
    # No transport_factory network call should happen just by building the
    # client — proves "no crash on import / construction without network".
    called = {'n': 0}

    def factory(url, verify_tls, timeout):
        called['n'] += 1
        raise AssertionError('should not connect until call()/connect() is invoked')

    TrueNASWSClient('truenas.example', 443, transport_factory=factory)
    assert called['n'] == 0


# ---------------------------------------------------------------------------
# Unexpected disconnect fails in-flight calls
# ---------------------------------------------------------------------------

def test_connect_raises_connection_error_directly_without_retry():
    calls = {'n': 0}

    def factory(url, verify_tls, timeout):
        calls['n'] += 1
        raise OSError('refused')

    client = TrueNASWSClient('truenas.example', 443, transport_factory=factory)
    with pytest.raises(TrueNASConnectionError):
        client.connect()
    assert calls['n'] == 1
    assert not client.is_connected


def test_close_marks_disconnected_and_closes_transport():
    ft = FakeTransport()
    client = _client_with(ft)
    client.connect()
    assert client.is_connected
    ft.push({'jsonrpc': '2.0', 'method': 'noop'})  # unblock reader before close races it
    client.close()
    assert not client.is_connected
    assert ft.closed


def test_subscribe_registers_callback_and_dispatches_notifications():
    ft = FakeTransport()
    client = _client_with(ft)
    received = []

    def on_event(msg):
        received.append(msg)

    def do_subscribe():
        client.subscribe('core.get_jobs', callback=on_event)

    t = threading.Thread(target=do_subscribe)
    t.start()
    assert _wait_for(lambda: ft.sent)
    req = json.loads(ft.sent[0])
    assert req['method'] == 'core.subscribe'
    assert req['params'] == ['core.get_jobs']
    ft.push({'jsonrpc': '2.0', 'id': req['id'], 'result': True})
    t.join(timeout=2)

    # A later unsolicited notification (no 'id') for that name is dispatched.
    ft.push({'method': 'core.get_jobs', 'params': {'id': 42}})
    assert _wait_for(lambda: received)
    assert received[0]['params'] == {'id': 42}
    _shutdown(client, ft)


def test_unsubscribe_stops_further_dispatch():
    ft = FakeTransport()
    client = _client_with(ft)
    received = []
    with client._subscriptions_lock:
        client._subscriptions['core.get_jobs'] = [received.append]
    client.unsubscribe('core.get_jobs', received.append)
    with client._subscriptions_lock:
        assert client._subscriptions.get('core.get_jobs') == []


def test_background_reconnect_recovers_after_unexpected_disconnect():
    ft1 = FakeTransport()
    ft2 = FakeTransport()
    transports = iter([ft1, ft2])

    def factory(url, verify_tls, timeout):
        return next(transports)

    client = TrueNASWSClient('truenas.example', 443, transport_factory=factory,
                              sleep_fn=lambda s: None)
    client.connect()
    assert client.is_connected

    ft1.push_error(ConnectionResetError('simulated socket drop'))
    # is_connected alone is a bad wait condition here: it's already True
    # before the drop is even processed. Wait for the *new* transport to
    # actually take over instead.
    assert _wait_for(lambda: client._ws is ft2, timeout=3)
    assert client.is_connected
    _shutdown(client, ft2)


def test_reconnect_relogins_with_stored_api_key():
    ft1 = FakeTransport()
    ft2 = FakeTransport()
    transports = iter([ft1, ft2])

    def factory(url, verify_tls, timeout):
        return next(transports)

    client = TrueNASWSClient('truenas.example', 443, transport_factory=factory,
                              sleep_fn=lambda s: None)
    client.connect()

    def do_login():
        client.login('ro-key-123')

    t = threading.Thread(target=do_login)
    t.start()
    assert _wait_for(lambda: ft1.sent)
    req = json.loads(ft1.sent[0])
    ft1.push({'jsonrpc': '2.0', 'id': req['id'], 'result': True})
    t.join(timeout=2)

    ft1.push_error(ConnectionResetError('drop'))
    assert _wait_for(lambda: ft2.sent, timeout=3)
    relogin_req = json.loads(ft2.sent[0])
    assert relogin_req['method'] == 'auth.login_with_api_key'
    assert relogin_req['params'] == ['ro-key-123']
    ft2.push({'jsonrpc': '2.0', 'id': relogin_req['id'], 'result': True})
    _shutdown(client, ft2)


def test_unexpected_disconnect_fails_pending_call():
    ft = FakeTransport()
    client = _client_with(ft, auto_reconnect=False)
    result_box = {}

    def do_call():
        try:
            client.call('system.info', timeout=2)
        except TrueNASConnectionError as e:
            result_box['exc'] = e

    t = threading.Thread(target=do_call)
    t.start()
    assert _wait_for(lambda: ft.sent)
    ft.push_error(ConnectionResetError('simulated socket drop'))
    t.join(timeout=2)
    assert isinstance(result_box.get('exc'), TrueNASConnectionError)
    assert not client.is_connected
