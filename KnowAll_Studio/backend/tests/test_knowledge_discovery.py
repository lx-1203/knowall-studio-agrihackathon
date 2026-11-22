"""Unified knowledge discovery is explicit and limited to owned selected sources."""
from tests.test_knowledge_network import network, extract, point, PREFIX


async def test_discovery_without_intent_is_empty_and_does_not_seed(network):
    await extract(network)
    for scope in ['personal', 'public']:
        result = await network.client.get(PREFIX+'/discover', params={'scope': scope, 'q': '  '})
        assert result.status_code == 200
        assert result.json() == {'items': [], 'total': 0}


async def test_document_options_belong_to_current_user(network):
    result = await network.client.get(PREFIX+'/documents')
    assert result.status_code == 200
    assert {row['id'] for row in result.json()['items']} == {'doc', 'other'}
    network.account['id'] = 'bob'
    assert {row['id'] for row in (await network.client.get(PREFIX+'/documents')).json()['items']} == {'bobdoc'}


async def test_document_match_excludes_other_owned_documents(network):
    await extract(network)
    network.generated['points'] = [dict(point('other-1'), name='极限', definition='函数极限是逼近值。')]
    await extract(network, 'other')
    result = await network.client.get(PREFIX+'/discover', params={'scope': 'personal', 'document_id': 'doc'})
    assert result.status_code == 200
    assert [item['name'] for item in result.json()['items']] == ['导数']
    result = await network.client.get(PREFIX+'/discover', params={'scope': 'personal', 'document_id': 'other', 'q': '导数'})
    assert result.json()['items'] == []


async def test_foreign_and_legacy_document_ids_are_rejected(network):
    for identity in ['bobdoc', 'legacy', 'missing']:
        result = await network.client.get(PREFIX+'/discover', params={'scope': 'personal', 'document_id': identity})
        assert result.status_code == 404


async def test_public_discovery_only_returns_keyword_matches(network):
    await extract(network)
    concept = (await network.client.get(PREFIX+'/concepts', params={'scope': 'personal'})).json()['items'][0]
    await network.client.post(PREFIX+'/concepts/'+concept['id']+'/publish', json={'reviewed': True})
    network.account['id'] = 'bob'
    result = await network.client.get(PREFIX+'/discover', params={'scope': 'public', 'q': '  微商  '})
    assert [item['name'] for item in result.json()['items']] == ['导数']
    assert (await network.client.get(PREFIX+'/discover', params={'scope': 'public', 'q': 'does-not-match'})).json()['items'] == []
