# -*- coding: utf-8 -*-
"""Services subsystem: ``service.query`` — SMB/NFS/iSCSI/SSH/etc. running
state. Read-only (F4a).

F4b (start/stop/restart) is deliberately NOT implemented yet: verified live
2026-07-20 against a real TrueNAS-25.10.1 instance that ``service.start``/
``stop``/``restart``/``update`` are invisible to ``core.get_methods`` under
BOTH the plugin's current RO key (``SERVICE_READ`` only — exposes
``service.query``/``get_instance``/``started``/``started_or_enabled``) and
its RW key (granular ``DATASET_*``/``SNAPSHOT_*`` roles only, no
``SERVICE_*`` at all). The real TrueNAS role that gates the write methods is
the builtin ``SERVICE_WRITE`` role (confirmed via ``privilege.roles``) —
granting it to the RW key is a deliberate TrueNAS-side privilege change, not
a plugin code change, so it is left for the operator to decide rather than
silently widened here.
"""

from core.subsystem import HealthReport, Subsystem


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
