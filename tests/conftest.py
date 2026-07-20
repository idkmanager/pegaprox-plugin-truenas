# -*- coding: utf-8 -*-
"""Stub the PegaProx host modules (+ flask) so the plugin imports standalone
in CI, mirroring pegaprox-plugin-wake-on-lan's conftest.py pattern.

The plugin only needs a handful of names from ``pegaprox.*`` at import time
(register_plugin_route, auth, rbac, audit, permissions) plus ``flask``. We
register lightweight fakes in ``sys.modules`` *before* the plugin is
imported, then load ``__init__.py`` as the module ``truenas_plugin`` for the
tests to use. ``core`` and ``routes`` are imported directly from ``src/``
via the same sys.path injection the plugin itself performs.
"""

import os
import sys
import types
import importlib.util

import pytest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PLUGIN_ROOT, 'src')


def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


def _install_fakes():
    if 'flask' not in sys.modules:
        flask = _mod('flask')

        def jsonify(obj=None, **kw):
            return ('JSON', obj if obj is not None else kw)

        def send_file(path, mimetype=None):
            return ('FILE', path, mimetype)

        flask.jsonify = jsonify
        flask.send_file = send_file
        flask.request = types.SimpleNamespace(
            args={}, method='GET', session={'user': 'tester'},
            get_json=lambda silent=False: {},
        )

    _mod('pegaprox')
    api = _mod('pegaprox.api')
    sys.modules['pegaprox'].api = api
    plugins = _mod('pegaprox.api.plugins')
    plugins.register_plugin_route = lambda *a, **k: None
    utils = _mod('pegaprox.utils')
    sys.modules['pegaprox'].utils = utils
    auth = _mod('pegaprox.utils.auth')
    auth.load_users = lambda: {'tester': {'role': 'admin'}}
    rbac = _mod('pegaprox.utils.rbac')
    rbac.has_permission = lambda user, perm, tenant_id=None: True
    audit = _mod('pegaprox.utils.audit')
    audit.log_audit = lambda **k: None
    models = _mod('pegaprox.models')
    sys.modules['pegaprox'].models = models
    permissions = _mod('pegaprox.models.permissions')
    permissions.ROLE_ADMIN = 'admin'


_install_fakes()
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture(scope='session')
def plugin():
    _install_fakes()
    spec = importlib.util.spec_from_file_location(
        'truenas_plugin', os.path.join(PLUGIN_ROOT, '__init__.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['truenas_plugin'] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tmp_plugin_dir(plugin, tmp_path, monkeypatch):
    """Point the routes.api module's CONFIG_PATH at a scratch dir so config
    tests never touch a real plugin directory. Mirrors wake-on-lan's
    tmp_plugin_dir fixture but re-runs ``init()`` instead of poking module
    globals directly, exercising the real wiring path."""
    from routes import api as routes_api
    routes_api.init(str(tmp_path))
    routes_api.conn_manager.close_all()
    return tmp_path
