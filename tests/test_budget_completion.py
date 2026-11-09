"""Completion protocol regression tests: no server, model, or network calls."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('budget_compare', ROOT / 'scripts/compare_budget_overhead.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)
CASE = json.loads((ROOT / 'fixtures/budget_compression/cases.json').read_text())[0]


def run(response):
    return compare.evaluate_with_completion(compare.new, CASE, count=lambda _: 1,
                                           post=lambda *_: response, base='offline://fixture')


@pytest.mark.parametrize('reason', [None, '', 'content_filter', 'tool_calls', 'function_call', 'unknown'])
def test_non_cap_finish_reasons_are_model_errors(reason):
    response = {'choices': [{'finish_reason': reason, 'message': {'content': 'partial'}}]}
    result = run(response)
    assert result['status'] == 'model_error'
    assert result['task_success'] is not True
    assert result['completion']['finish_reason'] == reason
    assert result['completion']['raw_response'] == 'partial'
    assert result['completion']['response'] == response


@pytest.mark.parametrize('response', [None, {}, {'choices': []}, {'choices': [None]},
    {'choices': [{'finish_reason': 'length', 'message': {'content': None}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': None}}]},
    {'choices': [{'finish_reason': 'length', 'message': {'content': '', 'tool_calls': [{'id': 'x'}]}}]}])
def test_malformed_completions_fail_closed_and_keep_response(response):
    result = run(response)
    assert result['status'] == 'model_error'
    assert result['completion']['response'] == response


def test_only_explicit_text_length_preserves_partial_output_as_quality_observation():
    result = run({'choices': [{'finish_reason': 'length', 'message': {'content': '{"tool":'}}]})
    assert result['status'] == 'incomplete_output'
    assert result['task_success'] is not True
    assert result['completion']['finish_reason'] == 'length'
    assert result['completion']['raw_response'] == '{"tool":'


def test_normal_stop_still_uses_independent_scorer():
    text = json.dumps({'tool': CASE['tool'], 'arguments': CASE['required_arguments']})
    result = run({'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]})
    assert result['status'] == 'success'
    assert result['task_success'] is True
    wrong = run({'choices': [{'finish_reason': 'stop', 'message': {'content': 'not JSON'}}]})
    assert wrong['status'] == 'invalid_output'


def test_transport_errors_remain_model_errors():
    def fail(*_):
        raise ConnectionError('offline fixture')
    result = compare.evaluate_with_completion(compare.new, CASE, count=lambda _: 1, post=fail, base='offline:')
    assert result['status'] == 'model_error'
    assert 'completion' not in result
