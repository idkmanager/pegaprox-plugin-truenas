# -*- coding: utf-8 -*-
"""Subsystem collectors/writers (pools, datasets, snapshots, shares,
replication, apps_vms, system) — the ``Subsystem`` contract from the brief
(§2).

INTENTIONALLY EMPTY in F0. Per the brief's exact F0 scope, this plugin ships
only the transport (``core/ws_client.py``, ``core/conn_manager.py``), config
and UI shell in this phase — no subsystem is implemented yet. F1 adds the
read-only collectors (system/pools/datasets/snapshots/shares/replication/
apps_vms) against this contract; see PEGAPROX_PLUGIN_TRUENAS_BRIEF.md §1/§2.
"""
