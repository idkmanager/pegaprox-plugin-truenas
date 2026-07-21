# -*- coding: utf-8 -*-
"""``parallel_safe_calls`` — perf finding 2026-07-21: every multi-collection
subsystem (shares, apps_vms, data_protection, telemetry) paid N sequential
WebSocket round-trips for N independent reads. This collapses that to the
slowest single call, without weakening ``safe_call``'s per-spec isolation."""

import time

import pytest

from core.errors import TrueNASConnectionError
from core.subsystem import parallel_safe_calls


def test_runs_specs_concurrently_not_sequentially():
    delay = 0.15

    def slow(tag):
        time.sleep(delay)
        return tag

    start = time.monotonic()
    results = parallel_safe_calls([
        ('a', lambda: slow('a'), None),
        ('b', lambda: slow('b'), None),
        ('c', lambda: slow('c'), None),
    ])
    elapsed = time.monotonic() - start

    assert results == [('a', None), ('b', None), ('c', None)]
    # Sequential would take ~3*delay; concurrent should stay well under 2*delay
    # even with scheduling jitter on a loaded CI box.
    assert elapsed < delay * 2


def test_preserves_spec_order_regardless_of_completion_order():
    def fast():
        return 'fast'

    def slow():
        time.sleep(0.05)
        return 'slow'

    # 'slow' is submitted FIRST but finishes LAST — result order must still
    # match the order specs were passed in, not completion order.
    results = parallel_safe_calls([
        ('slow', slow, None),
        ('fast', fast, None),
    ])
    assert results == [('slow', None), ('fast', None)]


def test_one_failing_spec_does_not_affect_the_others():
    def boom():
        raise TrueNASConnectionError('appliance unreachable')

    def ok():
        return 'fine'

    results = parallel_safe_calls([
        ('ok1', ok, None),
        ('boom', boom, 'DEGRADED'),
        ('ok2', ok, None),
    ])
    assert results[0] == ('fine', None)
    assert results[1][0] == 'DEGRADED'
    assert 'appliance unreachable' in results[1][1]
    assert results[2] == ('fine', None)


def test_non_truenas_exception_propagates_same_as_sequential_safe_call():
    """``safe_call`` only catches ``TrueNASError`` — a programming bug
    (e.g. a ``TypeError`` from a subsystem's own code) is NOT swallowed,
    it raises straight out. The parallel path must have the same parity:
    a bug in one spec must surface loudly, not vanish into a degraded
    default alongside its siblings' results."""
    def buggy():
        raise TypeError('not a TrueNASError — a real bug')

    def ok():
        return 'fine'

    with pytest.raises(TypeError, match='not a TrueNASError'):
        parallel_safe_calls([
            ('ok', ok, None),
            ('buggy', buggy, None),
        ])


def test_empty_specs_returns_empty_list():
    assert parallel_safe_calls([]) == []
