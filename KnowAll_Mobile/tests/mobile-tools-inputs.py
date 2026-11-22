"""Capture all application build inputs, detect edits made during export."""
import hashlib
import json
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
target = root / 'docs/patrol/ui03-01a12efd/build-inputs.json'
files = [p for folder in ['app', 'src', 'assets'] for p in (root / folder).rglob('*') if p.is_file()]
files += [root / name for name in ['package.json', 'package-lock.json', 'app.json', 'tsconfig.json', 'babel.config.js', 'metro.config.js'] if (root / name).exists()]
current = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
if '--check' in sys.argv:
    previous = json.loads(target.read_text(encoding='utf-8'))
    changed = [name for name in set(current) | set(previous) if previous.get(name) != current.get(name)]
    print(json.dumps({'input_count': len(current), 'changed': changed}, ensure_ascii=False))
    sys.exit(bool(changed))
target.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
print('Recorded ' + str(len(current)) + ' inputs')
