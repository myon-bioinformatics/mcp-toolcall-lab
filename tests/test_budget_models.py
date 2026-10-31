"""Model choice and integrity checks are offline; inference is a separate CI job."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('budget_models', ROOT / 'scripts/budget_models.py')
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)


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
