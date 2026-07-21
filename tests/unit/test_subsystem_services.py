# -*- coding: utf-8 -*-
"""services subsystem: service.query, read-only (F4a). F4b (start/stop/
restart) is NOT implemented yet — see services.py's module docstring for
why (SERVICE_WRITE not yet granted to the RW key, verified live)."""

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
