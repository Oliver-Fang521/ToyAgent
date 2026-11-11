import pytest
from gaia_agent.providers.base import ModelResponse
from gaia_agent.interactive.conversation_context import select_history_file

class Provider:
    def __init__(self, result): self.result = result
    async def complete(self, request, executor=None):
        self.request = request
        return ModelResponse(response_status='completed', parsed=self.result)

@pytest.mark.asyncio
async def test_model_selects_previous_file_for_followup():
    provider = Provider({'action': 'read', 'attachment_id': 'file-1', 'clarification': ''})
    result = await select_history_file(provider, '讲第二题', [{'role':'user','content':'讲第一题'}], [{'id':'file-1','name':'习题.pdf','available':True}], 'low')
    assert result.action == 'read'
    assert result.attachment_id == 'file-1'
    assert '习题.pdf' in str(provider.request.input_items)

@pytest.mark.asyncio
async def test_model_cannot_select_unknown_file():
    provider = Provider({'action':'read','attachment_id':'other-user-file','clarification':''})
    result = await select_history_file(provider, '讲第二题', [], [{'id':'file-1','name':'习题.pdf','available':True}], 'low')
    assert result.action == 'clarify'

@pytest.mark.asyncio
async def test_model_can_leave_unrelated_chat_without_file():
    provider = Provider({'action':'none','attachment_id':None,'clarification':''})
    result = await select_history_file(provider, '你好', [], [{'id':'file-1','name':'习题.pdf','available':True}], 'low')
    assert result.action == 'none'

@pytest.mark.asyncio
async def test_selected_file_is_scoped_and_copied_for_existing_pipeline(tmp_path):
    from gaia_agent.interactive.conversation_context import resolve_history_file
    from gaia_agent.interactive.store import ConversationStore
    from gaia_agent.interactive.models import InteractiveRequest
    store = ConversationStore(tmp_path / 'db.sqlite')
    cid = store.create('alice', 'openai')['id']
    source = tmp_path / 'source.pdf'; source.write_bytes(b'%PDF-example')
    aid = store.upload('alice', cid, source, '题目.pdf')
    run = store.reserve('alice', cid, InteractiveRequest(provider='openai', model='gpt-6.1-sol', question='第一题', attachment_id=aid), 'first')
    store.update(run['id'], status='completed', result={'answer':'第一题答案','usage':{},'evidence':[]})
    workspace = tmp_path / 'run'; workspace.mkdir()
    provider = Provider({'action':'read','attachment_id':aid,'clarification':''})
    selected, clarification, _ = await resolve_history_file(provider, '讲第二题', store.history('alice',cid), store.conversation_attachments('alice',cid), lambda i: store.attachment('alice',cid,i), workspace, 'low')
    assert selected.read_bytes() == source.read_bytes()
    assert selected.parent == workspace
    assert clarification == ''

@pytest.mark.asyncio
async def test_expired_selected_file_requests_reupload(tmp_path):
    from gaia_agent.interactive.conversation_context import resolve_history_file
    from gaia_agent.interactive.store import ServiceError
    provider = Provider({'action':'read','attachment_id':'file-1','clarification':''})
    def unavailable(_): raise ServiceError('attachment_expired')
    path, message, _ = await resolve_history_file(provider, '第二题', [], [{'id':'file-1','name':'old.pdf','available':False}], unavailable, tmp_path, 'low')
    assert path is None
    assert '重新上传' in message

@pytest.mark.asyncio
async def test_ambiguous_files_return_model_clarification(tmp_path):
    from gaia_agent.interactive.conversation_context import resolve_history_file
    provider = Provider({'action':'clarify','attachment_id':None,'clarification':'你指哪一份文件？'})
    path, message, _ = await resolve_history_file(provider, '第二题', [], [{'id':'a','name':'a.pdf','available':True}, {'id':'b','name':'b.pdf','available':True}], lambda i: None, tmp_path, 'low')
    assert path is None
    assert message == '你指哪一份文件？'

@pytest.mark.asyncio
async def test_model_generates_bounded_index():
    from gaia_agent.interactive.conversation_context import build_conversation_index
    provider = Provider({'summary':'线性代数：特征向量', 'keywords':['特征向量','eigenvector']})
    index = await build_conversation_index(provider, '讲主方向', '特征向量解释', [], 'low')
    assert index.summary == '线性代数：特征向量'
    assert 'eigenvector' in index.keywords

@pytest.mark.asyncio
async def test_index_never_persists_secret_returned_by_model():
    from gaia_agent.interactive.conversation_context import build_conversation_index
    provider = Provider({'summary':'API key sk-abcdefghijklmnopqrstuvwxyz', 'keywords':['secret']})
    with pytest.raises(ValueError):
        await build_conversation_index(provider, 'hello', 'world', [], 'low')
