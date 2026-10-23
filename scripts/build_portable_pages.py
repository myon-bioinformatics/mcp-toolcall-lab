"""Static export, optionally generating declared summaries in an owned local model."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from mcp_toolcall_lab.adapters.engine_adapter import distill_engine_adapter, export_adapter_cache, extract_engine_spec
from mcp_toolcall_lab.catalog import dispatch_tool
from mcp_toolcall_lab.portable_execution import CONTRACT, execute, request_sha256
from mcp_toolcall_lab.record import record_call
from mcp_toolcall_lab.portable_summary_build import build_summaries
from write_portfolio_redirect import write_redirect

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'src/mcp_toolcall_lab/static'


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def build(out: Path, source_sha: str, *, model_path: Path | None = None, llama_server: Path | None = None,
          reviewed_corpus: Path | None = None, result_cache: Path | None = None, case_offset: int = 0, case_limit: int = 16) -> None:
    if not source_sha or len(source_sha) != 40 or any(c not in '0123456789abcdef' for c in source_sha):
        raise ValueError('source_sha must be the exact 40-character source commit')
    target = out / 'execution'
    if target.exists():
        raise ValueError('Output execution directory already exists; use a fresh output to avoid stale build receipts.')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('Static output must be a fresh empty directory; existing files may contain private data.')
    for private in (reviewed_corpus, result_cache, model_path, llama_server):
        if private is not None and Path(private).resolve().is_relative_to(out.resolve()):
            raise ValueError('Model, executable, reviewed corpus and result cache must stay outside the public output directory.')
    write_redirect(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'results').mkdir(exist_ok=True)
    # The CLI package is directly reusable, not a Pages-specific fork. No models
    # or private log files are copied; only allowlisted source/dependencies/fixtures.
    runtime = target / 'runtime'
    shutil.copytree(ROOT / 'src/mcp_toolcall_lab', runtime / 'src/mcp_toolcall_lab', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'), dirs_exist_ok=True)
    # Existing markdown_lib resolves vendor relative to the source package.
    shutil.copytree(ROOT / 'vendor', runtime / 'vendor', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'), dirs_exist_ok=True)
    shutil.copyfile(ROOT / 'LICENSE', runtime / 'LICENSE')
    shutil.copyfile(ROOT / 'vendor.lock.json', runtime / 'vendor.lock.json')
    shutil.copyfile(ROOT / 'docs/portable-execution.md', runtime / 'README.md')
    (runtime / 'fixtures/portable_summary').mkdir(parents=True)
    shutil.copyfile(ROOT / 'fixtures/portable_summary/public_sources.json', runtime / 'fixtures/portable_summary/public_sources.json')
    for name in ('build-time-summaries.md', 'summary-corpus.md'):
        shutil.copyfile(ROOT / 'docs' / name, runtime / name)
    for source, destination in [('portable_page.html', 'index.html'), ('portable_page.mjs', 'portable_page.mjs'), ('terminal.css', 'terminal.css')]:
        shutil.copyfile(STATIC / source, target / destination)
    # Reuse the README branding byte-for-byte, with execution-local URLs.
    (target / 'assets').mkdir()
    for name in ('mcp-toolcall-lab-logo.png', 'mcp-toolcall-lab-favicon-artwork.png'):
        shutil.copyfile(ROOT / 'docs/assets' / name, target / 'assets' / name)
    meta = {'schema': 'lab-portable-build/1', 'source_sha': source_sha, 'source_url': f'https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/{source_sha}', 'build_run': os.environ.get('GITHUB_RUN_ID') or None, 'build_attempt': os.environ.get('GITHUB_RUN_ATTEMPT') or None, 'execution': 'build-time deterministic mock functions; no MCP transport or model inference', 'runtime': {'python': subprocess.check_output(['python3', '--version'], text=True).strip(), 'node': subprocess.check_output(['node', '--version'], text=True).strip()}}
    with tempfile.TemporaryDirectory() as temporary:
        log = Path(temporary) / 'calls.jsonl'
        previous = os.environ.get('MCP_TOOLCALL_LOG')
        os.environ['MCP_TOOLCALL_LOG'] = str(log)
        try:
            for query in ('Exampleville', 'not-a-known-municipality'):
                result = dispatch_tool('find_municipalities', {'query': query})
                record_call(tool='find_municipalities', arguments={'query': query}, outcome='success' if result else 'empty', result=result, meta={'source': 'build-time deterministic mock; no MCP transport'})
        finally:
            if previous is None:
                os.environ.pop('MCP_TOOLCALL_LOG', None)
            else:
                os.environ['MCP_TOOLCALL_LOG'] = previous
        jsonl = log.read_text(encoding='utf-8')
        (target / 'example.jsonl').write_text(jsonl, encoding='utf-8')
        request = {'schema': CONTRACT, 'operation': 'trace_summary', 'input': {'jsonl': jsonl}}
        summary = execute(request)
        if summary['status'] != 'ok':
            raise RuntimeError(summary)
        adapter = distill_engine_adapter(extract_engine_spec(engine_name='public-log-metrics', source_type='local', repo_or_image='example.jsonl', revision_or_tag=source_sha), {'public-example': {'metrics': summary['metrics'], 'confidence': None, 'log_sha256': summary['provenance']['input_sha256']}}, adapter_id='public-log-metrics')
        cache = export_adapter_cache(adapter, target / 'example-adapter.json')
        replay = {'schema': CONTRACT, 'operation': 'adapter_lookup', 'input': {'selector': {'query': 'public-example'}}}
        for req, filename in [(request, 'example-request.json'), (replay, 'adapter-request.json')]:
            receipt = execute(req, adapter_path=cache)
            if receipt['status'] != 'ok':
                raise RuntimeError(receipt)
            write_json(target / filename, req)
            key = request_sha256(req)
            write_json(target / 'results' / f'{key}.json', {'request': req, 'request_sha256': key, 'receipt': receipt, 'build': meta})
    summary_manifest = build_summaries(target, source_sha=source_sha, model_path=model_path, llama_server=llama_server,
                                      reviewed_corpus=reviewed_corpus, result_cache=result_cache, case_offset=case_offset, case_limit=case_limit)
    meta['summaries'] = {'status': summary_manifest['status'], 'declared_requests': len(summary_manifest['entries']),
                         'model_inference_performed': summary_manifest['model_inference_performed'],
                         'new_generations': summary_manifest['new_generations'], 'reused_results': summary_manifest['reused_results']}
    meta['execution'] = (f"build-time deterministic mock functions; no MCP transport; summaries: {summary_manifest['status']}; "
                         f"new local llama.cpp generations: {summary_manifest['new_generations']}; "
                         f"verified prior results reused: {summary_manifest['reused_results']}")
    # Archive has the same public runtime, useful without Pages or a running server.
    with zipfile.ZipFile(target / 'runtime.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(runtime.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(runtime))
    meta['assets'] = {str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(target.rglob('*')) if path.is_file() and path.name != 'build.json'}
    write_json(target / 'build.json', meta)
    (out / '.nojekyll').touch()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--source-sha', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--model', type=Path, help='Verified pinned local GGUF, used only during this build')
    parser.add_argument('--llama-server', type=Path, help='Pinned local llama-server executable, never hosted by Pages')
    parser.add_argument('--reviewed-corpus', type=Path, help='Explicitly reviewed public JSONL/archive corpus, not browser uploads')
    parser.add_argument('--result-cache', type=Path, help='Local append-only result store, reused only by exact recipe key')
    parser.add_argument('--case-offset', type=int, default=0)
    parser.add_argument('--case-limit', type=int, default=16)
    args = parser.parse_args()
    build(args.out, args.source_sha, model_path=args.model, llama_server=args.llama_server,
          reviewed_corpus=args.reviewed_corpus, result_cache=args.result_cache, case_offset=args.case_offset, case_limit=args.case_limit)
