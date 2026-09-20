"""The restored onboarding runs in isolated processes, including background imports."""
import time
from fastapi.testclient import TestClient


def test_company_setup_import_and_session_isolation(monkeypatch,tmp_path):
    from shadow import db, preview
    monkeypatch.setattr(db,'DATA',db.DATA)
    monkeypatch.setattr(db,'RUNS',db.RUNS)
    monkeypatch.setattr(preview,'PREVIEWS',{})
    import demo_public
    import public_workspaces as workers
    monkeypatch.setattr(workers,'BASE',tmp_path/'companies')
    monkeypatch.setattr(workers,'WORKERS',{})
    try:
        with TestClient(demo_public.app,base_url='https://demo.test') as a, TestClient(demo_public.app,base_url='https://demo.test') as b:
            api='/company-api/onboarding'
            assert a.get(api+'/company').json()=={'created':False}
            payload={'id':'TEST','name':'Example Company','blurb':'Customer receipts',
                     'chart':{'1200':'Receivables'},'users':[{'id':'lead','name':'Lead','role':'owner','senior':True}]}
            created=a.post(api+'/company',json=payload)
            assert created.status_code==200,created.text
            assert created.json()['created']
            assert b.get(api+'/company').json()=={'created':False}
            upload=a.post(api+'/upload?role=bank_lines&filename=history.csv',content='date,amount,description,ref\n2026-01-03,100,Customer receipt,REF1\n',headers={'content-type':'text/csv'})
            assert upload.status_code==200,upload.text
            uid=upload.json()['upload_id']
            mapped=a.post(api+'/mapping/propose',json={'upload_id':uid})
            assert mapped.status_code==200,mapped.text
            imported=a.post(api+'/import',json={'upload_id':uid,'mapping':{k:mapped.json()[k] for k in ('columns','sign')}})
            assert imported.status_code==200,imported.text
            for _ in range(50):
                job=a.get('/company-api/jobs/'+imported.json()['job_id']).json()
                if job['state'] in ('done','error'): break
                time.sleep(.1)
            assert job['state']=='done',job
            assert a.get(api+'/company').json()['counts']['bank_line']==1
            assert b.post(api+'/mapping/propose',json={'upload_id':uid}).status_code==404
            assert a.post('/company-api/jev/key',json={'key':'never'}).status_code==404
            assert a.get('/company-api/runs?grades=true').status_code==404
            assert a.get('/api/stage').json()['results'][0]['summary']['needs_review']==9
    finally:
        workers.stop_workers()
        for proc,_ in workers.WORKERS.values():
            proc.wait(timeout=10)
