"""History/new-receipt separation and actual unlabeled matching, without model calls."""
import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from shadow import db, pipeline, playbook
from shadow.onboard import boundary, company, staging, importer, mapping


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA', tmp_path/'data')
    monkeypatch.setattr(db, 'RUNS', tmp_path/'runs')
    monkeypatch.setattr(company, 'CONFIG', db.DATA/'company.json')
    company.create('TESTNEW','Test Company','Customer receipts',{'1200':'Receivables','6990':'Small differences'},
                   [{'id':'lead','role':'finance reviewer','senior':True}],reconciler='lead')
    return 'TESTNEW'


def upload(c, role, header, rows, purpose='history'):
    buf=io.StringIO();w=csv.writer(buf);w.writerow(header);w.writerows(rows)
    rec=staging.store(c, role, 'source.csv', buf.getvalue().encode(), purpose)
    return importer.run_import(c, rec['upload_id'], mapping.guess(role,rec['columns']))


def freeze(c):
    boundary.freeze(c,'2026-03')
    playbook.save(c,'main',{'trained_before':'2026-03','rules':[]},{'type':'test_empty_policy'})


def test_source_receipts_have_no_imported_decisions_and_match_from_evidence(workspace):
    c=workspace;freeze(c)
    upload(c,'bank_lines',['source_id','date','amount','description','ref'],[['new','2026-03-03',100,'Customer receipt','REF']], 'incoming')
    upload(c,'ledger_entries',['date','amount','account','memo','ref'],[['2026-03-03',100,'1200','Customer receivable','REF']], 'incoming')
    con=db.connect(c,readonly=True)
    assert not db.q(con,'SELECT * FROM reconcile_link')
    assert not db.q(con,'SELECT * FROM journal_entry')
    assert not db.q(con,'SELECT * FROM approval')
    con.close()
    run=pipeline.run(c,'2026-03','playbook',use_llm=False,persist=False)
    assert run['items'][0]['resolution']['action']=='match'
    assert run['items'][0]['tier']=='matcher'


def test_new_receipts_reject_answers_and_training_overwrite(workspace):
    c=workspace
    upload(c,'bank_lines',['source_id','date','amount','description'],[['prior','2026-01-03',10,'Past receipt']])
    freeze(c)
    with pytest.raises(ValueError,match='decision columns'):
        upload(c,'bank_lines',['date','amount','description','decision'],[['2026-03-03',12,'Receipt','match']], 'incoming')
    with pytest.raises(ValueError,match='cannot include'):
        upload(c,'approvals',['date','subject_ref','approver','status'],[['2026-03-03','new','lead','approved']], 'incoming')
    with pytest.raises(ValueError,match='must be dated'):
        upload(c,'bank_lines',['date','amount','description'],[['2026-02-01',12,'Receipt']], 'incoming')
    with pytest.raises(ValueError,match='overwrite'):
        upload(c,'bank_lines',['source_id','date','amount','description'],[['prior','2026-03-03',500,'Changed history']], 'incoming')
    con=db.connect(c,readonly=True)
    assert db.q(con,'SELECT amount FROM bank_line')[0]['amount']==10
    con.close()


def test_training_snapshot_excludes_future_supporting_documents(workspace):
    c=workspace
    upload(c,'bank_lines',['date','amount','description'],[['2026-01-03',10,'Past receipt']])
    freeze(c)
    upload(c,'documents',['date','type','body'],[['2026-01-01','email','Later uploaded context']], 'incoming')
    frozen=sqlite3.connect(db.DATA/c/'training_snapshot.db')
    assert frozen.execute('SELECT COUNT(*) FROM document').fetchone()[0]==0
    frozen.close()
    with pytest.raises(ValueError,match='original history'):
        boundary.freeze(c,'2026-04')
    with pytest.raises(ValueError,match='fixed'):
        upload(c,'bank_lines',['date','amount','description'],[['2026-04-01',20,'New receipt']], 'history')


def test_estimate_does_not_save_a_fake_run_and_history_cannot_be_reconciled(workspace):
    c=workspace;freeze(c)
    upload(c,'bank_lines',['date','amount','description'],[['2026-03-03',777,'Unknown receipt']], 'incoming')
    from shadow.app import app
    api=TestClient(app)
    res=api.post('/api/onboarding/reconcile/estimate',json={'period':'2026-03'})
    assert res.status_code==200,res.text
    assert res.json()['cleared_free']==0
    assert not list(db.RUNS.glob('*/run.json'))
    assert api.post('/api/onboarding/reconcile',json={'period':'2026-02'}).status_code==400
    assert api.get('/api/onboarding/company').json()['incoming_periods']==['2026-03']


def test_missing_fields_and_bad_rows_fail_atomically(workspace):
    c=workspace
    with pytest.raises(ValueError):
        upload(c,'bank_lines',['date','amount'],[['2026-01-03',10]])
    with pytest.raises(ValueError):
        upload(c,'bank_lines',['date','amount','description'],[['2026-01-03',10,'Valid'],['not a date',20,'Invalid']])
    con=db.connect(c,readonly=True)
    assert not db.q(con,'SELECT * FROM bank_line')
    con.close()


def test_induction_route_uses_the_frozen_snapshot(workspace, monkeypatch):
    from shadow import jobs
    from shadow.onboard import preflight
    from shadow.app import app
    c=workspace
    monkeypatch.setattr(preflight,'run',lambda *a: {'ready':True,'before':'2026-03','estimate':{'seconds':1},'checks':[]})
    seen={}
    def induce(client,before,track,usage,db_file):
        seen['path']=db_file
        assert db_file.name=='training_snapshot.db' and db_file.exists()
        return {'version':1,'rules':[]}
    monkeypatch.setattr(playbook,'induce',induce)
    class Job:
        def phase(self, *a):pass
    monkeypatch.setattr(jobs,'submit',lambda kind,client,fn,**kw: {'state':'done','result':fn(Job())})
    result=TestClient(app).post('/api/onboarding/induce',json={})
    assert result.status_code==200,result.text
    assert seen['path']==db.DATA/c/'training_snapshot.db'


def test_jev_uses_company_records_without_a_stage_manifest(workspace,monkeypatch):
    from shadow import jev
    c=workspace;freeze(c)
    upload(c,'bank_lines',['date','amount','description'],[['2026-03-01',777,'Unidentified receipt']], 'incoming')
    con=db.connect(c,readonly=True);item=db.q(con,'SELECT id FROM bank_line')[0]['id'];con.close()
    monkeypatch.setattr(jev.stage,'report',lambda:pytest.fail('A company case must not use sample stage data'))
    payload,state=jev.context(c,[item],'Additional context')
    assert state['company']=='Test Company'
    assert set(state['roles'])=={'finance_reviewer'}
    assert state['items'][0]['record']['amount']==777
