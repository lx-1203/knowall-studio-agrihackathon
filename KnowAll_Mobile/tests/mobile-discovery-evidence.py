"""Capture build inputs, verify final evidence, and hash owned artifacts."""
import difflib
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
root=Path(__file__).resolve().parents[1]
folder=root/'docs/patrol/ui04-01a12efd'
hash_file=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
files=[p for name in ['app','src','assets'] for p in (root/name).rglob('*') if p.is_file()]
files += [root/name for name in ['package.json','package-lock.json','app.json','tsconfig.json','babel.config.js','metro.config.js'] if (root/name).exists()]
inputs={p.relative_to(root).as_posix():hash_file(p) for p in sorted(files)}
target=folder/'build-inputs.json'
if '--capture' in sys.argv:
    target.write_text(json.dumps(inputs,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Captured '+str(len(inputs))+' build inputs')
    sys.exit(0)
before=json.loads(target.read_text(encoding='utf-8'))
changed=[name for name in set(before)|set(inputs) if before.get(name)!=inputs.get(name)]
assert not changed,changed
if '--check' in sys.argv:
    print(json.dumps({'inputs':len(inputs),'changed':changed}));sys.exit(0)
logs=[]
for theme in ['light','dark']:
    for width in [320,390]:
        logs += [f'final-{page}-{theme}-{width}.txt' for page in ['search','knowledge']]
    logs += [f'final-edge-{theme}.txt',f'http-final-{theme}.txt',f'reading-{theme}.txt']
    trace=json.loads((folder/f'http-trace-{theme}.json').read_text(encoding='utf-8'))
    assert trace['health']['external_calls']==0
    assert all(r['status']<400 for r in trace['requests'])
    assert trace['bundles']
    for bundle in trace['bundles']:
        assert (root/'dist-calm-discovery-web-01a12efd'/bundle.split(':8920/')[1]).is_file(),bundle
logs += ['tools-'+name+'-dark.txt' for name in ['quiz','flashcards','recitation','chat']]
for name in logs:
    assert (folder/name).read_text(encoding='utf-8-sig').startswith('PASS '),name
contracts=(folder/'contracts.txt').read_text(encoding='utf-8-sig')
count=int(re.search(r'tests (\d+)',contracts).group(1))
assert f'pass {count}' in contracts and 'fail 0' in contracts
assert not (folder/'typecheck.txt').read_text(encoding='utf-8-sig').strip()
for platform in ['web','android']:
    assert 'Exported: dist-calm-discovery-'+platform+'-01a12efd' in (folder/f'build-{platform}.txt').read_text(encoding='utf-8-sig')
diff=[]
for name in ['app/search.tsx','app/(tabs)/knowledge.tsx','app/(tabs)/index.tsx','app/(tabs)/_layout.tsx','app/_layout.tsx','app/material.tsx']:
    old=(folder/'before'/(re.sub(r'[\\/()]','_',name)+'.txt')).read_text(encoding='utf-8').splitlines(True)
    diff += difflib.unified_diff(old,(root/name).read_text(encoding='utf-8').splitlines(True),fromfile='before/'+name,tofile=name)
(folder/'product.diff').write_text(''.join(diff),encoding='utf-8')
all_files=set(files)
all_files.update(p for p in folder.rglob('*') if p.is_file() and p.name!='manifest.json')
all_files.update(p for p in (root/'tests').glob('*discovery*') if p.is_file())
for platform in ['web','android']:
    all_files.update(p for p in (root/('dist-calm-discovery-'+platform+'-01a12efd')).rglob('*') if p.is_file())
for name in ['material-calm-ui.mjs','mobile-tools-ui.mjs','mobile-tools-fixture.mjs']:
    all_files.add(root/'tests'/name)
manifest={'round':'MOBILE-UX-04/01a12efd','created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'contracts':count,'browser_flows':len(logs),'source_changes_since_build':changed,'build_input_count':len(inputs),'typecheck':0,'web_export':0,'android_resource_export':0,'limits':['No native device/APK validation','Semantic retrieval isolated mock; no live model or vector call'],'files':{p.relative_to(root).as_posix():hash_file(p) for p in sorted(all_files)}}
(folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'contracts':count,'browser_flows':len(logs),'sha_files':len(all_files),'source_changes':changed}))
