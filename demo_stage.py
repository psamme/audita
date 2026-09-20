"""Prepare and serve learned policies in an isolated, repeatable judging sandbox.

uv run python demo_stage.py --port 8793
No model calls at launch. Policies come from a saved model induction on Jan-Feb history.
"""
import argparse
import hashlib
import json
import sqlite3
import shutil
from pathlib import Path

import uvicorn
from shadow import db, playbook


def prepare(root: Path, source_track: str):
    root = root.resolve()
    if (root / 'stage.json').exists():
        db.DATA, db.RUNS = root / 'data', root / 'runs'
        return
    source = db.DATA
    pbs = {c: playbook.load(c, source_track, 1) for c in ('A', 'B')}
    if any(not p or p.get('cause', {}).get('type') != 'induction' or p.get('trained_before') != '2026-03'
           or p.get('synthetic_demo') for p in pbs.values()):
        raise ValueError('Expected saved model-induced policies trained strictly before March, not hand-authored fixtures')
    db.DATA, db.RUNS = root / 'data', root / 'runs'
    provenance = {}
    for client, pb in pbs.items():
        src = sqlite3.connect(f'file:{source / client / "client.db"}?mode=ro', uri=True)
        con = db.connect(client)
        for table in preview_tables():
            clause = ' WHERE period < \'2026-04\'' if table in {'bank_line', 'ledger_entry', 'reconcile_link', 'journal_entry', 'approval'} else ' WHERE date < \'2026-04-01\'' if table in {'invoice', 'document'} else ''
            rows = src.execute(f'SELECT * FROM {table}' + clause).fetchall()
            if rows:
                con.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in rows[0])})', rows)
        src.close()
        # Fresh, clearly labelled stage transactions. These never enter induction or accuracy evaluation.
        for n, (party, short) in enumerate([('Brightwater Group LLC', 12.40), ('Juniper Studio LLC', 13.10), ('Northbridge Works LLC', 14.20)]):
            ref = f'STAGE-INV-{n}'
            con.execute('INSERT INTO invoice VALUES (?,?,?,?,?,?,?,?)', (ref,party,'AR','2026-05-01','2026-05-12',4800,'net30','open'))
            con.execute('INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)', (f'{client}-STAGE-SHORT-{n}','2026-05','2026-05-12',4800-short,'ACH CREDIT '+party+' '+ref,party,ref))
            con.execute('INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)', (f'{client}-STAGE-AR-{n}','2026-05','2026-05-12','2026-05-12','1200',4800,'Expected customer receipt',party,ref,ref))
        for n, amount in enumerate((44.50,46.17,48.20,49.50)):
            con.execute('INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)', (f'{client}-STAGE-MOBILE-{n}','2026-05',f'2026-05-{14+n}',amount,'MOBILE DEPOSIT','', ''))
        con.execute('INSERT INTO document VALUES (?,?,?,?,?,?,?)', (f'{client}-STAGE-DOC','email','2026-05-05','billing@vendor.example','New bank details','Our bank account has changed. Please update payment details.',json.dumps({'party':'Demo vendor'})))
        for n, amount in enumerate((-900,-1100)):
            ref=f'STAGE-PAY-{n}'
            con.execute('INSERT INTO bank_line VALUES (?,?,?,?,?,?,?)', (f'{client}-STAGE-PAY-{n}','2026-05',f'2026-05-{20+n}',amount,'Vendor payment '+ref,'Demo vendor',ref))
            con.execute('INSERT INTO ledger_entry VALUES (?,?,?,?,?,?,?,?,?,?)', (f'{client}-STAGE-AP-{n}','2026-05',f'2026-05-{20+n}',f'2026-05-{20+n}','2000',amount,'Vendor payment','Demo vendor',ref,None))
        con.commit()
        # Validate against the same historical window using current code. No new policy is authored here.
        playbook.backtest(con, pb)
        saved=playbook.save(client,'stage', {k:v for k,v in pb.items() if k not in ('version','created_at','cause')},
                           {'type':'induction','source_track':source_track,'source_version':1,'before':pb['trained_before'],'note':'Saved model induction; replayed against Jan-Feb history on this build.'})
        source_file=source/client/'playbook'/source_track/'v1.json'
        provenance[client]={'track':source_track,'version':1,'sha256':hashlib.sha256(source_file.read_bytes()).hexdigest(),
                            'method':'Model induction from ERP links, journal adjustments, approvals and emails', 'training_window':'January and February 2026'}
        con.close()
    (root/'stage.json').write_text(json.dumps({'track':'stage','period':'2026-05','clients':['A','B'],'provenance':provenance,
          'disclosure':'Synthetic ERP history. Model-induced policies. May transactions are constructed demonstration cases, not an accuracy benchmark. Live approval and undo use no model calls.'},indent=2))


def preview_tables():
    return ('client','user','bank_line','ledger_entry','invoice','document','reconcile_link','journal_entry','approval')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('work/judging-demo'))
    parser.add_argument('--source-track',default='stage_learned')
    parser.add_argument('--port',type=int,default=8793)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--reset',action='store_true',help='Reset only an existing labelled stage sandbox. Stop its server first.')
    args=parser.parse_args()
    if args.reset and args.data_dir.exists():
        marker=args.data_dir/'stage.json'
        if not marker.exists() or json.loads(marker.read_text()).get('track') != 'stage':
            raise ValueError('Refusing to reset a directory that is not a labelled stage sandbox')
        shutil.rmtree(args.data_dir)
    prepare(args.data_dir,args.source_track)
    if not args.prepare_only:
        print(f'Judging demo: http://127.0.0.1:{args.port}/company/index.html?track=main',flush=True)
        uvicorn.run('shadow.app:app',host='127.0.0.1',port=args.port)
