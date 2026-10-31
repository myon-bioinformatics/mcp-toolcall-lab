"""Pinned, explicit model selection and verified cache/download for budget CI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

CATALOG = Path('fixtures/budget_compression/models.json')


def select_models(choice):
    models = json.loads(CATALOG.read_text())
    if choice == 'all':
        return list(models)
    if choice not in models:
        raise ValueError('unknown model choice')
    return [choice]


def prepare(choice, dest):
    model = json.loads(CATALOG.read_text())[choice]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        subprocess.run([sys.executable, 'scripts/fetch_tiny_cpu_gguf.py', str(dest)], check=True,
                       env={**os.environ, 'CPU_LLM_GGUF_URL': model['url'],
                            'CPU_LLM_GGUF_SHA256': model['sha256']})
    with dest.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != model['sha256'] or dest.stat().st_size != model['bytes']:
        raise ValueError('model size/SHA256 mismatch (including cached bytes)')
    template = model['template']
    receipt = {**model, 'model_id': choice, 'sha256_verified': True,
               'template_sha256': hashlib.sha256(Path(template).read_bytes()).hexdigest() if template else None,
               'reasoning': 'off',
               'template_note': 'MiniCPM5 text-only compatibility override, not default template' if template else 'upstream GGUF template'}
    dest.with_suffix('.gguf.provenance.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('choice')
    p.add_argument('--prepare', type=Path)
    p.add_argument('--github-output', type=Path)
    args = p.parse_args()
    selected = select_models(args.choice)
    if args.prepare:
        if len(selected) != 1:
            p.error('prepare requires one model')
        prepare(selected[0], args.prepare)
    if args.github_output:
        with args.github_output.open('a') as stream:
            stream.write('matrix=' + json.dumps({'model': selected}, separators=(',', ':')) + '\n')
    print(json.dumps(selected))


if __name__ == '__main__':
    main()
