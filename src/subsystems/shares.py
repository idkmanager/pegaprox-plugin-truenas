# -*- coding: utf-8 -*-
"""Shares subsystem: SMB, NFS and iSCSI (target/extent/targetextent),
read-only in F1 — CRUD lands in F3 per the brief's phase table.

Deliberate deviation from the generic ``list(conn) -> list[dict]`` shape:
"shares" isn't one TrueNAS collection, it's five (``sharing.smb.query``,
``sharing.nfs.query``, three ``iscsi.*.query`` calls), and the UI's own
layout (brief §6) already wants them as separate SMB/NFS/iSCSI tabs, not
one flattened list an operator would have to filter client-side. ``list()``
therefore returns a dict keyed by kind — documented here rather than forced
into a shape that would just be un-flattened again by the caller.

The iSCSI write payload is explicitly unconfirmed per the brief (§4.2) —
irrelevant here since F1 only reads; ``sharing.smb.query``/
``sharing.nfs.query``/``iscsi.*.query`` take no required params for a full
listing.

Each of the five collections is fetched independently via ``safe_call`` —
a failing ``iscsi.*`` query (silent-failure-hunter finding, F1 review round
2) must not also take down SMB/NFS results that DID come back fine. Every
kind gets a ``<kind>_error`` key alongside it (``None`` on success).
"""

from core.subsystem import Subsystem, safe_call


def list_smb(conn):
    return conn.call('sharing.smb.query') or []


def list_nfs(conn):
    return conn.call('sharing.nfs.query') or []


def list_iscsi_targets(conn):
    return conn.call('iscsi.target.query') or []


def list_iscsi_extents(conn):
    return conn.call('iscsi.extent.query') or []


def list_iscsi_targetextents(conn):
    return conn.call('iscsi.targetextent.query') or []


class SharesSubsystem(Subsystem):
    SUBSYSTEM_ID = 'shares'

    def list(self, conn):
        """Returns a dict, not a flat list — see module docstring. Each of
        the five collections degrades independently (safe_call) — one
        failing kind never hides the others."""
        smb, smb_error = safe_call('sharing.smb.query', lambda: list_smb(conn), [])
        nfs, nfs_error = safe_call('sharing.nfs.query', lambda: list_nfs(conn), [])
        targets, targets_error = safe_call(
            'iscsi.target.query', lambda: list_iscsi_targets(conn), [])
        extents, extents_error = safe_call(
            'iscsi.extent.query', lambda: list_iscsi_extents(conn), [])
        targetextents, targetextents_error = safe_call(
            'iscsi.targetextent.query', lambda: list_iscsi_targetextents(conn), [])
        return {
            'smb': smb, 'smb_error': smb_error,
            'nfs': nfs, 'nfs_error': nfs_error,
            'iscsi_targets': targets, 'iscsi_targets_error': targets_error,
            'iscsi_extents': extents, 'iscsi_extents_error': extents_error,
            'iscsi_targetextents': targetextents, 'iscsi_targetextents_error': targetextents_error,
        }


shares = SharesSubsystem()
