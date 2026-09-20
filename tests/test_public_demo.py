"""The public demo must run real mutations without leaking them between visitors."""
from fastapi.testclient import TestClient


def test_public_policy_flow_isolated_and_private_routes_blocked(monkeypatch,tmp_path):
    from shadow import db, preview, llm
    monkeypatch.setattr(db,'DATA',db.DATA)
    monkeypatch.setattr(db,'RUNS',db.RUNS)
    monkeypatch.setattr(preview,'PREVIEWS',{})
    monkeypatch.setattr(llm,'call',lambda *a,**k: (_ for _ in ()).throw(AssertionError('No model needed')))
    import demo_public as public
    monkeypatch.setattr(public,'SESSIONS',tmp_path/'sessions')
    public.SESSIONS.mkdir()
    monkeypatch.setattr(public,'PREVIEWS',{})
    monkeypatch.setattr(public,'VISITS',{})
    with TestClient(public.app,base_url='https://demo.test') as a, TestClient(public.app,base_url='https://demo.test') as b:
        before=a.get('/api/stage').json()
        company=before['results'][0]
        group=next(g for g in company['groups'] if g['approve_rule'])
        payload={'client':company['client'],'track':'stage','period':before['period'],
                 'rule_id':group['rule_id'],'condition':group['condition'],'role':company['role'],
                 'answer':'limit','limit':15,'confirm_rule':True,'effective_from':'2026-05-01'}
        p=a.post('/api/playbook/preview-policy',json=payload)
        assert p.status_code==200,p.text
        token=p.json()['preview_id']
        assert b.post('/api/playbook/apply-preview',json={'preview_id':token,'role':company['role']}).status_code==409
        applied=a.post('/api/playbook/apply-preview',json={'preview_id':token,'role':company['role']})
        assert applied.status_code==200,applied.text
        assert a.get('/api/stage').json()['results'][0]['summary']['needs_review']==6
        assert b.get('/api/stage').json()['results'][0]['summary']['needs_review']==9
        result=a.post('/api/retract',json={'client':'A','track':'stage','role':company['role'],'correction_id':applied.json()['correction_id']})
        assert result.status_code==200,result.text
        assert len(result.json()['reopened'])==3
        assert a.get('/api/stage').json()['results'][0]['summary']['needs_review']==9
        assert a.post('/api/jev/key',json={'key':'x'*30}).status_code==404
        assert a.post('/api/onboarding/company',json={}).status_code==404
        assert a.get('/api/runs?grades=true').status_code==403
        assert a.get('/api/playbook/A?track=main').status_code==400
        assert 'no-store' in a.get('/api/clients').headers['cache-control']
