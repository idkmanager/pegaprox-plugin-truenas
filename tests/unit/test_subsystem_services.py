# -*- coding: utf-8 -*-
"""services subsystem: service.query (F4a, read-only) and
service.start/stop/restart (F4b, write) — see services.py's module
docstring for the SERVICE_WRITE privilege story."""

import pytest

from subsystems import services
from tests.unit.fakes import FakeConn


def _svc(name='cifs', enable=True, state='RUNNING'):
    return {'id': 1, 'service': name, 'enable': enable, 'state': state, 'pids': [123]}


def test_list_services_calls_service_query():
    conn = FakeConn({'service.query': [_svc()]})
    result = services.list_services(conn)
    assert result == [_svc()]
    assert conn.methods_called() == ['service.query']


def test_list_services_returns_list_even_when_middleware_returns_none():
    conn = FakeConn({'service.query': None})
    assert services.list_services(conn) == []


def test_is_service_unhealthy_true_when_enabled_but_not_running():
    assert services.is_service_unhealthy(_svc(enable=True, state='STOPPED')) is True


def test_is_service_unhealthy_false_when_disabled_and_stopped():
    assert services.is_service_unhealthy(_svc(enable=False, state='STOPPED')) is False


def test_is_service_unhealthy_false_when_enabled_and_running():
    assert services.is_service_unhealthy(_svc(enable=True, state='RUNNING')) is False


def test_health_healthy_when_all_enabled_services_running():
    conn = FakeConn({'service.query': [_svc('cifs'), _svc('nfs')]})
    report = services.services.health(conn)
    assert report.healthy is True


def test_health_unhealthy_when_an_enabled_service_is_down():
    conn = FakeConn({'service.query': [
        _svc('cifs', enable=True, state='RUNNING'),
        _svc('nfs', enable=True, state='STOPPED'),
    ]})
    report = services.services.health(conn)
    assert report.healthy is False
    assert 'nfs' in report.summary
    assert report.details['down_enabled_services'] == ['nfs']


def test_health_accepts_prefetched_services_without_a_second_call():
    conn = FakeConn({})  # no canned service.query -> would raise if called again
    report = services.services.health(conn, services=[_svc(enable=False, state='STOPPED')])
    assert report.healthy is True
    assert conn.methods_called() == []


def test_read_finds_service_by_name():
    conn = FakeConn({'service.query': [_svc('ssh'), _svc('cifs')]})
    found = services.services.read(conn, 'cifs')
    assert found['service'] == 'cifs'


def test_read_returns_none_for_unknown_service():
    conn = FakeConn({'service.query': [_svc('ssh')]})
    assert services.services.read(conn, 'nonexistent') is None


def test_list_returns_same_as_list_services():
    conn = FakeConn({'service.query': [_svc()]})
    assert services.services.list(conn) == [_svc()]


# ---------------------------------------------------------------------------
# F4b: start/stop/restart write path — same build/execute pattern as
# datasets/snapshots (brief §5).
# ---------------------------------------------------------------------------

def test_build_control_envelope_start():
    method, params = services.build_control_envelope('start', 'cifs')
    assert method == 'service.start'
    assert params == ['cifs', {'silent': False}]


def test_build_control_envelope_stop():
    method, params = services.build_control_envelope('stop', 'nfs')
    assert method == 'service.stop'
    assert params == ['nfs', {'silent': False}]


def test_build_control_envelope_restart():
    method, params = services.build_control_envelope('restart', 'ssh')
    assert method == 'service.restart'
    assert params == ['ssh', {'silent': False}]


def test_build_control_envelope_rejects_unknown_op():
    with pytest.raises(ValueError):
        services.build_control_envelope('frobnicate', 'cifs')


def test_build_control_envelope_rejects_empty_service_name():
    with pytest.raises(ValueError):
        services.build_control_envelope('start', '')


def test_control_calls_the_exact_envelope_the_builder_produced():
    conn = FakeConn({'service.start': True})
    result = services.control(conn, 'start', 'cifs')
    assert result is True
    assert conn.calls == [('service.start', ['cifs', {'silent': False}])]


def test_control_uses_write_timeout_not_the_read_default():
    from core.ws_client import WRITE_TIMEOUT
    conn = FakeConn({'service.restart': True})
    services.control(conn, 'restart', 'cifs')
    assert conn.timeouts == [WRITE_TIMEOUT]
