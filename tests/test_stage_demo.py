"""End-to-end rehearsal using saved model induction, never a live model or hidden evaluation."""
import pytest
from shadow import db, llm, preview
from scripts.export_stage_replay import capture


def test_learned_stage_two_answers_and_selective_undo(monkeypatch):
    # Register globals with monkeypatch so the launcher's isolated paths are restored after the test.
    monkeypatch.setattr(db, 'DATA', db.DATA)
    monkeypatch.setattr(db, 'RUNS', db.RUNS)
    monkeypatch.setattr(preview, 'PREVIEWS', {})
    monkeypatch.setattr(llm, 'call', lambda *a, **kw: pytest.fail('The stage must not call a model'))
    result=capture()
    a,b=result['after']['results']
    first=lambda c: next(i for i in c['items'] if i['item_id'].endswith('SHORT-0'))
    left,right=first(a),first(b)
    for field in ('amount','date','description','counterparty','ref'):
        assert left['record'][field]==right['record'][field]
    assert left['reference_invoice']['amount'] == 4800
    assert left['reference_invoice']['direction'] == 'AR'
    assert left['resolution']['action']=='match_adjust'
    assert right['resolution']['action']=='escalate'
    assert result['both']['results'][0]['summary']['needs_review']==2
    assert len(result['undo']['reopened'])==3
    assert result['restored']['results'][0]['summary']['automatic_bank_items']==4
    assert left['precedents']  # Original learning evidence remains visible after dated sign-off.
    assert a['trained_before']=='2026-03'
