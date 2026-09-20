"""Run the real demo in a temporary sandbox and export a standalone, labelled replay.
Run from the repo root: PYTHONPATH=. uv run python scripts/export_stage_replay.py --output work/demo-replay.html
"""
import argparse
import json
import tempfile
from pathlib import Path
from demo_stage import prepare
from shadow import stage, preview, unlearn


def capture():
    with tempfile.TemporaryDirectory(prefix='shadow-replay-') as tmp:
        prepare(Path(tmp), 'stage_learned')
        before=stage.report()
        a=before['results'][0]
        g=next(g for g in a['groups'] if g['approve_rule'])
        p=preview.build(a['client'],'stage',before['period'],g['rule_id'],g['condition'],a['role'],
                        limit=15,approve_rule=True,effective_from=before['period']+'-01')
        applied=preview.commit(p['preview_id'],a['role'])
        after=stage.report()
        deposits=next(g for g in after['results'][0]['groups'] if g['condition']=='amount_max')
        dp=preview.build(a['client'],'stage',before['period'],deposits['rule_id'],deposits['condition'],a['role'],limit=50)
        deposit_applied=preview.commit(dp['preview_id'],a['role'])
        both=stage.report()
        undo=unlearn.retract(a['client'],'stage',correction_id=applied['correction_id'])
        restored=stage.report()
        assert len(applied['reran'])==3 and len(deposit_applied['reran'])==4
        assert both['results'][0]['summary']['needs_review']==2
        assert len(undo['reopened'])==3
        assert restored['results'][0]['summary']['automatic_bank_items']==4
        for c in both['results']:
            assert len([i for i in c['items'] if i['item_kind']=='bank' and i.get('control_flags')])==2
        return {'before':before,'preview':p,'after':after,'both':both,'undo':undo,'restored':restored,
                'validation':'3 receipts cleared; 4 deposits cleared; 2 vendor payments held; undo reopened 3 and preserved the 4 deposits.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    record=capture()
    record["benchmark"] = json.loads(Path("benchrec/report_summary.json").read_text())
    template=Path('scripts/templates/replay.html').read_text()
    data=json.dumps(record).replace('<','\\u003c')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(template.replace('/*RECORDED_DATA*/',data))
    args.output.with_suffix('.json').write_text(json.dumps({'validation':record['validation'],
        'induction_sources':record['before']['provenance'],'before':record['before']['results'][0]['summary'],
        'after_two_answers':record['both']['results'][0]['summary'],'undo_checked':record['undo']['resolutions_checked'],
        'undo_reopened':len(record['undo']['reopened'])},indent=2))
    print(record['validation'])
    print(args.output.resolve())
