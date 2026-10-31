"""Paired 800/512 comparison; explicit running llama-server, no downloads."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from mcp_toolcall_lab import budget_compression as new


def evaluate_with_completion(mod, case, *, count, post, base):
    """Keep generation-cap observations separate from malformed server replies."""
    completion = {}
    capped = False

    def answer(text):
        nonlocal capped
        response = post(base + '/v1/chat/completions', {
            'messages': [{'role': 'user', 'content': text}],
            'temperature': 0, 'seed': 42, 'max_tokens': 128, 'stream': False})
        completion['response'] = response
        choices = response.get('choices') if isinstance(response, dict) else None
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError('expected one completion choice')
        choice = choices[0]
        reason = choice.get('finish_reason')
        completion['finish_reason'] = reason
        message = choice.get('message')
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise ValueError('expected textual completion content')
        completion['raw_response'] = message['content']
        if message.get('tool_calls') or message.get('function_call'):
            raise ValueError('unexpected tool/function completion')
        if reason == 'length':
            capped = True
            raise ValueError('output token limit reached')
        if reason != 'stop':
            raise ValueError(f'unexpected completion finish_reason: {reason!r}')
        return message['content']

    result = mod.evaluate(case, max_chars=800, max_tokens=512, token_counter=count, answer=answer)
    if completion:
        result['completion'] = completion
    if capped and result['status'] == 'model_error':
        result['status'] = 'incomplete_output'
    return result


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--baseline', required=True, help='immutable git commit')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    code = subprocess.check_output(['git', 'show', args.baseline + ':src/mcp_toolcall_lab/budget_compression.py'])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'old.py'; path.write_bytes(code)
        spec = importlib.util.spec_from_file_location('mcp_toolcall_lab._baseline', path)
        old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    raw = Path('fixtures/budget_compression/cases.json').read_bytes()
    cases = json.loads(raw)
    base = args.base_url.rstrip('/')
    def count(text):
        return len(new._post(base + '/tokenize', {'content': text, 'add_special': False, 'parse_special': False})['tokens'])
    rows = []
    for case in cases:
        row = {'case_id': case['id']}
        for label, mod in [('before', old), ('after', new)]:
            result = evaluate_with_completion(mod, case, count=count, post=new._post, base=base)
            result['all_candidates'] = [{**c, 'chars': len(mod.prompt_for(case, c['text'])),
                'tokens': count(mod.prompt_for(case, c['text']))} for c in mod.candidates(case)]
            row[label] = result
        rows.append(row)
        print(case['id'], row['before']['status'], row['after']['status'], flush=True)
    first = cases[0]
    parts = {}
    for label, mod in [('before', old), ('after', new)]:
        full = mod.prompt_for(first, '')
        header, catalog = full.split('\n', 1)
        parts[label] = {'instruction': len(header), 'catalogue_and_framing': len(catalog) + 1,
                        'total': len(full),
                        'tool_lines': [{'text': line, 'chars': len(line)}
                                       for line in full.splitlines()[2:-1]]}
    report = {'baseline_commit': args.baseline, 'corpus_sha256': hashlib.sha256(raw).hexdigest(),
        'measurement': 'complete_prompt_text_without_chat_template', 'seed': 42, 'temperature': 0,
        'max_output_tokens': 128, 'overhead': parts, 'cases': rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

if __name__ == '__main__':
    main()
