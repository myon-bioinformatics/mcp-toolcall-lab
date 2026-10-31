from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mcp_toolcall_lab import budget_compression as bc

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/budget_compression/cases.json').read_text())


def test_twenty_fictional_bilingual_cases_keep_all_hard_anchors():
    assert len(CASES) == len({c['id'] for c in CASES}) == 20
    assert {c['language'] for c in CASES} == {'en', 'ja'}
    for c in CASES:
        assert len(c['advertised_tools']) == 5
        for row in bc.candidates(c):
            if row['id'] == 'rules':
                continue  # Generic contractions may be rejected by the literal policy.
            assert all(anchor in row['text'] for anchor in c['required_anchors'])
        assert c['candidates'][-1]['losses']


def test_catalogue_declares_types_and_optional_arguments_independently():
    catalogue = CASES[0]['advertised_tools']
    assert all(case['advertised_tools'] == catalogue for case in CASES)
    text = bc.prompt_for(CASES[0], CASES[0]['source'])
    assert 'job:string,max_retries:integer,on_failure_only:boolean,note?:string' in text
    assert 'delete_allowed:boolean,review_required:boolean' in text
    assert 'all arguments required except ?' in text


def test_selection_does_not_read_expected_tool_or_arguments():
    case = copy.deepcopy(CASES[0]); original = bc.prompt_for(case, case['source'])
    case['tool'] = 'not-advertised'; case['required_arguments'] = {'secret_oracle': 'HIDDEN'}
    assert bc.prompt_for(case, case['source']) == original
    assert 'HIDDEN' not in original


def test_large_budget_preserves_verbatim_and_exact_boundary():
    c = CASES[0]; prompt = bc.prompt_for(c, c['source'])
    chosen = bc.select(c, max_chars=len(prompt), max_tokens=len(prompt), token_counter=len)
    assert chosen['selected']['candidate'] == 'verbatim'
    assert chosen['selected']['prompt'] == prompt


def test_lossy_fits_budget_and_does_not_silently_truncate():
    c = CASES[0]; skeleton = c['candidates'][-1]; size = len(bc.prompt_for(c, skeleton['text']))
    row = bc.select(c, max_chars=size, max_tokens=size, token_counter=len)
    assert row['status'] == 'selected'
    assert row['selected']['candidate'] == 'skeleton'
    assert row['selected']['losses']
    assert row['selected']['prompt'].endswith(skeleton['text'])
    assert row['selected']['chars'] <= size and row['selected']['tokens'] <= size
    too_small = bc.select(c, max_chars=size - 1, max_tokens=size, token_counter=len)
    assert too_small['status'] == 'infeasible' and too_small['selected'] is None


def test_character_and_token_constraints_are_independent():
    c = CASES[0]
    assert bc.select(c, max_chars=10000, max_tokens=0, token_counter=lambda _: 1)['status'] == 'infeasible'
    assert bc.select(c, max_chars=0, max_tokens=10000, token_counter=lambda _: 1)['status'] == 'infeasible'
    assert bc.select(c, max_chars=10000, max_tokens=10000)['status'] == 'tokenizer_unavailable'
    assert bc.select(c, max_chars=10000, max_tokens=None)['status'] == 'selected'


def test_candidate_missing_negation_or_bound_is_rejected():
    c = copy.deepcopy(CASES[2]); c['candidates'] = [{'id': 'unsafe', 'loss_rank': 2, 'text': 'Do run on success.', 'losses': ['negation']}]
    result = bc.select(c, max_chars=0, max_tokens=None)
    unsafe = next(x for x in result['attempts'] if x['candidate'] == 'unsafe')
    assert unsafe['missing_anchors'] and not unsafe['fits']


def test_required_task_success_and_optional_quality_are_separate():
    c = CASES[0]
    core = json.dumps({'tool': c['tool'], 'arguments': c['required_arguments']})
    judged = bc.judge(c, core)
    assert judged['task_success'] is True and judged['quality_score'] == 0
    full = json.dumps({'tool': c['tool'], 'arguments': {**c['required_arguments'], **c['preferred_arguments']}})
    assert bc.judge(c, full)['quality_score'] == 1
    assert bc.judge(c, '{"tool":null,"arguments":{}}')['status'] == 'abstained'
    assert bc.judge(c, 'unknown')['status'] == 'invalid_output'


def test_wrong_tool_unknown_arguments_and_bool_numeric_coercion_fail():
    c = CASES[2]
    for tool, args in [('invented', c['required_arguments']), (c['tool'], {**c['required_arguments'], 'extra': 1}),
                       (c['tool'], {**c['required_arguments'], 'on_failure_only': 1})]:
        assert bc.judge(c, json.dumps({'tool': tool, 'arguments': args}))['task_success'] is False


def test_unavailable_not_run_and_model_error_do_not_become_success():
    c = CASES[0]
    assert bc.evaluate(c, max_chars=0, max_tokens=None, answer=lambda _: pytest.fail())['task_success'] is None
    assert bc.evaluate(c, max_chars=10000, max_tokens=None)['status'] == 'not_run'
    def fail(_): raise RuntimeError('offline')
    r = bc.evaluate(c, max_chars=10000, max_tokens=None, answer=fail)
    assert r['status'] == 'model_error' and r['task_success'] is None


@pytest.mark.parametrize('limit', [-1, True, 1.5])
def test_invalid_budgets_fail(limit):
    with pytest.raises(ValueError): bc.select(CASES[0], max_chars=limit, max_tokens=None)


def test_cli_offline_counts_all_infeasible_trials(tmp_path):
    dest = tmp_path / 'out.json'
    assert bc.main(['--cases', str(Path(__file__).parents[1] / 'fixtures/budget_compression/cases.json'),
                    '--char-budget', '1', '10000', '--token-budget', '128', '--output', str(dest)]) == 0
    r = json.loads(dest.read_text())
    assert r['summary']['all_trials'] == 40
    assert r['summary']['statuses'] == {'tokenizer_unavailable': 40}
    assert r['summary']['task_success_rate_all_trials'] == 0


def test_tokenizer_failure_and_invalid_count_are_not_model_errors():
    def fail(_): raise RuntimeError('counter unavailable')
    for counter in [fail, lambda _: -1, lambda _: True]:
        row = bc.evaluate(CASES[0], max_chars=10000, max_tokens=1000, token_counter=counter,
                          answer=lambda _: pytest.fail('must not call model without a count'))
        assert row['status'] == 'measurement_error' and row['task_success'] is None


def test_compaction_keeps_all_signatures_and_unknown_constraints():
    case = copy.deepcopy(CASES[0])
    case['advertised_tools'][0] += ' Never send payment; require approval.'
    prompt = bc.prompt_for(case, case['source'])
    assert case['advertised_tools'][0] in prompt
    for tool in case['advertised_tools'][1:]:
        assert tool.split(': ', 1)[0] in prompt
    assert prompt.endswith(case['source'])


def test_all_twenty_fit_chars_without_extra_task_loss():
    for case in CASES:
        row = bc.select(case, max_chars=800, max_tokens=None)
        assert row['status'] == 'selected'
        assert row['selected']['chars'] <= 800
        assert not row['selected']['missing_anchors']
        assert row['selected']['loss_rank'] <= 2  # preferred arguments need not be dropped


def test_each_budget_boundary_is_inclusive_and_checked_independently():
    case = CASES[0]
    size = len(bc.prompt_for(case, case['candidates'][-1]['text']))
    assert bc.select(case, max_chars=size, max_tokens=512,
                     token_counter=lambda _: 512)['status'] == 'selected'
    assert bc.select(case, max_chars=size - 1, max_tokens=512,
                     token_counter=lambda _: 512)['status'] == 'infeasible'
    assert bc.select(case, max_chars=size, max_tokens=512,
                     token_counter=lambda _: 513)['status'] == 'infeasible'
