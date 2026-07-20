# -*- coding: utf-8 -*-
"""apps_vms subsystem: app.query + vm.query, both confirmed live against
the real .64 instance (25.10.1) responding [] — no virt.instance.* shim in
F1 (see module docstring for why it would be speculative here). Each
fetched independently (safe_call) — see module docstring for why vm.query
specifically is the one flagged as unstable across versions."""

from core.errors import TrueNASConnectionError
from subsystems import apps_vms
from tests.unit.fakes import FakeConn


def test_list_calls_both_app_query_and_vm_query():
    conn = FakeConn({'app.query': [{'name': 'plex'}], 'vm.query': []})
    result = apps_vms.apps_vms.list(conn)
    assert result == {'apps': [{'name': 'plex'}], 'apps_error': None,
                       'vms': [], 'vms_error': None}
    assert set(conn.methods_called()) == {'app.query', 'vm.query'}


def test_failing_vm_query_does_not_hide_working_apps():
    conn = FakeConn({
        'app.query': [{'name': 'plex'}],
        'vm.query': TrueNASConnectionError('vm.query errored on this version'),
    })
    result = apps_vms.apps_vms.list(conn)
    assert result['apps'] == [{'name': 'plex'}]
    assert result['apps_error'] is None
    assert result['vms'] == []
    assert 'vm.query errored' in result['vms_error']


def test_list_never_calls_virt_instance_namespace():
    conn = FakeConn({'app.query': [], 'vm.query': []})
    apps_vms.apps_vms.list(conn)
    assert not any(m.startswith('virt.instance') for m in conn.methods_called())


def test_handles_none_responses():
    conn = FakeConn({'app.query': None, 'vm.query': None})
    assert apps_vms.list_apps(conn) == []
    assert apps_vms.list_vms(conn) == []
