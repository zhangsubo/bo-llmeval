"""Tests never access the user's real 1Password account."""
import json
import uuid
import pytest
from scripts import secret_store


@pytest.fixture(autouse=True)
def fake_onepassword(monkeypatch):
    items = {}
    monkeypatch.setenv('OP_ACCOUNT', 'test-account')
    monkeypatch.setenv('BO_LLMEVAL_OP_VAULT', 'test-vault')
    def run(args, settings, payload=None):
        if args[:2] == ['item', 'create']:
            item_id = uuid.uuid4().hex
            ref = f'op://test-vault/{item_id}/credential'
            items[ref] = payload['fields'][0]['value']
            return json.dumps({'id': item_id, 'vault': {'id': 'test-vault'}})
        if args[0] == 'read':
            if args[1] not in items:
                raise ValueError('测试条目不存在')
            return items[args[1]]
        if args[:2] == ['item', 'delete']:
            items.pop(f'op://test-vault/{args[2]}/credential', None)
            return ''
        raise AssertionError('Unexpected op command')
    monkeypatch.setattr(secret_store, '_run', run)
    return items
