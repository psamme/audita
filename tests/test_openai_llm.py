import json
from types import SimpleNamespace as NS
import pytest
from shadow import llm


class Item(NS):
    def model_dump(self, **kwargs):
        return vars(self).copy()


def response(*items, text='', status='completed'):
    return NS(output=list(items), output_text=text, status=status,
              usage=NS(input_tokens=100, output_tokens=20, input_tokens_details=NS(cached_tokens=40)))


def fake(monkeypatch, replies):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return replies.pop(0)
    monkeypatch.setenv('SHADOW_BACKEND', 'openai')
    monkeypatch.setattr(llm, '_openai_client', NS(responses=NS(create=create)))
    return requests


SCHEMA = {'type':'object', 'properties':{'approved':{'type':'boolean'}}, 'required':['approved']}
TOOL = {'name':'lookup', 'description':'Look up evidence', 'input_schema':{
    'type':'object','properties':{'id':{'type':'string'}}, 'required':['id']}}


def test_structured_output_validates_and_accounts_for_cached_tokens(monkeypatch):
    requests = fake(monkeypatch, [response(text='{"approved":"invalid"}'), response(text='{"approved":true}')])
    usage = llm.Usage()
    reply = llm.call('Review', [{'role':'user','content':'Evidence'}], schema=SCHEMA, usage=usage, model='gpt-5.2')
    assert json.loads(reply.text) == {'approved':True}
    assert usage.as_dict()['llm_calls'] == 2
    assert reply.usage['input_tokens'] == 60
    assert reply.usage['cost_usd'] == pytest.approx(.000392)
    assert requests[0]['store'] is False
    assert requests[0]['text']['format']['type'] == 'json_object'
    assert 'JSON' in requests[0]['input'][0]['content']


def test_tool_roundtrip_preserves_reasoning_and_call_id(monkeypatch):
    thought = Item(type='reasoning', id='rs_1', summary=[], encrypted_content='encrypted')
    tool = Item(type='function_call', id='fc_1', call_id='call_1', name='lookup', arguments='{"id":"invoice-1"}')
    requests = fake(monkeypatch, [response(thought, tool), response(text='Done')])
    messages = [{'role':'user','content':'Investigate'}]
    reply = llm.call('Review', messages, tools=[TOOL], model='gpt-5.2')
    assert reply.tool_calls == [{'id':'call_1','name':'lookup','input':{'id':'invoice-1'}}]
    messages += [{'role':'assistant','content':reply.raw_content},
                 {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_1','content':'evidence'}]}]
    llm.call('Review', messages, tools=[TOOL], model='gpt-5.2')
    assert requests[1]['input'][1]['encrypted_content'] == 'encrypted'
    assert requests[1]['input'][-1] == {'type':'function_call_output','call_id':'call_1','output':'evidence'}
    assert requests[0]['tools'][0]['strict'] is False


def test_incomplete_response_never_executes_tools(monkeypatch):
    fake(monkeypatch, [response(Item(type='function_call',name='lookup',call_id='x',arguments='{}'),status='incomplete')])
    with pytest.raises(RuntimeError, match='incomplete'):
        llm.call('Review', [], tools=[TOOL], model='gpt-5.2')


def test_refusal_never_accepted_as_policy(monkeypatch):
    fake(monkeypatch, [response(Item(type='message', content=[NS(type='refusal')]))])
    with pytest.raises(RuntimeError, match='refused'):
        llm.call('Review', [], schema=SCHEMA, model='gpt-5.2')


def test_missing_key_never_falls_back_to_claude(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('SHADOW_BACKEND', raising=False)
    monkeypatch.setattr(llm, '_openai_client', None)
    assert llm.backend() == 'openai'
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
        llm.call('Review', [], model='gpt-5.2')
