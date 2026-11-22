"""Build a clean deployment archive; never copy checkout secrets or user data."""
import hashlib
import json
import zipfile
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDIO = ROOT / 'KnowAll_Studio'
OUTPUT = STUDIO / 'release' / 'KnowAll-Cloud-0.1.0.zip'
BLOCKED = {'node_modules', '__pycache__', '.git', '.tmp', 'data', 'keys', 'test-results', 'playwright-report'}


def allowed(path):
    return not (set(path.parts) & BLOCKED) and not (path.name.startswith('.env') and path.name != '.env.example') and path.suffix.lower() not in {'.pyc', '.db', '.sqlite', '.pem', '.key'} and path.name not in {'.jwt_secret', '.secret_key'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--frontend-dist', type=Path, default=STUDIO / 'frontend/dist')
    args = parser.parse_args()
    output = args.output.resolve()
    frontend_dist = args.frontend_dist.resolve()
    if not (frontend_dist / 'index.html').is_file():
        raise RuntimeError('Build the current frontend before packaging')
    paths = set()
    for relative in ['KnowAll_Studio/backend/app', 'KnowAll_Studio/backend/alembic', 'KnowAll_Studio/docker/cloud',
                     'KnowAll_Studio/backend/third_party', 'KnowAll_Studio/frontend/src', 'KnowAll_Studio/frontend/public',
                     'KnowAll_Studio/frontend/scripts', 'KnowAll_Studio/frontend/vendor', 'kaf_format/kaf']:
        paths.update(path for path in (ROOT / relative).rglob('*') if path.is_file() and allowed(path.relative_to(ROOT)))
    for relative in ['KnowAll_Studio/backend/requirements.txt', 'KnowAll_Studio/backend/alembic.ini',
                     'KnowAll_Studio/backend/cloud_entry.py', 'KnowAll_Studio/backend/cloud_worker.py',
                     'KnowAll_Studio/backend/scripts/cloud_integration_fixture.py', 'kaf_format/pyproject.toml']:
        paths.add(ROOT / relative)
    for path in (STUDIO / 'frontend').iterdir():
        if path.is_file() and path.suffix in {'.json', '.ts', '.js', '.html'} and allowed(path.relative_to(ROOT)):
            paths.add(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    entries = [(path, path.relative_to(ROOT).as_posix()) for path in sorted(paths)]
    entries.extend((path, 'KnowAll_Studio/frontend/dist/' + path.relative_to(frontend_dist).as_posix())
                   for path in sorted(frontend_dist.rglob('*')) if path.is_file() and allowed(path.relative_to(frontend_dist)))
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, relative in entries:
            data = path.read_bytes()
            manifest.append({'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            archive.writestr(relative, data)
        archive.writestr('MANIFEST.json', json.dumps({'version': '0.1.0', 'files': manifest}, indent=2))
        archive.writestr('README.txt', 'Cloud deployment bundle. Start with KnowAll_Studio/docker/cloud/README.md. Frontend dist is already built. Configure your own domain before deployment. No user data or real credentials are included.\n')
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert all(allowed(Path(name)) for name in archive.namelist())
        for entry in manifest:
            assert hashlib.sha256(archive.read(entry['path'])).hexdigest() == entry['sha256']
    print(json.dumps({'archive': str(output), 'files': len(manifest), 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
