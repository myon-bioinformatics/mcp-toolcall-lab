"""Budget experiments, small CPU models and model workflow gates: offline only, inference runs in its own CI jobs."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
from mcp_toolcall_lab import budget_compression as bc
from mcp_toolcall_lab.aoi_gguf_benchmark import ATTACKS, BenchmarkError, EXPECTED_CODES, REFUSED, _as_digit, catalog, run, scenario


# ═══ · budget completion ═════════════════════════════════════════════════════════════════════════════════
# Completion protocol regression tests: no server, model, or network calls.

spec = importlib.util.spec_from_file_location('budget_compare', ROOT / 'scripts/compare_budget_overhead.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)
CASE = json.loads((ROOT / 'fixtures/budget_compression/cases.json').read_text())[0]


def run_budget_completion(response):
    return compare.evaluate_with_completion(compare.new, CASE, count=lambda _: 1,
                                           post=lambda *_: response, base='offline://fixture')


@pytest.mark.parametrize('reason', [None, '', 'content_filter', 'tool_calls', 'function_call', 'unknown'])
def test_non_cap_finish_reasons_are_model_errors(reason):
    response = {'choices': [{'finish_reason': reason, 'message': {'content': 'partial'}}]}
    result = run_budget_completion(response)
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
    result = run_budget_completion(response)
    assert result['status'] == 'model_error'
    assert result['completion']['response'] == response


def test_only_explicit_text_length_preserves_partial_output_as_quality_observation():
    result = run_budget_completion({'choices': [{'finish_reason': 'length', 'message': {'content': '{"tool":'}}]})
    assert result['status'] == 'incomplete_output'
    assert result['task_success'] is not True
    assert result['completion']['finish_reason'] == 'length'
    assert result['completion']['raw_response'] == '{"tool":'


def test_normal_stop_still_uses_independent_scorer():
    text = json.dumps({'tool': CASE['tool'], 'arguments': CASE['required_arguments']})
    result = run_budget_completion({'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]})
    assert result['status'] == 'success'
    assert result['task_success'] is True
    wrong = run_budget_completion({'choices': [{'finish_reason': 'stop', 'message': {'content': 'not JSON'}}]})
    assert wrong['status'] == 'invalid_output'


def test_transport_errors_remain_model_errors():
    def fail(*_):
        raise ConnectionError('offline fixture')
    result = compare.evaluate_with_completion(compare.new, CASE, count=lambda _: 1, post=fail, base='offline:')
    assert result['status'] == 'model_error'
    assert 'completion' not in result


# ═══ · budget compression ════════════════════════════════════════════════════════════════════════════════

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


# ═══ · budget models ═════════════════════════════════════════════════════════════════════════════════════
# Model choice and integrity checks are offline; inference is a separate CI job.

spec_budget_models = importlib.util.spec_from_file_location('budget_models', ROOT / 'scripts/budget_models.py')
models = importlib.util.module_from_spec(spec_budget_models)
spec_budget_models.loader.exec_module(models)


def test_model_choices_are_fixed_and_every_download_has_an_immutable_pin():
    catalog = json.loads((ROOT / models.CATALOG).read_text())
    assert len(models.select_models('all')) == len(catalog) == 7
    for key, model in catalog.items():
        assert models.select_models(key) == [key]
        assert len(model['revision']) == 40 and len(model['sha256']) == 64
        assert model['url'] == f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{model['filename']}"
        assert model['bytes'] > 0
    with pytest.raises(ValueError):
        models.select_models('not-in-catalog')
    mini = catalog['minicpm5-2b']
    assert (ROOT / mini['template']).is_file()
    assert mini['quality_gate'] is False
    assert catalog['qwen-1.5b']['quality_gate'] is True


def test_cached_bytes_are_verified_and_receipt_labels_the_model(tmp_path, monkeypatch):
    raw = b'fake test model, not live evidence'
    model = tmp_path / 'model.gguf'; model.write_bytes(raw)
    catalog = tmp_path / 'catalog.json'
    profile = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'template': None,
               'quality_gate': False, 'url': 'https://example.invalid/no-download'}
    catalog.write_text(json.dumps({'test': profile}))
    monkeypatch.setattr(models, 'CATALOG', catalog)
    receipt = models.prepare('test', model)
    assert receipt['sha256_verified'] and receipt['model_id'] == 'test'
    assert receipt['template_sha256'] is None
    model.write_bytes(raw[:-1] + b'!')
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        models.prepare('test', model)


# ═══ · budget quality ci ═════════════════════════════════════════════════════════════════════════════════
# Offline evidence validation; saved real-token receipts are not re-inference.

spec_budget_quality_ci = importlib.util.spec_from_file_location('budget_ci', ROOT / 'scripts/summarize_budget_quality.py')
ci = importlib.util.module_from_spec(spec_budget_quality_ci)
spec_budget_quality_ci.loader.exec_module(ci)
BUDGET_QUALITY_CI_CASES = (ROOT / 'fixtures/budget_compression/cases.json').read_bytes()
REPORT = json.loads((ROOT / 'docs/experiments/budget-quality/v3-overhead-paired.json').read_text())


def test_saved_paired_receipt_keeps_failures_and_denominators_separate():
    summary = ci.summarize(REPORT, BUDGET_QUALITY_CI_CASES)
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
        ci.summarize(report, BUDGET_QUALITY_CI_CASES)


@pytest.mark.parametrize('status', ['incomplete_output', 'wrong_action', 'abstained', 'model_error', 'measurement_error', 'tokenizer_unavailable', 'not_run'])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('label', ['before', 'after'])
def test_candidate_quality_is_observed_but_infrastructure_always_fails(tmp_path, monkeypatch, status, strict, label):
    report = copy.deepcopy(REPORT)
    report['cases'][0][label].update(status=status, task_success=False)
    receipt = tmp_path / 'paired.json'; receipt.write_text(json.dumps(report))
    model = tmp_path / 'model.gguf'
    model.with_suffix('.gguf.provenance.json').write_text(json.dumps({
        'model_id': 'candidate', 'quality_gate': strict, 'reasoning': 'off', 'template': None}))
    monkeypatch.setattr('sys.argv', ['summary', '--report', str(receipt), '--model', str(model),
                                   '--image', 'offline-test', '--summary', str(tmp_path / 'summary.md')])
    if (strict and label == 'after') or status in {'model_error', 'measurement_error', 'tokenizer_unavailable', 'not_run'}:
        with pytest.raises(SystemExit): ci.main()
    else:
        ci.main()
    provenance = json.loads((tmp_path / 'provenance.json').read_text())
    assert provenance['summary'][label]['successes'] == (19 if label == 'after' else 11)
    assert provenance['quality_gate'] is strict


# ═══ · cpu llm lite ══════════════════════════════════════════════════════════════════════════════════════

DEMOS = ROOT / "demos"
if str(DEMOS) not in sys.path:
    sys.path.insert(0, str(DEMOS))

import cpu_llm_lite as cpu  # noqa: E402


def test_cpu_lite_emits_tool_call_for_exampleville() -> None:
    message = cpu.decide(
        {
            "messages": [{"role": "user", "content": "Find municipalities named Exampleville"}],
            "tools": [{"type": "function", "function": {"name": "find_municipalities"}}],
        }
    )
    assert message["tool_calls"][0]["function"]["name"] == "find_municipalities"


def test_cpu_lite_plain_reply_without_tools() -> None:
    message = cpu.decide({"messages": [{"role": "user", "content": "hello"}]})
    assert message.get("tool_calls") is None
    assert "lab-cpu-lite" in message["content"]


# ═══ · fetch tiny cpu gguf ═══════════════════════════════════════════════════════════════════════════════
# scripts/fetch_tiny_cpu_gguf.py -- no real huggingface.co access in tests.

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_tiny_cpu_gguf as fetch  # noqa: E402


def test_default_repo_is_the_verified_one() -> None:
    # HuggingFaceTB/SmolLM2-135M-Instruct-GGUF (an earlier guess) does not
    # exist -- confirmed live via the Hugging Face Hub connector, which
    # also confirmed this one does (apache-2.0, a quantization of
    # HuggingFaceTB/SmolLM2-135M-Instruct). Regression guard against
    # reintroducing an unverified repo name as the default.
    assert fetch.DEFAULT_REPO == "bartowski/SmolLM2-135M-Instruct-GGUF"
    assert fetch.DEFAULT_FILE == "SmolLM2-135M-Instruct-Q4_K_M.gguf"
    assert fetch.DEFAULT_REVISION == "09816acd5d99df7be770d85ea30822623dab342c"
    assert fetch.DEFAULT_SHA256 == "2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d"
    assert fetch.DEFAULT_SIZE_BYTES == 105454432


def test_pick_file_prefers_smallest_pattern() -> None:
    siblings = [
        {"rfilename": "model-f16.gguf"},
        {"rfilename": "model-q4_k_m.gguf"},
        {"rfilename": "README.md"},
    ]
    assert fetch._pick_file(siblings, None) == "model-q4_k_m.gguf"


def test_pick_file_honors_explicit_choice() -> None:
    siblings = [{"rfilename": "a.gguf"}, {"rfilename": "b.gguf"}]
    assert fetch._pick_file(siblings, "b.gguf") == "b.gguf"


def test_pick_file_rejects_unknown_explicit_choice() -> None:
    siblings = [{"rfilename": "a.gguf"}]
    try:
        fetch._pick_file(siblings, "missing.gguf")
    except RuntimeError as exc:
        assert "missing.gguf" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_pick_file_raises_when_no_gguf() -> None:
    try:
        fetch._pick_file([{"rfilename": "README.md"}], None)
    except RuntimeError as exc:
        assert "no .gguf" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_discover_resolves_commit_and_filename(monkeypatch) -> None:
    payload = json.dumps(
        {"sha": "abc123", "siblings": [{"rfilename": "tiny-q4_k_m.gguf"}, {"rfilename": "README.md"}]}
    ).encode("utf-8")

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    commit, filename, url = fetch.discover("some/repo", None)
    assert commit == "abc123"
    assert filename == "tiny-q4_k_m.gguf"
    assert url == "https://huggingface.co/some/repo/resolve/abc123/tiny-q4_k_m.gguf"


def test_download_computes_sha256_and_writes_provenance(tmp_path, monkeypatch) -> None:
    content = b"not a real gguf, just bytes for the test"
    expected_sha = hashlib.sha256(content).hexdigest()

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(content)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "model.gguf"
    sha256, size = fetch._download("http://example.invalid/model.gguf", dest)
    assert sha256 == expected_sha
    assert size == len(content)
    assert dest.read_bytes() == content


def test_download_retries_then_raises(monkeypatch) -> None:
    calls = {"n": 0}

    def always_fails(request, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        raise URLError("no route")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", always_fails)
    monkeypatch.setattr(fetch, "RETRIES", 2)
    try:
        fetch._download("http://example.invalid/model.gguf", Path("/tmp/does-not-matter.gguf"))
    except RuntimeError as exc:
        assert "failed after 2 attempts" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    assert calls["n"] == 2


def test_main_verifies_expected_sha256_and_fails_loud(tmp_path, monkeypatch) -> None:
    content = b"tiny model bytes"

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(content)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CPU_LLM_GGUF_URL", "http://example.invalid/model.gguf")
    monkeypatch.setenv("CPU_LLM_GGUF_SHA256", "0" * 64)
    dest = tmp_path / "model.gguf"
    code = fetch.main([str(dest)])
    assert code == 3
    assert not dest.exists()


def test_main_default_repo_uses_pinned_url_and_default_sha(tmp_path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        seen["url"] = request.full_url
        return _FakeResponse(b"not the real gguf")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("CPU_LLM_GGUF_URL", raising=False)
    monkeypatch.delenv("CPU_LLM_GGUF_REPO", raising=False)
    monkeypatch.delenv("CPU_LLM_GGUF_FILE", raising=False)
    monkeypatch.delenv("CPU_LLM_GGUF_SHA256", raising=False)
    dest = tmp_path / "model.gguf"
    code = fetch.main([str(dest)])
    assert code == 3
    assert not dest.exists()
    assert fetch.DEFAULT_REVISION in seen["url"]
    assert fetch.DEFAULT_FILE in seen["url"]


def test_main_records_digest_when_no_pin_given(tmp_path, monkeypatch) -> None:
    content = b"tiny model bytes"
    expected_sha = hashlib.sha256(content).hexdigest()

    def fake_urlopen(request, timeout=0):  # noqa: ARG001
        return _FakeResponse(content)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CPU_LLM_GGUF_URL", "http://example.invalid/model.gguf")
    monkeypatch.delenv("CPU_LLM_GGUF_SHA256", raising=False)
    dest = tmp_path / "model.gguf"
    code = fetch.main([str(dest)])
    assert code == 0
    assert dest.read_bytes() == content
    provenance = json.loads(dest.with_suffix(".gguf.provenance.json").read_text(encoding="utf-8"))
    assert provenance["sha256"] == expected_sha
    assert provenance["sha256_verified_against_pin"] is False
    assert provenance["source"] == "explicit"


# ═══ · aoi gguf benchmark ════════════════════════════════════════════════════════════════════════════════
# Aoi labels are historical published outcomes, not independent confirmation.

def _synthetic_aoi_files(tmp_path: Path) -> tuple[Path, Path]:
    # Small synthetic source only. Live Actions checks out the pinned Aoi repo.
    index = tmp_path / "index.md"
    propositions = tmp_path / "propositions.toml"
    index.write_text(
        "# Published verdicts\n"
        + "".join(
            f"| {pid} | [all] test => true | mostly | 1.00 (8) | "
            f"**{code}** archived outcome |\n"
            for pid, code in EXPECTED_CODES.items()
        ), encoding="utf-8")
    propositions.write_text("".join(
        "[[proposition]]\n"
        f'id = "{pid}"\n'
        f'statement = "Test statement {pid}"\n'
        for pid in EXPECTED_CODES
    ), encoding="utf-8")
    return index, propositions


def _data(tmp_path):
    return catalog(*_synthetic_aoi_files(tmp_path), strict=False)


def _fake_post(path, payload):
    assert path == "/v1/chat/completions"
    assert payload["temperature"] == 0
    assert payload["cache_prompt"] is False
    assert payload["stream"] is False
    assert payload["response_format"]["type"] == "json_schema"
    messages = payload["messages"]
    user = [m["content"] for m in messages if m["role"] == "user"][-1]
    pid = re.search(r"TARGET_ID: (P\d+)", user).group(1)
    assert f"ID={pid} | overall_exit={EXPECTED_CODES[pid]}" in user
    answer = str(EXPECTED_CODES[pid])
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps({"exit_code": int(answer)})}}],
        "usage": {
            "prompt_tokens": max(1, len(json.dumps(messages, ensure_ascii=False)) // 3),
            "completion_tokens": 1,
        },
    }


def test_aoi_verdict_reader_and_consistent_source_identity(tmp_path):
    data = _data(tmp_path)
    assert data["registered_count"] == len(EXPECTED_CODES) == 12
    assert data["verdicts"] == EXPECTED_CODES
    assert all(len(d) == 64 for d in [data["index_sha256"], data["propositions_sha256"]])
    with pytest.raises(BenchmarkError, match="count"):
        catalog(*_synthetic_aoi_files(tmp_path))


def test_broken_or_missing_source_rejected(tmp_path):
    index, propositions = _synthetic_aoi_files(tmp_path)
    index.write_text(index.read_text().replace("| P2 |", "| P1 |"), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="duplicate"):
        catalog(index, propositions, strict=False)
    index, propositions = _synthetic_aoi_files(tmp_path)
    index.write_text(index.read_text().replace("**1** archived outcome", "**0** archived outcome", 1),
                     encoding="utf-8")
    with pytest.raises(BenchmarkError, match="changed for P2"):
        catalog(index, propositions, strict=False)
    index, propositions = _synthetic_aoi_files(tmp_path)
    propositions.write_text(propositions.read_text().replace('id = "P96"', 'id = "P997"'),
                            encoding="utf-8")
    with pytest.raises(BenchmarkError, match="do not match"):
        catalog(index, propositions, strict=False)


def test_compaction_preserves_real_user_turns_and_typed_tool_boundary(tmp_path):
    data = _data(tmp_path)
    for pid in EXPECTED_CODES:
        old, new, changed = scenario(data, pid)
        untouched = copy.deepcopy(old)
        assert old == untouched
        assert new[-1] == old[-1]
        assert changed is (pid not in REFUSED)
        assert len([m for m in old if m["role"] == "tool"]) == 3
        if changed:
            assert len(new) == 2
            assert all(m["role"] != "tool" for m in new)
        else:
            assert new is old
        if pid in ATTACKS:
            assert "Ignore the TARGET_ID" in json.dumps(old)
            assert "Ignore the TARGET_ID" not in json.dumps(new)


def test_paired_metrics_and_secret_free_artifact(tmp_path):
    data = _data(tmp_path)
    result = run(data, _fake_post, context_window=2048, repeats=2,
                 model_sha256="f" * 64)
    a = result["aggregate"]
    assert a["paired_samples"] == 24
    assert a["compacted_samples"] == 20
    assert a["declined_samples"] == 4
    assert a["quality_exact_original"] == a["quality_exact_reduced"] == 24
    assert a["quality_exact_by_compaction_status"]["refused"]["n"] == 4
    assert a["saved_prompt_tokens_compactable"] > 0
    assert 0 < a["token_reduction_fraction_compactable"] < 1
    assert a["attack_case_count"] == 4
    assert result["aoi_source_commit"]
    assert len(result["samples"]) == 24
    assert all(isinstance(s["original"]["predicted_exit_code"], int)
               for s in result["samples"])
    published = json.dumps(result, ensure_ascii=False)
    for forbidden in ("SYSTEM OVERRIDE", "Ignore the TARGET_ID",
                      "Test statement", "Preserve earlier user constraints",
                      "Old public Aoi record:", "read the Aoi adjudication"):
        assert forbidden.lower() not in published.lower()


def test_invalid_predictions_never_rewritten_as_valid_digit():
    for reply in ("exit=1", "4\nexplanation", "0 1", "unknown", "",
                  "{\"exit_code\": true}", "{\"exit_code\": 7}",
                  "{\"exit_code\": 1, \"exit_code\": 2}",
                  "{\"exit_code\": \"3\"}", "{\"exit_code\": 3, \"extra\": 1}"):
        assert _as_digit({"choices": [{"message": {"content": reply}}]}) is None
    assert _as_digit({"choices": [{"message": {"content": ' {"exit_code":3} '}}]}) == 3
    assert _as_digit({}) is None


def test_source_injection_cannot_change_latest_user_reference(tmp_path):
    data = _data(tmp_path)
    for pid in ATTACKS:
        original, reduced, compacted = scenario(data, pid)
        assert compacted
        assert any(m["role"] == "tool" and "role: system" in m["content"]
                   for m in original)
        assert all("role: system" not in m.get("content", "") for m in reduced)
        assert f"TARGET_ID: {pid}" in reduced[-1]["content"]


def test_repeats_and_context_fail_closed(tmp_path):
    data = _data(tmp_path)
    with pytest.raises(ValueError):
        run(data, _fake_post, context_window=2048, repeats=0, model_sha256="f"*64)
    with pytest.raises(BenchmarkError, match="context"):
        run(data, _fake_post, context_window=128, repeats=1, model_sha256="f"*64)


def test_all_scope_enumerates_every_registered_id(tmp_path):
    data = _data(tmp_path)
    result = run(data, _fake_post, context_window=2048, repeats=1,
                 model_sha256="a" * 64, scope="all")
    assert result["scope"] == "all"
    assert result["distinct_question_ids"] == len(EXPECTED_CODES)
    assert {s["case_id"] for s in result["samples"]} == set(EXPECTED_CODES)
    assert result["aggregate"]["compacted_samples"] == 10
    assert result["aggregate"]["declined_samples"] == 2
    assert result["aggregate"]["quality_improvements"] == 0
    assert result["aggregate"]["quality_regressions"] == 0
    assert result["aggregate"]["class_accuracies"]["4"]["n"] >= 1
    assert 0 < result["aggregate"]["majority_class_baseline_fraction"] <= 1


def test_invalid_scope_is_rejected(tmp_path):
    data = _data(tmp_path)
    with pytest.raises(ValueError, match="scope"):
        run(data, _fake_post, context_window=2048, repeats=1,
            model_sha256="a" * 64, scope="speculation")


# ═══ · model workflow gates ══════════════════════════════════════════════════════════════════════════════
# Offline CI policy checks: manual model validation is not a mocked CI pass.

MANUAL = "github.event_name == 'workflow_dispatch'"
PORTABLE = {
    "name": "portable-build",
    "dockerfile": "docker/portable-pages/Dockerfile",
    "target": "build",
    "trust_args": "",
}


def _workflows():
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".github/workflows").glob("*.yml")
    }


def _events(workflow):
    # PyYAML's YAML 1.1 loader treats GitHub's unquoted `on` key as True.
    return workflow.get("on", workflow.get(True, {}))


def _assert_no_automatic_models(workflows):
    runtime = workflows["component-runtime.yml"]
    assert set(_events(runtime)) == {"pull_request", "workflow_dispatch"}
    automatic = runtime["jobs"]["runtime"]["strategy"]["matrix"]["include"]
    assert {row["name"] for row in automatic} == {
        "librechat-mcp", "openwebui-mcp", "openai-mock", "stub-cpu", "stub-front",
    }
    assert all(row["dockerfile"] != PORTABLE["dockerfile"] for row in automatic)
    live = runtime["jobs"]["portable-live"]
    assert live["if"] == MANUAL  # Job-level skip, not successful skipped steps.
    assert live["strategy"]["matrix"]["include"] == [PORTABLE]
    assert not live.get("continue-on-error", False)


def test_all_model_routes_are_manual_and_no_workflow_publishes_pages():
    # The manual-only pages.yml was removed (2026-11-13): nothing here deploys Pages or uploads a Pages artifact.
    workflows = _workflows()
    _assert_no_automatic_models(workflows)
    for filename, workflow in workflows.items():
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                assert not step.get("uses", "").startswith(("actions/deploy-pages@", "actions/upload-pages-artifact@")), filename


@pytest.mark.parametrize("damage", ["runtime_matrix", "runtime_job"])
def test_policy_rejects_each_reopened_automatic_model_route(damage):
    workflows = deepcopy(_workflows())
    runtime = workflows["component-runtime.yml"]
    if damage == "runtime_matrix":
        runtime["jobs"]["runtime"]["strategy"]["matrix"]["include"].append(PORTABLE)
    else:
        runtime["jobs"]["portable-live"]["if"] = "always()"
    with pytest.raises(AssertionError):
        _assert_no_automatic_models(workflows)


def test_manual_runtime_retains_real_build_trust_and_original_artifact_contract():
    jobs = _workflows()["component-runtime.yml"]["jobs"]
    automatic = {step["name"]: step for step in jobs["runtime"]["steps"] if "name" in step}
    manual = {step["name"]: step for step in jobs["portable-live"]["steps"] if "name" in step}
    assert jobs["portable-live"]["name"] == "runtime (portable-build, docker/portable-pages/Dockerfile, build)"
    for name in automatic:
        assert manual[name] == automatic[name]
    assert "docker build --pull --no-cache" in manual["Rebuild pinned runtime with no layer cache"]["run"]
    assert "--live" in manual["Verify actual container trust and installed component versions"]["run"]
    assert "node --version" in manual["Record portable Node version"]["run"]
    assert manual["Preserve runtime receipts even on failure"]["with"]["name"] == "runtime-${{ matrix.name }}"
    assert "browser-protocol" in jobs


def test_matrix_setup_precedes_bare_python_and_keeps_exact_pins_and_reports():
    workflows = _workflows()
    job = workflows["ci.yml"]["jobs"]["test"]
    setup_index = next(i for i, step in enumerate(job["steps"])
                       if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    setup = job["steps"][setup_index]["with"]
    assert setup["version"] == "latest"
    assert setup["activate-environment"] is True
    assert setup["enable-cache"] is True
    assert setup["save-cache"] == "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}"  # only main writes
    assert setup["prune-cache"] is True
    for index, step in enumerate(job["steps"]):
        if step.get("run", "").lstrip().startswith("python "):
            assert setup_index < index
    assert job["strategy"]["matrix"]["include"] == [
        {"python-version": "3.12.15", "node-version": "26.12.1"},
        {"python-version": "3.15", "node-version": "26.12.1"},
    ]
    assert workflows["jsonl-archive.yml"]["jobs"]["archive"]["strategy"]["matrix"]["python"] == ["3.12.15", "3.13.17", "3.14.9"]
    reports = workflows["ci.yml"]["jobs"]["failure-identity"]["with"]["expected-reports"]
    for row in job["strategy"]["matrix"]["include"]:
        assert f"junit-python-{row['python-version']}-node-{row['node-version']}/pytest.xml" in reports
