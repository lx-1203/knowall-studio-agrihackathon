"""Verify recorded checks and save source/artifact SHA evidence for this round."""
import difflib
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
root = Path(__file__).resolve().parents[1]
evidence = root / 'docs/patrol/ui02-01a12efd'
inputs = json.loads((evidence / 'build-inputs.json').read_text(encoding='utf-8'))
changed = [name for name, value in inputs.items() if hashlib.sha256((root / name).read_bytes()).hexdigest() != value]
assert not changed, 'Shared build inputs changed: ' + repr(changed)
for theme in ['light', 'dark']:
    for width in ['320', '390']:
        for case in ['upload', 'review', 'study', 'profile']:
            name = 'final-' + case + '-' + theme + '-' + width + '.txt'
            assert (evidence / name).read_text(encoding='utf-8-sig').startswith('PASS '), name
    for name in ['http-flow-final-' + theme + '.txt', 'reading-regression-' + theme + '-final.txt']:
        assert (evidence / name).read_text(encoding='utf-8-sig').startswith('PASS '), name
    trace = json.loads((evidence / ('http-trace-' + theme + '.json')).read_text(encoding='utf-8'))
    assert trace['server']['ai_calls'] == 0 and not trace['errors']
    for url in trace['runtimeBundles']:
        assert (root / 'dist-calm-shell-web-01a12efd' / url.split(':8914/')[1]).is_file(), 'Stale runtime bundle: ' + url
assert not (evidence / 'typecheck-app-final.txt').read_text(encoding='utf-8-sig').strip()
contracts = (evidence / 'contracts-final.txt').read_text(encoding='utf-8-sig')
test_count = int(re.search(r'tests (\d+)', contracts).group(1))
assert 'fail 0' in contracts and 'pass ' + str(test_count) in contracts
for name, folder in [('build-web-final.txt', 'dist-calm-shell-web-01a12efd'), ('build-android-final.txt', 'dist-calm-shell-android-01a12efd')]:
    assert 'Exported: ' + folder in (evidence / name).read_text(encoding='utf-8-sig')
diff = []
for name in ['upload.tsx', 'review.tsx', 'study.tsx', 'profile.tsx', '_layout.tsx']:
    before = (evidence / 'before' / (name + '.txt')).read_text(encoding='utf-8').splitlines(True)
    after = (root / 'app/(tabs)' / name).read_text(encoding='utf-8').splitlines(True)
    diff.extend(difflib.unified_diff(before, after, fromfile='before/app/(tabs)/' + name, tofile='app/(tabs)/' + name))
(evidence / 'product.diff').write_text(''.join(diff), encoding='utf-8')
files = {root / name for name in inputs}
files.update(p for p in evidence.rglob('*') if p.is_file() and p.name != 'manifest.json')
for folder in ['dist-calm-shell-web-01a12efd', 'dist-calm-shell-android-01a12efd']:
    files.update(p for p in (root / folder).rglob('*') if p.is_file())
files.update(root / 'tests' / name for name in ['mobile-shell-before.mjs', 'mobile-shell-flow.mjs', 'mobile-shell-fixture.mjs', 'mobile-shell-http.py', 'mobile-shell-real-flow.mjs', 'mobile-shell-source-manifest.py', 'mobile-shell-web-server.py', 'mobile-shell-evidence.py', 'mobile-shell-tsconfig.json', 'material-calm-ui.mjs'])
manifest = {
    'round': 'MOBILE-UX-02/01a12efd', 'generated_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'review': 'Full with OCR delegate scope/rules and manual validation; no full repository LLM scan',
    'build_inputs': len(inputs), 'changed_inputs': changed, 'contract_tests_passed': test_count,
    'ui_matrix_passed': 16, 'real_http_flows_passed': 2, 'reading_regressions_passed': 2,
    'application_typecheck_exit': 0, 'workspace_typecheck_exit': 1, 'web_export_exit': 0, 'android_export_exit': 0,
    'limits': ['No APK or native device validation', 'No production account or paid AI', 'Other tool child pages not redesigned in this round', 'Global tsc blocked by other chat browser fixture react-dom/client declarations; application source tsc passed'],
    'files': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
}
(evidence / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({ 'contract_tests': test_count, 'ui': 16, 'http': 2, 'reading': 2, 'sha_files': len(files), 'changed_inputs': changed }, ensure_ascii=False))
