"""Record build inputs and compare them after export in a shared checkout."""
import hashlib
import json
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
output = root / 'docs/patrol/ui02-01a12efd/build-inputs.json'
paths = []
for folder in ['app', 'src', 'assets']:
    paths.extend(p for p in (root / folder).rglob('*') if p.is_file())
paths.extend(root / name for name in ['package.json', 'package-lock.json', 'app.json', 'tsconfig.json', 'babel.config.js', 'metro.config.js'] if (root / name).is_file())
current = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
if '--check' in sys.argv:
    previous = json.loads(output.read_text(encoding='utf-8'))
    changed = [name for name in sorted(set(previous) | set(current)) if previous.get(name) != current.get(name)]
    print(json.dumps({'inputs': len(current), 'changed_since_export_start': changed}, ensure_ascii=False))
    sys.exit(1 if changed else 0)
else:
    output.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Recorded ' + str(len(current)) + ' build inputs')
