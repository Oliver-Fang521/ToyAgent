import pytest
from gaia_agent.interactive.store import ConversationStore, ServiceError
from gaia_agent.interactive.models import InteractiveRequest


def complete(store, owner, cid, question, answer, key, attachment_id=None):
    request = InteractiveRequest(provider='openai', model='gpt-6.1-sol', question=question, attachment_id=attachment_id)
    run = store.reserve(owner, cid, request, key)
    store.update(run['id'], status='completed', result={'answer': answer, 'evidence': [], 'usage': {}})
    return run


def test_search_finds_old_turn_and_attachment_with_account_scope(tmp_path):
    store = ConversationStore(tmp_path / 'db.sqlite')
    cid = store.create('alice', 'openai')['id']
    file = tmp_path / 'homework.pdf'
    file.write_bytes(b'%PDF-1.4')
    aid = store.upload('alice', cid, file, '线性代数习题.pdf')
    complete(store, 'alice', cid, '讲解线性代数的矩阵运算', '我们计算矩阵的特征值', 'old', aid)
    for i in range(6):
        complete(store, 'alice', cid, f'无关问题{i}', f'无关答案{i}', str(i))
    items = store.search_conversation('alice', cid, '矩阵特征值')
    assert items[0]['question'] == '讲解线性代数的矩阵运算'
    assert items[0]['attachments'][0]['id'] == aid
    with pytest.raises(ServiceError):
        store.search_conversation('bob', cid, '矩阵特征值')


def test_attachment_catalog_is_scoped_and_includes_expired_status(tmp_path):
    store = ConversationStore(tmp_path / 'db.sqlite')
    cid = store.create('alice', 'openai')['id']
    file = tmp_path / 'x.pdf'
    file.write_bytes(b'%PDF-1.4')
    aid = store.upload('alice', cid, file, '题目.pdf')
    complete(store, 'alice', cid, '讲第一题', '答案', 'k', aid)
    file.unlink()
    catalog = store.conversation_attachments('alice', cid)
    assert catalog[0]['id'] == aid
    assert catalog[0]['available'] is False
    with pytest.raises(ServiceError):
        store.conversation_attachments('bob', cid)


def test_saved_model_index_finds_semantically_related_turn(tmp_path):
    store = ConversationStore(tmp_path / 'db.sqlite')
    cid = store.create('alice', 'openai')['id']
    run = complete(store, 'alice', cid, '请解释主方向', '这是线性变换的一类向量', 'one')
    store.save_conversation_index('alice', cid, run['id'], '线性代数：特征向量', ['eigenvector', '特征向量'])
    matches = store.search_conversation('alice', cid, 'eigenvector')
    assert matches[0]['run_id'] == run['id']
    assert matches[0]['summary'] == '线性代数：特征向量'
    reopened = ConversationStore(tmp_path / 'db.sqlite')
    assert reopened.search_conversation('alice', cid, 'eigenvector')[0]['run_id'] == run['id']
    with pytest.raises(ServiceError):
        store.save_conversation_index('bob', cid, run['id'], 'bad', ['bad'])


def test_reused_attachment_becomes_part_of_followup_run(tmp_path):
    store = ConversationStore(tmp_path / 'db.sqlite')
    cid = store.create('alice', 'openai')['id']
    file = tmp_path / 'x.pdf'; file.write_bytes(b'%PDF-1.4')
    aid = store.upload('alice',cid,file,'x.pdf')
    run = store.reserve('alice',cid,InteractiveRequest(provider='openai',model='gpt-6.1-sol',question='第二题'),'r')
    store.update(run['id'], status='running')
    store.bind_run_attachment('alice',cid,run['id'],aid)
    assert store.db.execute('SELECT attachment FROM runs WHERE id=?',(run['id'],)).fetchone()[0] == aid
    with pytest.raises(ServiceError):
        store.bind_run_attachment('bob',cid,run['id'],aid)
