"""Validate logs/runtime identity and record this round's source/artifact hashes."""
import difflib
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
root = Path(__file__).resolve().parents[1]
folder = root / 'docs/patrol/ui03-01a12efd'
inputs = json.loads((folder / 'build-inputs.json').read_text(encoding='utf-8'))
changed = [name for name, digest in inputs.items() if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
assert not changed, changed
logs = []
for theme in ['light', 'dark']:
    for width in ['320', '390']:
        for page in ['quiz', 'flashcards', 'recitation', 'chat']:
            logs.append('final-' + page + '-' + theme + '-' + width + '.txt')
    logs += ['draft-' + theme + '.txt', 'http-final-' + theme + '.txt', 'reading-' + theme + '.txt']
    trace = json.loads((folder / ('http-trace-' + theme + '.json')).read_text(encoding='utf-8'))
    assert trace['server']['real_ai_calls'] == 0 and trace['server']['stub_replies'] == 1
    assert all(r['status'] < 400 for r in trace['requests'])
    for bundle in trace['bundles']:
        assert (root / 'dist-calm-tools-web-01a12efd' / bundle.split(':8916/')[1]).is_file(), bundle
logs += ['parent-' + page + '-dark.txt' for page in ['upload', 'review', 'study', 'profile']]
for name in logs:
    assert (folder / name).read_text(encoding='utf-8-sig').startswith('PASS '), name
assert not (folder / 'typecheck-app.txt').read_text(encoding='utf-8-sig').strip()
contract = (folder / 'contracts.txt').read_text(encoding='utf-8-sig')
count = int(re.search(r'tests (\d+)', contract).group(1))
assert 'pass ' + str(count) in contract and 'fail 0' in contract
for log, target in [('build-web.txt', 'dist-calm-tools-web-01a12efd'), ('build-android.txt', 'dist-calm-tools-android-01a12efd')]:
    assert 'Exported: ' + target in (folder / log).read_text(encoding='utf-8-sig')
diff = []
for name in ['quiz.tsx', 'flashcards.tsx', 'recitation.tsx', 'chat.tsx', '_layout.tsx']:
    old = (folder / 'before' / (name + '.txt')).read_text(encoding='utf-8').splitlines(True)
    new = (root / 'app/(tabs)' / name).read_text(encoding='utf-8').splitlines(True)
    diff += difflib.unified_diff(old, new, fromfile='before/app/(tabs)/' + name, tofile='app/(tabs)/' + name)
(folder / 'product.diff').write_text(''.join(diff), encoding='utf-8')
files = {root / name for name in inputs}
files.update(p for p in folder.rglob('*') if p.is_file() and p.name != 'manifest.json')
for target in ['dist-calm-tools-web-01a12efd', 'dist-calm-tools-android-01a12efd']:
    files.update(p for p in (root / target).rglob('*') if p.is_file())
files.update(p for p in (root / 'tests').glob('mobile-tools-*') if p.is_file())
files.update(root / 'tests' / name for name in ['quiz-session.test.mjs', 'mobile-shell-flow.mjs', 'material-calm-ui.mjs', 'mobile-shell-tsconfig.json'])
workspace_errors = (folder / 'typecheck-workspace.txt').read_text(encoding='utf-8-sig').strip()
manifest = {
    'round': 'MOBILE-UX-03/01a12efd', 'created_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'review': 'Full; OCR delegate scope/rules and manual review of this task, no independent repository LLM scan',
    'contract_tests': count, 'browser_flows': len(logs), 'ui_matrix': 16, 'draft_fault_flows': 2,
    'real_http_flows': 2, 'parent_regressions': 4, 'reading_regressions': 2,
    'build_inputs': len(inputs), 'source_changes_since_build': changed, 'application_typecheck': 0,
    'workspace_typecheck': 1 if workspace_errors else 0, 'web_export': 0, 'android_resource_export': 0,
    'limits': ['No native device/APK validation', 'No paid model; chat model reply replaced with deterministic test function', 'Workspace typecheck errors, if present, are recorded without claiming a pass'],
    'files': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
}
(folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'contracts': count, 'browser_flows': len(logs), 'sha_files': len(files), 'changed': changed, 'workspace_typecheck': manifest['workspace_typecheck']}, ensure_ascii=False))
