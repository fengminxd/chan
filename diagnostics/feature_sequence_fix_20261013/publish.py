"""Publish the verified local report with pre-v8 backups and byte checks."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
STAGE = ROOT/'reports/BTCUSDT_20260606_20261013_features_v8'
assert json.loads((OUT/'validation.json').read_text())['passed']
assert json.loads((STAGE/'browser_validation.json').read_text())['passed']
manifest = json.loads((STAGE/'code_manifest.json').read_text())
assert manifest['rule_manifest']['version'] == 'chan-rules-v8'
for name, digest in manifest['sha256'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest
log = (OUT/'unittest.log').read_text()
assert 'Ran 257 tests' in log and log.rstrip().endswith('OK')
assert not (STAGE/'browser_error.json').exists()
records = []
for name in ('BTCUSDT_20260606_20261013_five_periods', 'BTCUSDT_20260606_20261013_causal_v6'):
    target = ROOT/'reports'/name
    backup = target.with_name(name+'_before_v8')
    if not backup.exists():
        shutil.copytree(target, backup)
    original_hash = hashlib.sha256((backup/'btc_tradingview.html').read_bytes()).hexdigest()
    shutil.copytree(STAGE, target, dirs_exist_ok=True)
    files = [p for p in STAGE.rglob('*') if p.is_file()]
    for p in files:
        assert p.read_bytes() == (target/p.relative_to(STAGE)).read_bytes()
    records.append(dict(updated=str(target.relative_to(ROOT)), backup=str(backup.relative_to(ROOT)),
                        old_html_sha256=original_hash,
                        new_html_sha256=hashlib.sha256((target/'btc_tradingview.html').read_bytes()).hexdigest(),
                        verified_files=len(files), all_published_files_identical=True))
(OUT/'publication.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(records, ensure_ascii=False, indent=2))
