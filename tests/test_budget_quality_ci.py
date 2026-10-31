"""Offline evidence validation; saved real-token receipts are not re-inference."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('budget_ci', ROOT / 'scripts/summarize_budget_quality.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
CASES = (ROOT / 'fixtures/budget_compression/cases.json').read_bytes()
REPORT = json.loads((ROOT / 'docs/experiments/budget-quality/v3-overhead-paired.json').read_text())


def test_saved_paired_receipt_keeps_failures_and_denominators_separate():
    summary = ci.summarize(REPORT, CASES)
    assert summary['before']['statuses'] == {'success': 12, 'infeasible': 8}
    assert summary['after']['successes'] == 20
    assert summary['before']['preferred_total'] == 12
    assert summary['after']['preferred_total'] == 20


@pytest.mark.parametrize('damage', ['missing_case', 'wrong_hash', 'wrong_budget', 'token_over',
                                    'chars_over', 'bad_length', 'missing_anchor', 'bool_tokens'])
def test_corrupt_or_over_budget_evidence_is_rejected(damage):
    report = copy.deepcopy(REPORT)
    selected = report['cases'][0]['after']['selected']
    if damage == 'missing_case': report['cases'].pop()
    elif damage == 'wrong_hash': report['corpus_sha256'] = 'wrong'
    elif damage == 'wrong_budget': report['cases'][0]['after']['max_tokens'] = 513
    elif damage == 'token_over': selected['tokens'] = 513
    elif damage == 'bool_tokens': selected['tokens'] = True
    elif damage == 'chars_over': selected.update(prompt='a' * 801, chars=801)
    elif damage == 'bad_length': selected['chars'] += 1
    elif damage == 'missing_anchor': selected['missing_anchors'] = ['missing']
    with pytest.raises(ValueError):
        ci.summarize(report, CASES)


@pytest.mark.parametrize('status', ['incomplete_output', 'wrong_action', 'abstained', 'model_error', 'measurement_error'])
@pytest.mark.parametrize('strict', [False, True])
def test_candidate_quality_is_observed_but_infrastructure_always_fails(tmp_path, monkeypatch, status, strict):
    report = copy.deepcopy(REPORT)
    report['cases'][0]['after'].update(status=status, task_success=False)
    receipt = tmp_path / 'paired.json'; receipt.write_text(json.dumps(report))
    model = tmp_path / 'model.gguf'
    model.with_suffix('.gguf.provenance.json').write_text(json.dumps({
        'model_id': 'candidate', 'quality_gate': strict, 'reasoning': 'off', 'template': None}))
    monkeypatch.setattr('sys.argv', ['summary', '--report', str(receipt), '--model', str(model),
                                   '--image', 'offline-test', '--summary', str(tmp_path / 'summary.md')])
    if strict or status in {'model_error', 'measurement_error'}:
        with pytest.raises(SystemExit): ci.main()
    else:
        ci.main()
    provenance = json.loads((tmp_path / 'provenance.json').read_text())
    assert provenance['summary']['after']['successes'] == 19
    assert provenance['quality_gate'] is strict
