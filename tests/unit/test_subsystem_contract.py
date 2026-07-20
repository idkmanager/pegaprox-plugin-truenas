# -*- coding: utf-8 -*-
"""core.subsystem: the base Subsystem contract's defaults (every concrete
module overrides list/read/health, but the base class's own NotImplementedError
stubs and the read-only write() default need direct coverage too)."""

import pytest

from core.errors import TrueNASConnectionError, TrueNASTimeoutError
from core.subsystem import HealthReport, ReadOnlySubsystem, Subsystem, safe_call


def test_health_report_to_dict():
    report = HealthReport(healthy=True, summary='all good', details={'n': 1})
    assert report.to_dict() == {'healthy': True, 'summary': 'all good', 'details': {'n': 1}}


def test_health_report_details_defaults_to_empty_dict():
    report = HealthReport(healthy=False, summary='bad')
    assert report.details == {}


def test_base_subsystem_list_raises_not_implemented():
    sub = Subsystem()
    with pytest.raises(NotImplementedError):
        sub.list(conn=None)


def test_base_subsystem_read_raises_not_implemented():
    sub = Subsystem()
    with pytest.raises(NotImplementedError):
        sub.read(conn=None, id='x')


def test_base_subsystem_health_raises_not_implemented():
    sub = Subsystem()
    with pytest.raises(NotImplementedError):
        sub.health(conn=None)


def test_base_subsystem_write_raises_read_only_subsystem():
    sub = Subsystem()
    sub.SUBSYSTEM_ID = 'example'
    with pytest.raises(ReadOnlySubsystem) as exc_info:
        sub.write(conn=None, op='create', payload={})
    assert 'example' in str(exc_info.value)


def test_concrete_subsystems_inherit_read_only_write():
    """Every real F1 subsystem must still refuse writes via the shared
    default — none of them override write() (that's F2+)."""
    from subsystems.apps_vms import apps_vms
    from subsystems.datasets import datasets
    from subsystems.pools import pools
    from subsystems.replication import replication
    from subsystems.shares import shares
    from subsystems.snapshots import snapshots
    from subsystems.system import system

    for sub in (system, pools, datasets, snapshots, shares, replication, apps_vms):
        with pytest.raises(ReadOnlySubsystem):
            sub.write(conn=None, op='create', payload={})


# ---------------------------------------------------------------------------
# safe_call — the shared failure-isolation helper (silent-failure-hunter
# finding, F1 review round 2): one sub-call failing must never sink an
# entire multi-call subsystem response.
# ---------------------------------------------------------------------------

def test_safe_call_returns_value_and_no_error_on_success():
    value, error = safe_call('some.method', lambda: {'ok': True}, default={})
    assert value == {'ok': True}
    assert error is None


def test_safe_call_degrades_to_default_on_truenas_error():
    def boom():
        raise TrueNASConnectionError('appliance unreachable')

    value, error = safe_call('disk.temperature_agg', boom, default={})
    assert value == {}
    assert 'appliance unreachable' in error


def test_safe_call_degrades_on_timeout_too():
    def boom():
        raise TrueNASTimeoutError('timed out waiting for disk.temperature_agg')

    value, error = safe_call('disk.temperature_agg', boom, default={})
    assert value == {}
    assert error is not None


def test_safe_call_does_not_swallow_non_truenas_exceptions():
    """A programming bug (e.g. AttributeError from a subsystem module) must
    still surface loudly — safe_call only isolates TrueNAS-side failures,
    not bugs in this codebase."""
    def boom():
        raise ValueError('this is a real bug, not a TrueNAS failure')

    with pytest.raises(ValueError):
        safe_call('some.method', boom, default={})
