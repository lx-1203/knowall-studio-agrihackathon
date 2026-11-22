"""Defense in depth: private worker accepts only its gateway-selected owner."""
from urllib.parse import parse_qs
from starlette.responses import JSONResponse


class WorkspaceOwnerGuard:
    def __init__(self, app, owner_id, decoder):
        self.app, self.owner_id, self.decoder = app, owner_id, decoder

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'} or (scope['type'] == 'http' and scope['path'] == '/health'):
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        authorization = headers.get(b'authorization', b'').decode('latin-1')
        token = authorization[7:] if authorization.lower().startswith('bearer ') else ''
        if scope['type'] == 'websocket' and not token:
            params = parse_qs(scope.get('query_string', b'').decode('utf-8', errors='replace'))
            token = params.get('token', [''])[0]
        uid = self.decoder(token) if token else None
        if not self.owner_id or uid != self.owner_id:
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4401})
            else:
                await JSONResponse({'detail': '请先登录'}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)
