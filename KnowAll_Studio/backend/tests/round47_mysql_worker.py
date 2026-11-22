"""Private subprocess worker used to prove independent directory coordination."""
import asyncio
import json
import os
import sys
from pathlib import Path
import pymysql
from cryptography.hazmat.primitives import serialization
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core import kaf_service, kaf_publisher


async def main():
    url, directory, key, source, kind, gate = sys.argv[1:]
    assert 'round47_private' in url and '127.0.0.1' in url
    kaf_service.KAF_DIR = Path(directory)
    service = kaf_service.KAFService()
    service._private_key = serialization.load_pem_private_key(Path(key).read_bytes(), None)
    service._public_key = service._private_key.public_key()
    kaf_publisher.get_kaf_service = lambda: service
    method = 'publish_from_markdown' if kind == 'document' else 'publish_from_package'
    original = getattr(service, method)
    def gated(*args, **kwargs):
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory)/'building').write_text('ready', encoding='ascii')
        parsed = make_url(url)
        with pymysql.connect(host=parsed.host, port=parsed.port, user=parsed.username, password=parsed.password, database=parsed.database) as db:
            with db.cursor() as cursor:
                cursor.execute('SELECT GET_LOCK(%s, 10)', (gate,))
                assert cursor.fetchone()[0] == 1
                cursor.execute('SELECT RELEASE_LOCK(%s)', (gate,))
        return original(*args, **kwargs)
    setattr(service, method, gated)
    engine = create_async_engine(url, pool_size=2, max_overflow=0, pool_timeout=.5)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if kind == 'document':
                result = await kaf_publisher.publish_document(db, source, 'worker47.md', markdown='# Native worker47')
            else:
                result = await kaf_publisher.publish_summary_package(db, source, 'Summary47', 'Private47', [{'title':'Native', 'body':'Worker content'}])
        print(json.dumps(dict(pid=os.getpid(), directory=directory, result=result)))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
