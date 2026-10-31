"""Validate paired CI evidence and render the Actions summary (stdlib only)."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


def summarize(report, cases):
    if report['corpus_sha256'] != hashlib.sha256(cases).hexdigest():
        raise ValueError('corpus hash mismatch')
    expected = [case['id'] for case in json.loads(cases)]
    if len(expected) != 20 or [row['case_id'] for row in report['cases']] != expected:
        raise ValueError('expected all 20 ordered cases')
    results = {}
    for label in ('before', 'after'):
        rows = [row[label] for row in report['cases']]
        for row in rows:
            if (row['max_chars'], row['max_tokens']) != (800, 512):
                raise ValueError('incorrect budget')
            selected = row['selected']
            if selected is not None:
                if (type(selected['tokens']) is not int or not 0 <= selected['tokens'] <= 512
                        or len(selected['prompt']) != selected['chars']
                        or selected['chars'] > 800 or selected['missing_anchors']):
                    raise ValueError('invalid selected input measurement')
        results[label] = {
            'statuses': dict(Counter(row['status'] for row in rows)),
            'successes': sum(row.get('task_success') is True for row in rows),
            'selected_tiers': dict(Counter(row['selected']['candidate'] for row in rows if row['selected'])),
            'preferred_hits': sum(row.get('quality_hits', 0) for row in rows),
            'preferred_total': sum(row.get('quality_total', 0) for row in rows),
        }
    return results


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--image', required=True)
    p.add_argument('--summary', type=Path, required=True)
    args = p.parse_args()
    report = json.loads(args.report.read_text())
    cases = Path('fixtures/budget_compression/cases.json').read_bytes()
    result = summarize(report, cases)
    provenance = {
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'source_sha256': hashlib.sha256(Path('src/mcp_toolcall_lab/budget_compression.py').read_bytes()).hexdigest(),
        'baseline_commit': report['baseline_commit'],
        'model': json.loads(args.model.with_suffix('.gguf.provenance.json').read_text()),
        'runtime_image': args.image,
        'server': {'context': 4096, 'threads': 2, 'batch_threads': 2, 'gpu_layers': 0,
                   'parallel': 1, 'cpus': 2, 'memory': '6g', 'jinja': True, 'reasoning_format': 'none'},
        'generation': {'seed': 42, 'temperature': 0, 'max_output_tokens': 128},
        'measurement': report['measurement'],
        'summary': result,
        'note': 'Docker runtime differs from historical native v2/v3; both sides rerun on this same pinned image.'
    }
    args.report.with_name('provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    lines = ['## Qwen2.5-1.5B Q4_K_M: 800 characters / 512 tokens', '',
             '| Version | Success / planned | Statuses | Selected tiers | Preferred hits / scored |',
             '| --- | --- | --- | --- | --- |']
    for label, row in result.items():
        lines.append(f"| {label} | {row['successes']} / 20 | {row['statuses']} | {row['selected_tiers']} | "
                     f"{row['preferred_hits']} / {row['preferred_total']} |")
    lines += ['', 'Real GGUF inference; complete prompt text excludes chat-template overhead.',
              'Both versions use the same digest-pinned Docker runtime; see provenance.json.', '']
    with args.summary.open('a') as f:
        f.write('\n'.join(lines))
    if any(row['before']['status'] in {'model_error', 'measurement_error', 'tokenizer_unavailable', 'not_run'}
           for row in report['cases']):
        raise SystemExit('baseline infrastructure/measurement failure; see evidence')
    if any(row['after']['status'] != 'success' or row['after'].get('task_success') is not True
           for row in report['cases']):
        raise SystemExit('not all 20 current-version tasks succeeded; see evidence')


if __name__ == '__main__':
    main()
