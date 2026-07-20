# -*- coding: utf-8 -*-
"""One WebSocket connection per configured TrueNAS instance, lazy-connect,
multi-instance from day one (per brief §2/§3 — TrueCommand-style).

Mirrors the "connection manager" pattern used by other PegaProx plugins
(e.g. the Proxmox-power plugin's node manager): a thin registry keyed by
instance id, handing out (and lazily creating) one ``TrueNASWSClient`` per
instance, with ``is_connected`` / ``connection_error`` accessors so routes
can report instance health without forcing a connect.
"""

import logging
import threading

from .errors import TrueNASError
from .ws_client import TrueNASWSClient

log = logging.getLogger('plugin.truenas.conn_manager')


class ConnectionManager:
    def __init__(self, client_factory=None):
        self._client_factory = client_factory or TrueNASWSClient
        self._clients = {}          # instance_id -> TrueNASWSClient
        self._lock = threading.Lock()

    def get_connection(self, instance_cfg):
        """Return the (lazily created) client for ``instance_cfg``. Does NOT
        connect — connection happens lazily on the client's first ``call()``."""
        instance_id = instance_cfg['id']
        with self._lock:
            client = self._clients.get(instance_id)
            if client is None:
                client = self._client_factory(
                    host=instance_cfg['host'],
                    port=instance_cfg.get('port', 443),
                    use_tls=instance_cfg.get('use_tls', True),
                    verify_tls=instance_cfg.get('verify_tls', False),
                )
                self._clients[instance_id] = client
            return client

    def is_connected(self, instance_id):
        client = self._clients.get(instance_id)
        return bool(client and client.is_connected)

    def connection_error(self, instance_id):
        client = self._clients.get(instance_id)
        return client.last_error if client else None

    def test_connection(self, instance_cfg, api_key):
        """Attempt connect + login_with_api_key against ``instance_cfg`` and
        report ok/error — the ONLY real interaction with a TrueNAS instance
        allowed in F0 (routes/api.py's ``instances/test``). Makes no other
        JSON-RPC call. Never raises: always returns a result dict."""
        client = self.get_connection(instance_cfg)
        try:
            client.connect()
            client.login(api_key)
        except TrueNASError as e:
            return {'ok': False, 'error': str(e)}
        except Exception as e:  # defensive: never let a transport bug 500 the route
            log.error(f"[truenas] unexpected error testing instance "
                      f"'{instance_cfg.get('id')}': {e}", exc_info=True)
            return {'ok': False, 'error': f'unexpected error: {e}'}
        return {'ok': True, 'error': None}

    def close(self, instance_id):
        with self._lock:
            client = self._clients.pop(instance_id, None)
        if client:
            client.close()

    def close_all(self):
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()
