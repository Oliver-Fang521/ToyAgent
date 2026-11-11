import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_automatic_initialization_is_idempotent_and_preserves_keys(tmp_path):
    root = tmp_path / 'secrets'
    cmd = [sys.executable, str(ROOT / 'deploy/local_runtime.py'), 'auto-init', '--root', str(root)]
    first = subprocess.run(cmd, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    before = (root / 'frontend/runtime.json').read_bytes()
    second = subprocess.run(cmd, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert (root / 'frontend/runtime.json').read_bytes() == before

def test_automatic_initialization_refuses_partial_secrets(tmp_path):
    root = tmp_path / 'secrets'
    root.mkdir()
    (root / 'incomplete').write_text('keep')
    result = subprocess.run([sys.executable, str(ROOT / 'deploy/local_runtime.py'), 'auto-init', '--root', str(root)], capture_output=True, text=True)
    assert result.returncode != 0
    assert (root / 'incomplete').read_text() == 'keep'
