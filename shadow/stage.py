"""A judging view over actual pipeline outputs and reconstructed historical evidence."""
import copy
import json
import math
from pathlib import Path

from shadow import db, history, pipeline, playbook, preview, state


def config():
    path = db.DATA.parent / 'stage.json'
    if not path.exists():
        raise ValueError('Start demo_stage.py to prepare the learned-policy demo')
    return json.loads(path.read_text())


@state.serialized
def report():
    cfg = config()
    results = []
    for client in cfg['clients']:
        pb = playbook.load(client, cfg['track'])
        con = db.connect(client, readonly=True)
        try:
            info = db.q(con, 'SELECT * FROM client')[0]
            args = dict(client=client, period=cfg['period'], condition='playbook', track=cfg['track'],
                        use_llm=False, persist=False, con_override=con)
            run = pipeline.run(**args, playbook_override=pb)
            original = playbook.load(client, cfg['track'], 1)
            original_rules = {r['id']: r for r in original['rules']}
            baseline = pipeline.run(**args, playbook_override=original)
            historical = {c['id']: c for c in playbook.cases(con, pb['trained_before'])}
            rules = {r['id']: r for r in pb['rules']}
            for item in run['items']:
                r = rules.get(item['resolution'].get('rule_id'))
                item['rule'] = r
                item['original_rule'] = original_rules.get((r or {}).get('id'))
                evidence_rule = item['original_rule'] or r
                item['precedents'] = [historical[p] for p in (evidence_rule or {}).get('precedent_ids', []) if p in historical][:4]
            groups = []
            for q in playbook.band_questions(pb, limit=30):
                r = rules[q['rule_id']]
                if q['condition'] == 'amount_max':
                    values = [abs(i['record']['amount']) for i in run['items'] if i['item_kind'] == 'bank' and i['resolution'].get('rule_id') == q['rule_id']]
                    if values:
                        value = math.ceil(max(values) / 10) * 10
                        if q['lo'] < value <= q['value']:
                            q = q | {'value': value}
                trial = copy.deepcopy(pb)
                change = playbook.apply_band_answer(trial, q['rule_id'], q['condition'], None, limit=q['value'])
                if not change or change['held']:
                    continue
                after = pipeline.run(**args, playbook_override=trial)
                prior = {i['item_id']: i for i in run['items']}
                cleared = [i for i in after['items'] if i['item_kind'] == 'bank'
                           and i['resolution']['action'] in {'book', 'match', 'match_adjust'}
                           and prior[i['item_id']]['resolution']['action'] == 'escalate']
                approve_rule = r.get('status') != 'approved' or bool(r.get('open_question'))
                if approve_rule:
                    try:
                        trial, _ = preview.prepare_policy(con, pb, q['rule_id'], q['condition'], q['value'], 'preview only', cfg['period'] + '-01')
                    except ValueError:
                        continue
                    after = pipeline.run(**args, playbook_override=trial)
                    cleared = [i for i in after['items'] if i['item_kind'] == 'bank'
                               and i['resolution']['action'] in {'book', 'match', 'match_adjust'}
                               and prior[i['item_id']]['resolution']['action'] == 'escalate']
                if not cleared:
                    continue
                groups.append(q | {'approve_rule': approve_rule, 'role': prior[cleared[0]['item_id']]['resolution'].get('escalate_to'), 'affected': len(cleared), 'items': [i['item_id'] for i in cleared],
                                   'precedents': [historical[p] for p in r.get('precedent_ids', []) if p in historical][:4],
                                   'rule': r})
            groups.sort(key=lambda g: (-g['affected'], g['rule_id']))
            roles = [history.role_key(u['role']) for u in db.q(con, 'SELECT role FROM user WHERE senior=1 ORDER BY role')]
            log = db.DATA / client / f"corrections_{cfg['track']}.jsonl"
            events = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
            retracted = {e.get('retracted') for e in events if e.get('type') == 'retraction'}
            applied_ids = {playbook.load(client, cfg['track'], v).get('cause', {}).get('correction_id')
                           for v in playbook.versions(client, cfg['track'])}
            undoable = [e for e in events if e.get('correction_id') in applied_ids
                        and e.get('correction_id') not in retracted and e.get('type') != 'retraction']
            results.append({'client': client, 'name': info['name'], 'chart': info['chart'], 'role': next((g['role'] for g in groups if g['role'] in roles), roles[0] if roles else None),
                            'version': pb['version'], 'trained_before': pb['trained_before'], 'cause': pb.get('cause'),
                            'origin': cfg['provenance'][client], 'historical_cases': len(historical),
                            'history_bank_lines': db.q(con, 'SELECT COUNT(*) n FROM bank_line WHERE period < ?', pb['trained_before'])[0]['n'],
                            'rules': pb['rules'], 'groups': groups, 'items': run['items'],
                            'summary': preview.summarize(run['items']), 'baseline': preview.summarize(baseline['items']),
                            'undoable': undoable[-1:]})
        finally:
            con.close()
    return cfg | {'results': results}
