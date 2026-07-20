# -*- coding: utf-8 -*-
"""ConnectionManager: lazy creation, one client per instance id, and the
instances/test path (connect + login, nothing else)."""

from core.conn_manager import ConnectionManager
from core.errors import TrueNASAuthError, TrueNASConnectionError


class _FakeClient:
    def __init__(self, host, port, use_tls=True, verify_tls=False,
                 connect_error=None, login_error=None):
        self.host = host
        self.port = port
        self.is_connected = False
        self.last_error = None
        self._connect_error = connect_error
        self._login_error = login_error
        self.logged_in_with = None
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        if self._connect_error:
            self.last_error = str(self._connect_error)
            raise self._connect_error
        self.is_connected = True

    def login(self, api_key):
        if self._login_error:
            raise self._login_error
        self.logged_in_with = api_key

    def close(self):
        self.is_connected = False


def _instance_cfg(id_='datos-64'):
    return {'id': id_, 'host': '192.0.2.64', 'port': 81,
            'use_tls': True, 'verify_tls': False}


def test_get_connection_creates_and_reuses_same_client():
    created = []

    def factory(**kwargs):
        c = _FakeClient(**kwargs)
        created.append(c)
        return c

    mgr = ConnectionManager(client_factory=factory)
    c1 = mgr.get_connection(_instance_cfg())
    c2 = mgr.get_connection(_instance_cfg())
    assert c1 is c2
    assert len(created) == 1


def test_get_connection_does_not_connect_eagerly():
    created = []

    def factory(**kwargs):
        c = _FakeClient(**kwargs)
        created.append(c)
        return c

    mgr = ConnectionManager(client_factory=factory)
    mgr.get_connection(_instance_cfg())
    assert created[0].connect_calls == 0


def test_test_connection_ok():
    mgr = ConnectionManager(client_factory=lambda **kw: _FakeClient(**kw))
    result = mgr.test_connection(_instance_cfg(), 'ro-key')
    assert result == {'ok': True, 'error': None}


def test_test_connection_reports_connect_error_without_raising():
    def factory(**kw):
        return _FakeClient(connect_error=TrueNASConnectionError('refused'), **kw)

    mgr = ConnectionManager(client_factory=factory)
    result = mgr.test_connection(_instance_cfg(), 'ro-key')
    assert result['ok'] is False
    assert 'refused' in result['error']


def test_test_connection_reports_auth_error_without_raising():
    def factory(**kw):
        return _FakeClient(
            login_error=TrueNASAuthError('auth.login_with_api_key', {'message': 'bad key'}), **kw)

    mgr = ConnectionManager(client_factory=factory)
    result = mgr.test_connection(_instance_cfg(), 'wrong-key')
    assert result['ok'] is False
    assert result['error']


def test_is_connected_and_connection_error_reflect_client_state():
    mgr = ConnectionManager(client_factory=lambda **kw: _FakeClient(**kw))
    assert mgr.is_connected('nope') is False
    assert mgr.connection_error('nope') is None
    client = mgr.get_connection(_instance_cfg())
    assert mgr.is_connected('datos-64') is False
    client.is_connected = True
    assert mgr.is_connected('datos-64') is True
    client.last_error = 'boom'
    assert mgr.connection_error('datos-64') == 'boom'


def test_test_connection_catches_unexpected_exception():
    class _Boom(_FakeClient):
        def connect(self):
            raise ValueError('totally unexpected')

    mgr = ConnectionManager(client_factory=lambda **kw: _Boom(**kw))
    result = mgr.test_connection(_instance_cfg(), 'ro-key')
    assert result['ok'] is False
    assert 'unexpected error' in result['error']


def test_close_removes_and_closes_single_client():
    mgr = ConnectionManager(client_factory=lambda **kw: _FakeClient(**kw))
    client = mgr.get_connection(_instance_cfg())
    client.is_connected = True
    mgr.close('datos-64')
    assert client.is_connected is False
    assert mgr.is_connected('datos-64') is False


def test_close_all_closes_every_client():
    clients = []

    def factory(**kwargs):
        c = _FakeClient(**kwargs)
        clients.append(c)
        return c

    mgr = ConnectionManager(client_factory=factory)
    mgr.get_connection(_instance_cfg('a'))
    mgr.get_connection(_instance_cfg('b'))
    for c in clients:
        c.is_connected = True
    mgr.close_all()
    assert all(not c.is_connected for c in clients)
