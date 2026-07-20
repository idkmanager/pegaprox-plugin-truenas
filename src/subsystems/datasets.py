# -*- coding: utf-8 -*-
"""Datasets/zvols subsystem: ``pool.dataset.query`` + ``pool.dataset.get_quota``.

Read-only in F1 — create/update/delete/resize are F2, gated behind the
dry-run/confirm/audit write-path (brief §5) once a dedicated test dataset
on a healthy pool is designated for it.

``list_datasets()``/``DatasetsSubsystem.list()`` is what F1's Datasets tab
actually calls — ``quota()`` below is a standalone, NOT YET WIRED helper for
a future per-dataset quota display: no route or UI code calls it yet, only
this module's own tests do. It's kept here (rather than deferred entirely
to F2) because ``pool.dataset.get_quota``'s per-dataset params
(``dataset_id``, quota type) needed *some* defensive shape decided now, so a
future caller building a "quota per dataset" sweep doesn't have to
rediscover the failure-isolation requirement. When that sweep IS built
(F1.5/F2), call ``quota()`` once per dataset and keep the same contract:
a bad dataset id degrades to ``[]`` for THAT dataset only, never aborts
the rest of the sweep — mirroring the ``safe_call`` isolation pattern used
by ``shares``/``apps_vms``/the pools and system routes.
"""

import logging

from core.errors import TrueNASError
from core.subsystem import Subsystem

log = logging.getLogger('plugin.truenas.subsystems.datasets')

DEFAULT_QUOTA_TYPE = 'USER'


def list_datasets(conn):
    """``pool.dataset.query`` — every dataset/zvol, attrs passthrough."""
    return conn.call('pool.dataset.query') or []


def quota(conn, dataset_id, quota_type=DEFAULT_QUOTA_TYPE):
    """``pool.dataset.get_quota`` for one dataset. Returns ``[]`` (not
    raises) on any TrueNAS-side error — read-only monitoring must degrade
    per-dataset, not all-or-nothing. Logs the failure (dataset id + cause)
    so a bad id or a real appliance error leaves a trace once this is
    wired to a caller, instead of vanishing silently."""
    try:
        return conn.call('pool.dataset.get_quota', [dataset_id, quota_type]) or []
    except TrueNASError as e:
        log.warning(f"[truenas] quota lookup failed for dataset {dataset_id!r} "
                    f"(quota_type={quota_type!r}): {e}")
        return []


class DatasetsSubsystem(Subsystem):
    SUBSYSTEM_ID = 'datasets'

    def list(self, conn):
        return list_datasets(conn)

    def read(self, conn, id):
        for ds in list_datasets(conn):
            if ds.get('id') == id or ds.get('name') == id:
                return ds
        return None


datasets = DatasetsSubsystem()
