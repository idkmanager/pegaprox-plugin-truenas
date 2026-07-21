# -*- coding: utf-8 -*-
"""Services subsystem: ``service.query`` (F4a, read-only) and
``service.start``/``stop``/``restart`` (F4b, write — brief §5 dry-run/
confirm/verify/audit pattern, same shape as datasets/snapshots).

F4b was blocked, then unblocked, by a real TrueNAS-side privilege gap
(NOT a plugin bug): verified live 2026-07-20 that ``service.start``/
``stop``/``restart``/``update`` were invisible to ``core.get_methods``
under both the RO key (``SERVICE_READ`` only) and the RW key (granular
``DATASET_*``/``SNAPSHOT_*`` roles, no ``SERVICE_*`` at all). The gating
role is the builtin ``SERVICE_WRITE`` — the operator granted it to the RW
key's privilege object (``privilege.update(id, {'roles': [...]})``, id=5
"PegaProx RW" on `.64`) after confirming no admin credential for widening
it existed anywhere else; re-checked live that ``service.start``/``stop``/
``restart`` are now visible to the RW key before this was implemented.

Each op is passed ``{'silent': False}`` explicitly — TrueNAS's own default
for these methods is ``silent: True`` (return ``false`` instead of raising
on failure), which would silently swallow a failed start/stop/restart as
an ordinary falsy result instead of a ``TrueNASRPCError`` the write path's
existing error/audit handling already knows how to report.
"""

from core.subsystem import HealthReport, Subsystem
from core.ws_client import WRITE_TIMEOUT

_CONTROL_OPS = ('start', 'stop', 'restart')


def list_services(conn):
    """``service.query`` — every service TrueNAS manages (id, service,
    enable, state, pids). A real .64 instance (2026-07-20) returned 8:
    cifs, ftp, iscsitarget, nfs, nvmet, snmp, ssh, ups."""
    return conn.call('service.query') or []


def is_service_unhealthy(svc):
    """A service the operator marked to auto-start (``enable: true``) but
    that isn't actually ``RUNNING`` is the interesting failure — a crashed
    or manually-stopped critical service (SMB/NFS/iSCSI) an operator would
    otherwise only discover from a client complaining. A service correctly
    disabled+stopped is not a problem."""
    return bool(svc.get('enable')) and str(svc.get('state', '')).upper() != 'RUNNING'


class ServicesSubsystem(Subsystem):
    SUBSYSTEM_ID = 'services'

    def list(self, conn):
        return list_services(conn)

    def read(self, conn, id):
        for svc in list_services(conn):
            if svc.get('service') == id or str(svc.get('id')) == str(id):
                return svc
        return None

    def health(self, conn, services=None):
        services = services if services is not None else list_services(conn)
        down = [s.get('service') for s in services if is_service_unhealthy(s)]
        healthy = not down
        summary = ('all enabled services running' if healthy else
                    f"{len(down)} enabled service(s) not running: {', '.join(down)}")
        return HealthReport(healthy=healthy, summary=summary, details={
            'service_count': len(services),
            'down_enabled_services': down,
        })


services = ServicesSubsystem()


def build_control_envelope(op, service_name):
    """Pure builder (no ``conn``) — the same function both ``writes/
    dry-run`` and ``writes/execute`` call first, so the two paths can
    never describe different JSON-RPC calls (brief §5)."""
    if op not in _CONTROL_OPS:
        raise ValueError(f"unknown service op '{op}' (expected one of {_CONTROL_OPS})")
    service_name = str(service_name or '').strip()
    if not service_name:
        raise ValueError('service name is required')
    return f'service.{op}', [service_name, {'silent': False}]


def control(conn, op, service_name):
    """Runs ``build_control_envelope`` first (identical validation to the
    dry-run path) then issues the real call with ``WRITE_TIMEOUT`` — a
    service restart can legitimately take longer than a 10s read default,
    same reasoning as datasets/snapshots' writes."""
    method, params = build_control_envelope(op, service_name)
    return conn.call(method, params, timeout=WRITE_TIMEOUT)
