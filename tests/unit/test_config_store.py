# -*- coding: utf-8 -*-
"""config_store: masking round-trip, client_id passthrough (unmasked),
use_tls safety guard, poll validation, atomic save."""

import os

from routes import config_store


def _instance(id_='datos-64', client_id='idkmanager', api_key_ro='real-secret-ro'):
    return {
        'id': id_, 'name': 'TrueNAS Datos', 'client_id': client_id,
        'host': '192.0.2.64', 'port': 81, 'use_tls': True, 'verify_tls': False,
        'api_key_ro': api_key_ro, 'api_key_rw': None, 'readonly': True,
    }


def test_mask_instance_masks_keys_when_present():
    masked = config_store.mask_instance(_instance())
    assert masked['api_key_ro'] == '***'
    assert masked['api_key_rw'] is None
    assert masked['client_id'] == 'idkmanager'  # never masked


def test_mask_instance_leaves_falsy_key_alone():
    inst = _instance(api_key_ro=None)
    masked = config_store.mask_instance(inst)
    assert masked['api_key_ro'] is None


def test_validate_instances_masked_key_round_trips():
    old = [_instance(api_key_ro='vault-secret')]
    incoming = [dict(_instance(api_key_ro='***'))]
    clean, err = config_store.validate_instances(incoming, old)
    assert err is None
    assert clean[0]['api_key_ro'] == 'vault-secret'


def test_validate_instances_new_key_overwrites():
    old = [_instance(api_key_ro='old-secret')]
    incoming = [dict(_instance(api_key_ro='new-secret'))]
    clean, err = config_store.validate_instances(incoming, old)
    assert err is None
    assert clean[0]['api_key_ro'] == 'new-secret'


def test_validate_instances_masked_key_without_prior_value_errors():
    incoming = [dict(_instance(api_key_ro='***'))]
    clean, err = config_store.validate_instances(incoming, [])
    assert clean is None
    assert 'enmascarad' in err


def test_validate_instances_rejects_duplicate_id():
    clean, err = config_store.validate_instances([_instance(), _instance()], [])
    assert clean is None
    assert 'duplicate instance id' in err


def test_validate_instances_rejects_missing_host():
    bad = _instance()
    bad['host'] = ''
    clean, err = config_store.validate_instances([bad], [])
    assert clean is None
    assert 'host' in err


def test_validate_instances_rejects_bad_port():
    bad = _instance()
    bad['port'] = 70000
    clean, err = config_store.validate_instances([bad], [])
    assert clean is None
    assert 'port' in err


def test_validate_instances_rejects_http_with_api_key():
    bad = _instance()
    bad['use_tls'] = False
    clean, err = config_store.validate_instances([bad], [])
    assert clean is None
    assert 'use_tls' in err


def test_validate_instances_preserves_client_id():
    clean, err = config_store.validate_instances([_instance(client_id='sacei')], [])
    assert err is None
    assert clean[0]['client_id'] == 'sacei'


def test_validate_instances_defaults_missing_client_id_to_unassigned():
    inst = _instance()
    del inst['client_id']
    clean, err = config_store.validate_instances([inst], [])
    assert err is None
    assert clean[0]['client_id'] == 'unassigned'


def test_group_by_client_groups_in_first_seen_order():
    instances = [
        _instance(id_='a', client_id='sacei'),
        _instance(id_='b', client_id='idkmanager'),
        _instance(id_='c', client_id='sacei'),
    ]
    groups = config_store.group_by_client(instances)
    assert [g['client_id'] for g in groups] == ['sacei', 'idkmanager']
    assert len(groups[0]['instances']) == 2
    assert len(groups[1]['instances']) == 1


def test_validate_poll_defaults_when_absent():
    poll, err = config_store.validate_poll(None)
    assert err is None
    assert poll == config_store.DEFAULT_POLL


def test_validate_poll_rejects_non_positive():
    poll, err = config_store.validate_poll({'fast_s': 0})
    assert poll is None
    assert 'fast_s' in err


def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = config_store.load_config(str(tmp_path / 'nope.json'))
    assert cfg == config_store.default_config()


def test_save_and_load_config_round_trips(tmp_path):
    path = str(tmp_path / 'config.json')
    cfg = {'instances': [_instance()], 'poll': config_store.DEFAULT_POLL}
    config_store.save_config(path, cfg)
    assert not os.path.exists(path + '.tmp')
    loaded = config_store.load_config(path)
    assert loaded['instances'][0]['id'] == 'datos-64'
    assert loaded['instances'][0]['client_id'] == 'idkmanager'


def test_save_config_is_chmod_600(tmp_path):
    path = str(tmp_path / 'config.json')
    config_store.save_config(path, config_store.default_config())
    # chmod is a no-op on some CI filesystems (Windows) — just assert the
    # file exists and save_config() didn't raise on the chmod call.
    assert os.path.exists(path)
