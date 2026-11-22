"""Private worker entry. Only the account chosen by the gateway is accepted."""
import os
from app import database
from app.core.cloud_workspaces import initialize_schema


async def init_workspace():
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda conn: initialize_schema(conn, legacy_owner_id=os.environ.get('KNOWALL_WORKSPACE_OWNER')))


database.init_db = init_workspace
from app.main import app
from app.core.auth import decode_access_token


from app.core.workspace_guard import WorkspaceOwnerGuard
app.add_middleware(WorkspaceOwnerGuard, owner_id=os.environ.get('KNOWALL_WORKSPACE_OWNER'), decoder=decode_access_token)


async def serve(port):
    """Use the server's normal lifespan on a private controller pipe's EOF."""
    import asyncio
    import sys
    import threading
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                          access_log=False, timeout_graceful_shutdown=5))
    loop = asyncio.get_running_loop()

    def controller():
        sys.stdin.buffer.read()
        try:
            loop.call_soon_threadsafe(setattr, server, 'should_exit', True)
        except RuntimeError:
            pass  # Server already exited through a signal or startup failure.

    threading.Thread(target=controller, name='workspace-controller', daemon=True).start()
    try:
        await server.serve()
    finally:
        await database.engine.dispose()


if __name__ == '__main__':
    import argparse
    import asyncio
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    asyncio.run(serve(parser.parse_args().port))
