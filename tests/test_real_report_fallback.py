import json
from shadow import routes_real


def test_saved_summary_works_without_local_benchmark_run(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_real, 'RESULTS', tmp_path / 'missing.json')
    report = routes_real.results()
    assert report['summary_only'] is True
    tier = report['tiers']['matcher_plus_conventions']
    assert abs(tier['match_rate_pair'] - tier['correct_pair'] / report['eval']['matchable']) < 1e-12
    assert abs(tier['precision_pair'] - tier['correct_pair'] / tier['matched']) < 1e-12
    assert tier['matched'] - tier['correct_pair'] == tier['wrong_pair']
    assert routes_real.playbook()['conventions'] == []
    assert routes_real.examples()['examples'] == []


def test_local_report_takes_precedence(tmp_path, monkeypatch):
    path = tmp_path / 'report.json'
    path.write_text(json.dumps({'client': 'local run', 'examples': [], 'playbook': []}))
    monkeypatch.setattr(routes_real, 'RESULTS', path)
    assert routes_real.results() == {'client': 'local run'}
