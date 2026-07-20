# -*- coding: utf-8 -*-
"""Route handlers: config GET/save (masking + grouping) and instances/test,
against the stubbed flask.request from conftest.py."""


from routes import api as routes_api
from routes import config_store


def _instance(id_='datos-64', client_id='idkmanager'):
    return {
        'id': id_, 'name': 'TrueNAS Datos', 'client_id': client_id,
        'host': '192.0.2.64', 'port': 81, 'use_tls': True, 'verify_tls': False,
        'api_key_ro': 'real-secret-ro', 'api_key_rw': None, 'readonly': True,
    }


def test_config_handler_masks_keys_and_groups_by_client(plugin, tmp_plugin_dir, monkeypatch):
    config_store.save_config(routes_api.CONFIG_PATH,
                              {'instances': [_instance()], 'poll': config_store.DEFAULT_POLL})
    resp = routes_api.config_handler()
    _, payload = resp
    assert payload['instances'][0]['api_key_ro'] == '***'
    assert payload['instances_by_client'][0]['client_id'] == 'idkmanager'


def test_config_save_handler_round_trips_masked_key(plugin, tmp_plugin_dir, monkeypatch):
    config_store.save_config(routes_api.CONFIG_PATH,
                              {'instances': [_instance()], 'poll': config_store.DEFAULT_POLL})
    incoming = dict(_instance())
    incoming['api_key_ro'] = '***'
    monkeypatch.setattr(routes_api.request, 'get_json',
                         lambda silent=False: {'instances': [incoming], 'poll': {}})
    routes_api.config_save_handler()
    saved = config_store.load_config(routes_api.CONFIG_PATH)
    assert saved['instances'][0]['api_key_ro'] == 'real-secret-ro'


def test_config_save_handler_rejects_invalid_instance(plugin, tmp_plugin_dir, monkeypatch):
    bad = _instance()
    bad['host'] = ''
    monkeypatch.setattr(routes_api.request, 'get_json',
                         lambda silent=False: {'instances': [bad], 'poll': {}})
    resp, status = routes_api.config_save_handler()
    assert status == 400


def test_instances_test_handler_requires_host_and_key(plugin, tmp_plugin_dir, monkeypatch):
    monkeypatch.setattr(routes_api.request, 'get_json',
                         lambda silent=False: {'id': '', 'host': '', 'port': 443})
    resp, status = routes_api.instances_test_handler()
    assert status == 400


def test_instances_test_handler_uses_stored_key_when_masked(plugin, tmp_plugin_dir, monkeypatch):
    config_store.save_config(routes_api.CONFIG_PATH,
                              {'instances': [_instance()], 'poll': config_store.DEFAULT_POLL})

    captured = {}

    def fake_test_connection(instance_cfg, api_key):
        captured['instance_cfg'] = instance_cfg
        captured['api_key'] = api_key
        return {'ok': True, 'error': None}

    monkeypatch.setattr(routes_api.conn_manager, 'test_connection', fake_test_connection)
    monkeypatch.setattr(routes_api.request, 'get_json', lambda silent=False: {
        'id': 'datos-64', 'host': '192.0.2.64', 'port': 81,
        'use_tls': True, 'verify_tls': False, 'api_key_ro': '***',
    })
    _, payload = routes_api.instances_test_handler()
    assert payload['ok'] is True
    assert captured['api_key'] == 'real-secret-ro'


def test_instances_test_handler_never_persists_config(plugin, tmp_plugin_dir, monkeypatch):
    before = config_store.load_config(routes_api.CONFIG_PATH)
    monkeypatch.setattr(routes_api.conn_manager, 'test_connection',
                         lambda instance_cfg, api_key: {'ok': True, 'error': None})
    monkeypatch.setattr(routes_api.request, 'get_json', lambda silent=False: {
        'id': 'draft', 'host': '1.2.3.4', 'port': 443, 'api_key_ro': 'draft-key',
    })
    routes_api.instances_test_handler()
    after = config_store.load_config(routes_api.CONFIG_PATH)
    assert before == after
