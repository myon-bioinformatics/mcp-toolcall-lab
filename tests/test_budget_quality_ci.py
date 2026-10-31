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
