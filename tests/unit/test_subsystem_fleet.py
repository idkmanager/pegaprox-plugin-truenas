# -*- coding: utf-8 -*-
"""Fleet Overview (F3): fan-out over every configured instance, per-instance
isolation (one unreachable/hung instance must never take down the rest),
and honest aggregates (no invented metrics)."""

from core.errors import TrueNASConnectionError
from subsystems import fleet
from tests.unit.fakes import FakeConn


def _instance(id_='a', name=None, client_id='acme'):
    return {'id': id_, 'name': name or f'TrueNAS {id_}', 'client_id': client_id}


def _pool(name='tank', allocated=50, size=100, healthy=True):
    return {'name': name, 'allocated': allocated, 'size': size, 'healthy': healthy}


def _svc(name='cifs', enable=True, state='RUNNING'):
    return {'service': name, 'enable': enable, 'state': state}


def _alert(level='WARNING', dismissed=False):
    return {'level': level, 'dismissed': dismissed}


def _audit_row(username='alfonso', event='METHOD_CALL', ts=1000):
    return {'timestamp': {'$date': ts}, 'username': username, 'event': event,
            'service': 'MIDDLEWARE', 'success': True}


def _healthy_conn(pools=None, services=None, alerts=None, activity=None):
    return FakeConn({
        'system.info': {'version': '25.10.1', 'hostname': 'nas1'},
        'alert.list': alerts if alerts is not None else [],
        'pool.query': pools if pools is not None else [_pool()],
        'service.query': services if services is not None else [_svc()],
        'audit.query': activity if activity is not None else [_audit_row()],
    })


def test_fetch_fleet_returns_one_summary_per_instance_in_order():
    instances = [_instance('a'), _instance('b')]
    conns = {'a': _healthy_conn(), 'b': _healthy_conn()}
    results = fleet.fetch_fleet(instances, lambda inst: conns[inst['id']])
    assert [r['id'] for r in results] == ['a', 'b']
    assert all(r['reachable'] for r in results)


def test_fetch_fleet_marks_unreachable_instance_without_crashing():
    def get_conn(inst):
        if inst['id'] == 'down':
            raise TrueNASConnectionError('could not connect')
        return _healthy_conn()

    instances = [_instance('up'), _instance('down')]
    results = fleet.fetch_fleet(instances, get_conn)
    by_id = {r['id']: r for r in results}
    assert by_id['up']['reachable'] is True
    assert by_id['down']['reachable'] is False
    assert 'could not connect' in by_id['down']['connect_error']


def test_fetch_fleet_one_unreachable_instance_does_not_affect_the_other():
    """The real risk this guards: a fan-out bug that lets one instance's
    exception propagate and abort futures for the rest."""
    def get_conn(inst):
        if inst['id'] == 'down':
            raise TrueNASConnectionError('nope')
        return _healthy_conn(pools=[_pool(allocated=90, size=100)])

    instances = [_instance('down'), _instance('up')]
    results = fleet.fetch_fleet(instances, get_conn)
    by_id = {r['id']: r for r in results}
    assert by_id['up']['capacity_used'] == 90
    assert by_id['up']['capacity_size'] == 100


def test_fetch_instance_degrades_service_query_failure_without_hiding_pools():
    conn = FakeConn({
        'system.info': {'version': '25.10.1'},
        'alert.list': [],
        'pool.query': [_pool()],
        'service.query': TrueNASConnectionError('timed out'),
        'audit.query': [],
    })
    result = fleet._fetch_instance(_instance('a'), lambda inst: conn)
    assert result['reachable'] is True
    assert result['pool_count'] == 1
    assert result['down_services'] == []
    assert 'services' in result['errors']


def test_fetch_instance_flags_unhealthy_pool_and_down_service():
    conn = _healthy_conn(
        pools=[_pool(name='tank', healthy=False)],
        services=[_svc('nfs', enable=True, state='STOPPED')],
        alerts=[_alert(level='CRITICAL', dismissed=False)],
    )
    result = fleet._fetch_instance(_instance('a'), lambda inst: conn)
    assert result['unhealthy_pools'] == ['tank']
    assert result['down_services'] == ['nfs']
    assert result['critical_alert_count'] == 1


def test_fetch_instance_activity_filters_auth_and_logout_noise():
    """Live-verified 2026-07-20: an unfiltered audit.query feed is ~100%
    AUTHENTICATION/LOGOUT self-noise from the plugin's own polling. The
    filter is built into the query sent to TrueNAS, not applied after the
    fact — assert the envelope actually excludes them."""
    conn = _healthy_conn()
    fleet._fetch_instance(_instance('a'), lambda inst: conn)
    audit_calls = [c for c in conn.calls if c[0] == 'audit.query']
    assert len(audit_calls) == 1
    filters = audit_calls[0][1][0]['query-filters']
    assert ['event', '!=', 'AUTHENTICATION'] in filters
    assert ['event', '!=', 'LOGOUT'] in filters


def test_aggregate_counts_healthy_degraded_and_unreachable():
    summaries = [
        {'id': 'a', 'reachable': True, 'unhealthy_pools': [], 'critical_alert_count': 0,
         'down_services': [], 'capacity_used': 10, 'capacity_size': 100, 'alert_count': 0,
         'pool_usage': [], 'activity': []},
        {'id': 'b', 'reachable': True, 'unhealthy_pools': ['tank'], 'critical_alert_count': 0,
         'down_services': [], 'capacity_used': 90, 'capacity_size': 100, 'alert_count': 2,
         'pool_usage': [], 'activity': []},
        {'id': 'c', 'reachable': False},
    ]
    agg = fleet.aggregate(summaries)
    assert agg['instance_count'] == 3
    assert agg['healthy_count'] == 1
    assert agg['degraded_count'] == 1
    assert agg['unreachable_count'] == 1
    assert agg['capacity_used'] == 100
    assert agg['capacity_size'] == 200
    assert agg['capacity_pct'] == 50.0
    assert agg['total_alerts'] == 2


def test_aggregate_never_divides_by_zero_when_no_pools_anywhere():
    summaries = [{'id': 'a', 'reachable': True, 'unhealthy_pools': [], 'critical_alert_count': 0,
                  'down_services': [], 'capacity_used': 0, 'capacity_size': 0, 'alert_count': 0,
                  'pool_usage': [], 'activity': []}]
    agg = fleet.aggregate(summaries)
    assert agg['capacity_pct'] is None


def test_aggregate_top_pools_sorted_by_usage_percent_across_instances():
    summaries = [
        {'id': 'a', 'reachable': True, 'unhealthy_pools': [], 'critical_alert_count': 0,
         'down_services': [], 'capacity_used': 0, 'capacity_size': 0, 'alert_count': 0,
         'activity': [],
         'pool_usage': [{'instance_id': 'a', 'instance_name': 'A', 'pool': 'low', 'used': 10,
                          'size': 100, 'pct': 10.0}]},
        {'id': 'b', 'reachable': True, 'unhealthy_pools': [], 'critical_alert_count': 0,
         'down_services': [], 'capacity_used': 0, 'capacity_size': 0, 'alert_count': 0,
         'activity': [],
         'pool_usage': [{'instance_id': 'b', 'instance_name': 'B', 'pool': 'high', 'used': 95,
                          'size': 100, 'pct': 95.0}]},
    ]
    agg = fleet.aggregate(summaries)
    assert agg['top_pools'][0]['pool'] == 'high'


def test_aggregate_activity_merged_and_sorted_most_recent_first():
    summaries = [
        {'id': 'a', 'reachable': True, 'unhealthy_pools': [], 'critical_alert_count': 0,
         'down_services': [], 'capacity_used': 0, 'capacity_size': 0, 'alert_count': 0,
         'pool_usage': [], 'name': 'A', 'activity': [_audit_row(ts=100)]},
        {'id': 'b', 'reachable': True, 'unhealthy_pools': [], 'critical_alert_count': 0,
         'down_services': [], 'capacity_used': 0, 'capacity_size': 0, 'alert_count': 0,
         'pool_usage': [], 'name': 'B', 'activity': [_audit_row(ts=200)]},
    ]
    agg = fleet.aggregate(summaries)
    assert agg['activity'][0]['instance_id'] == 'b'
    assert agg['activity'][1]['instance_id'] == 'a'
