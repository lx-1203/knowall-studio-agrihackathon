"""Central account API and authenticated streaming proxy to personal workers.

The gateway never imports app.main: central accounts and learning workspaces have
different lifecycles and databases. No private request falls back to anonymous.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse, Response
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect

from app.core.auth import decode_access_token
from app.database import async_session
from app.models import User

logger = logging.getLogger('knowall.cloud')
AUTH_PREFIX = '/api/v1/auth'
PUBLIC = {('POST', AUTH_PREFIX + '/' + name) for name in (
    'login', 'register', 'check-username', 'verify-email', 'forgot-password', 'reset-password', 'local-session')}
PUBLIC.add(('GET', '/health'))
PUBLIC.add(('GET', '/api/v1/oauth/providers'))
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}


def filtered_headers(headers, *, request=False):
    excluded = HOP_HEADERS | {name.strip().lower() for name in headers.get('connection', '').split(',')}
    if request:
        excluded |= {'host', 'content-length', 'accept-encoding'}
    return [(key, value) for key, value in headers.raw if key.decode('latin-1').lower() not in excluded]


def worker_origin(origin):
    parsed = urlsplit(origin)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1', 'localhost'}
            or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise HTTPException(502, '个人学习服务地址无效')
    return origin.rstrip('/')


async def central_identity(user_id):
    async with async_session() as session:
        return await session.get(User, user_id)


def connect_worker_websocket(uri, **kwargs):
    from websockets.asyncio.client import connect

    class DirectWorkerConnection(connect):
        # websockets follows redirects by default and preserves auth headers.
        # A private worker is a fixed loopback endpoint; never follow a redirect.
        def process_redirect(self, exc):
            return exc

    return DirectWorkerConnection(uri, **kwargs)


async def authenticate(headers, identity_lookup, token=None):
    if token is None:
        value = headers.get('authorization', '')
        token = value[7:].strip() if value.lower().startswith('bearer ') else ''
    user_id = decode_access_token(token) if token else None
    user = await identity_lookup(user_id) if isinstance(user_id, str) and user_id else None
    if user is None or not user.is_active:
        raise HTTPException(401, '请先登录')
    return user, token


class AuthenticationGate:
    """ASGI gate protects unknown routes as well as HTTP and WebSocket handlers."""
    def __init__(self, app, identity_lookup):
        self.app, self.identity_lookup = app, identity_lookup

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'}:
            public = scope['type'] == 'http' and (scope['method'], scope['path']) in PUBLIC
            if not public:
                headers = Headers(scope=scope)
                token = None
                if scope['type'] == 'websocket':
                    params = dict(parse_qsl(scope.get('query_string', b'').decode('latin-1')))
                    token = params.get('token') or None
                try:
                    user, token = await authenticate(headers, self.identity_lookup, token)
                    scope.setdefault('state', {}).update(cloud_user=user, cloud_token=token)
                except HTTPException as exc:
                    if scope['type'] == 'websocket':
                        await send({'type': 'websocket.close', 'code': 4401})
                    else:
                        await JSONResponse({'detail': exc.detail}, status_code=exc.status_code)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_cloud_app(pool, *, identity_lookup=None, http_client=None, initialize=None):
    """Injectable transports keep security tests away from live services and data."""
    lookup = identity_lookup or central_identity

    @asynccontextmanager
    async def lifespan(app):
        if initialize is not None:
            await initialize()
        try:
            yield
        finally:
            await pool.close()
            if http_client is None:
                await app.state.proxy_client.aclose()

    app = FastAPI(title='KnowAll Cloud', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.proxy_client = http_client or httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=httpx.Timeout(120, connect=10))
    app.add_middleware(AuthenticationGate, identity_lookup=lookup)

    from app.api.auth import router as auth_router
    from app.api.user import router as user_router
    # Replace local-session with a permanently disabled cloud implementation.
    accounts = APIRouter()
    accounts.routes.extend(route for route in auth_router.routes if route.path != AUTH_PREFIX + '/local-session')
    central_paths = {'/api/v1/user/' + name for name in ('profile', 'password', 'binds', 'bind', 'bind/{provider}')}
    accounts.routes.extend(route for route in user_router.routes if route.path in central_paths)
    app.include_router(accounts)

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'mode': 'cloud'}

    @app.post(AUTH_PREFIX + '/local-session')
    async def local_session():
        return {'enabled': False}

    @app.get('/api/v1/oauth/providers')
    async def oauth_providers():
        return {'providers': []}

    @app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
    @app.api_route('/glossary_terms.kgb', methods=['GET', 'HEAD'])
    async def learning_proxy(request: Request):
        # No central account operation may fall through to worker credentials,
        # including trailing slashes, unsupported methods and disabled OAuth/SMS.
        path = request.url.path.rstrip('/')
        account_prefixes = (AUTH_PREFIX, '/api/v1/oauth', '/api/v1/sms')
        user_path = path.removeprefix('/api/v1/user/')
        worker_user_route = user_path in {'keys', 'history'} or user_path.startswith('keys/')
        if (any(path == prefix or path.startswith(prefix + '/') for prefix in account_prefixes)
                or (path.startswith('/api/v1/user') and not worker_user_route)):
            raise HTTPException(404, '接口不存在')
        async def execute(scope, receive, send):
            async with pool.lease(request.state.cloud_user) as worker:
                origin = worker_origin(worker.origin)
                target = origin + request.scope.get('raw_path', request.url.path.encode('utf-8')).decode('ascii')
                if request.url.query:
                    target += '?' + request.url.query
                upstream_request = app.state.proxy_client.build_request(
                    request.method, target, headers=filtered_headers(request.headers, request=True), content=request.stream())
                try:
                    upstream = await app.state.proxy_client.send(upstream_request, stream=True, follow_redirects=False)
                except httpx.HTTPError as exc:
                    logger.warning('Workspace transport failed: %s', type(exc).__name__)
                    raise HTTPException(502, '个人学习服务暂时不可用') from None
                try:
                    response_headers = filtered_headers(upstream.headers)
                    if 300 <= upstream.status_code < 400 and upstream.headers.get('location'):
                        redirect = urlsplit(urljoin(target, upstream.headers['location']))
                        if (redirect.scheme, redirect.netloc) != (urlsplit(origin).scheme, urlsplit(origin).netloc):
                            raise HTTPException(502, '个人学习服务返回了不安全的跳转')
                        location = redirect.path + ('?' + redirect.query if redirect.query else '')
                        response_headers = [(key, value) for key, value in response_headers if key.lower() != b'location']
                        response_headers.append((b'location', location.encode('latin-1')))
                    response = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code)
                    response.raw_headers = response_headers
                    # Keep the worker lease alive until streaming or disconnect finishes.
                    await response(scope, receive, send)
                finally:
                    await upstream.aclose()
        return StreamingProxyResponse(execute)

    @app.websocket('/ws/sync')
    async def websocket_proxy(websocket: WebSocket):
        from websockets.exceptions import WebSocketException
        token = websocket.state.cloud_token
        async with pool.lease(websocket.state.cloud_user) as worker:
            origin = worker_origin(worker.origin).replace('http://', 'ws://', 1)
            query = [(key, value) for key, value in websocket.query_params.multi_items() if key != 'token']
            query.append(('token', token))
            target = origin + '/ws/sync?' + urlencode(query)
            try:
                async with connect_worker_websocket(target, additional_headers={'Authorization': 'Bearer ' + token},
                                                    proxy=None, max_size=None, open_timeout=10) as upstream:
                    await websocket.accept()

                    async def to_worker():
                        while True:
                            message = await websocket.receive()
                            if message['type'] == 'websocket.disconnect':
                                return
                            await upstream.send(message.get('text') if message.get('text') is not None else message['bytes'])

                    async def to_client():
                        async for message in upstream:
                            if isinstance(message, bytes):
                                await websocket.send_bytes(message)
                            else:
                                await websocket.send_text(message)

                    tasks = [asyncio.create_task(to_worker()), asyncio.create_task(to_client())]
                    try:
                        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    await websocket.close()
            except (OSError, WebSocketException, WebSocketDisconnect, TimeoutError):
                try:
                    await websocket.close(code=1011)
                except RuntimeError:
                    pass

    return app


class StreamingProxyResponse(Response):
    """Execute the whole streamed response inside its worker lease."""
    def __init__(self, execute):
        super().__init__()
        self.execute = execute

    async def __call__(self, scope, receive, send):
        await self.execute(scope, receive, send)
