"""KAF administrator boundary tests that run without optional app dependencies."""

import ast
import asyncio
import contextlib
import importlib.util
import io
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


BACKEND = Path(__file__).resolve().parents[1]


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class Dependency:
    def __init__(self, dependency):
        self.dependency = dependency


class Router:
    def __init__(self, prefix="", tags=None, dependencies=None):
        self.prefix = prefix
        self.dependencies = list(dependencies or [])
        self.routes = []

    def _route(self, path):
        def register(endpoint):
            self.routes.append(types.SimpleNamespace(
                path=self.prefix + path,
                dependencies=list(self.dependencies),
                endpoint=endpoint,
            ))
            return endpoint
        return register

    def get(self, path, **kwargs):
        return self._route(path)

    def post(self, path, **kwargs):
        return self._route(path)

    def include_router(self, other):
        for route in other.routes:
            self.routes.append(types.SimpleNamespace(
                path=self.prefix + route.path,
                dependencies=self.dependencies + route.dependencies,
                endpoint=route.endpoint,
            ))


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class User:
    def __init__(self, is_admin=False, is_active=True):
        self.is_admin = is_admin
        self.is_active = is_active


class Column:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class Query:
    def where(self, *args):
        return self


class KafFile:
    file_id = "file_id"


def load_python(name, path, replacements):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements):
        spec.loader.exec_module(loaded)
    return loaded


class AdminRoleTests(unittest.TestCase):
    def test_user_role_defaults_to_false_in_model(self):
        sa = module(
            "sqlalchemy", Column=Column,
            String=lambda *args: "String", Boolean="Boolean", DateTime="DateTime",
            Text="Text", ForeignKey=lambda *args, **kwargs: "ForeignKey",
            JSON="JSON", Index=lambda *args: None,
        )
        user_module = load_python("tested_user_model", BACKEND / "app/models/user.py", {
            "sqlalchemy": sa,
            "sqlalchemy.orm": module("sqlalchemy.orm", relationship=lambda *args, **kwargs: None),
            "app": module("app"),
            "app.database": module("app.database", Base=type("Base", (), {})),
        })
        role = user_module.User.is_admin
        self.assertIs(role.kwargs["default"], False)
        self.assertIs(role.kwargs["nullable"], False)
        self.assertEqual(role.kwargs["server_default"], "0")

    def test_migration_backfills_existing_users_and_has_single_parent(self):
        calls = []
        op = module("alembic.op", add_column=lambda table, column: calls.append((table, column)))
        sa = module("sqlalchemy", Column=Column, Boolean=lambda: "Boolean")
        migration = BACKEND / "alembic/versions/e2a4c6b8d0f1_add_user_admin_role.py"
        with patch.dict(sys.modules, {
            "alembic": module("alembic", op=op),
            "sqlalchemy": sa,
        }):
            namespace = runpy.run_path(str(migration))
            namespace["upgrade"]()
        self.assertEqual(namespace["down_revision"], "d6f3b9a4c1e2")
        table, column = calls.pop()
        self.assertEqual(table, "users")
        self.assertEqual(column.args[:2], ("is_admin", "Boolean"))
        self.assertIs(column.kwargs["nullable"], False)
        self.assertEqual(column.kwargs["server_default"], "0")

    def test_admin_migration_is_ancestor_of_the_only_alembic_head(self):
        revisions = {}
        for path in (BACKEND / "alembic/versions").glob("*.py"):
            values = {}
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                            values[target.id] = ast.literal_eval(node.value)
            if values.get("revision"):
                revisions[values["revision"]] = values.get("down_revision")
        parents = {parent for parent in revisions.values() if parent}
        heads = set(revisions) - parents
        self.assertEqual(len(heads), 1, "Migrations must have exactly one head")
        current = next(iter(heads))
        visited = set()
        while current is not None:
            self.assertNotIn(current, visited, "Migration ancestry contains a cycle")
            visited.add(current)
            self.assertIn(current, revisions, "Migration parent is missing")
            current = revisions[current]
        self.assertIn("e2a4c6b8d0f1", visited, "Admin role migration must remain in the upgrade chain")

    def test_all_admin_routes_share_role_dependency(self):
        api, _ = self._load_api()
        admin_routes = [route for route in api.router.routes if "/admin/" in route.path]
        self.assertEqual({route.path for route in admin_routes}, {
            "/api/v1/kaf/admin/publish",
            "/api/v1/kaf/admin/{file_id}/revoke",
            "/api/v1/kaf/admin/{file_id}/unpublish",
            "/api/v1/kaf/admin/{file_id}/restore",
        })
        for route in admin_routes:
            self.assertIn(api.require_kaf_admin, [d.dependency for d in route.dependencies])

    def test_regular_user_cannot_enter_admin_boundary(self):
        api, _ = self._load_api()
        with self.assertRaises(HTTPException) as raised:
            api.require_kaf_admin(User())
        self.assertEqual(raised.exception.status_code, 403)
        admin = User(is_admin=True)
        self.assertIs(api.require_kaf_admin(admin), admin)

    def test_admin_routes_require_login_before_role_check(self):
        auth = self._load_real_auth()
        api, _ = self._load_api(auth.get_current_user)
        self.assertIs(api.require_kaf_admin.__defaults__[0].dependency,
                      auth.get_current_user)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(auth.get_current_user(credentials=None, db=object()))
        self.assertEqual(raised.exception.status_code, 401)

    def test_restore_calls_service_for_optional_user_scope(self):
        api, service = self._load_api()
        db = object()
        api._get_existing = AsyncMock(return_value=object())
        result = asyncio.run(api.admin_restore("file-1", {"user_id": "user-1"}, db=db))
        self.assertEqual(result, {"restored": 2})
        service.restore.assert_awaited_once_with(db, "file-1", "user-1")

    def test_unknown_file_cannot_be_restored_or_revoked(self):
        api, service = self._load_api()
        api._get_existing = AsyncMock(side_effect=HTTPException(404, "文件不存在"))
        for endpoint in (api.admin_revoke, api.admin_restore):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(endpoint("missing", db=object()))
            self.assertEqual(raised.exception.status_code, 404)
        service.revoke.assert_not_awaited()
        service.restore.assert_not_awaited()

    def test_revoked_user_cannot_download_ciphertext(self):
        api, service = self._load_api()
        api._get_published = AsyncMock(return_value=object())
        api._user_tier = AsyncMock(return_value="pro")
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(api.download_file("file-1", current_user=types.SimpleNamespace(id="user-1"),
                                          db=object()))
        self.assertEqual(raised.exception.status_code, 403)
        service._has_revocation.assert_awaited_once()

    def test_check_rejects_unpublished_file_and_inadequate_tier(self):
        api, service = self._load_api()
        api._get_published = AsyncMock(side_effect=HTTPException(404, "文件不存在"))
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(api.check_access("file-1", current_user=types.SimpleNamespace(id="u"),
                                         db=object()))
        self.assertEqual(raised.exception.status_code, 404)
        service.check_active_grant.assert_not_awaited()

        api._get_published = AsyncMock(return_value=object())
        api._user_tier = AsyncMock(return_value="free")
        service.tier_allows.return_value = False
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(api.check_access("file-1", current_user=types.SimpleNamespace(id="u"),
                                         db=object()))
        self.assertEqual(raised.exception.status_code, 403)
        service.check_active_grant.assert_not_awaited()

    def test_check_rejects_revoked_grant(self):
        api, service = self._load_api()
        api._get_published = AsyncMock(return_value=object())
        api._user_tier = AsyncMock(return_value="pro")
        service.check_active_grant.return_value = (False, {})
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(api.check_access("file-1", current_user=types.SimpleNamespace(id="u"),
                                         db=object()))
        self.assertEqual(raised.exception.status_code, 403)

    def test_cli_rejects_inactive_grant_and_last_admin_revoke(self):
        cli = self._load_cli()
        with self.assertRaises(ValueError):
            cli.validate_role_change(User(is_active=False), "grant", 0)
        with self.assertRaises(ValueError):
            cli.validate_role_change(User(is_admin=True), "revoke", 1)
        self.assertIsNone(cli.validate_role_change(User(), "grant", 0))
        self.assertIsNone(cli.validate_role_change(User(is_admin=True), "revoke", 2))
        self.assertIsNone(cli.validate_role_change(
            User(is_admin=True, is_active=False), "revoke", 1
        ))

    def test_cli_promotes_only_confirmed_existing_account(self):
        cli = self._load_cli()
        user = User()
        user.id, user.username, user.email = "user-1", "chosen", "chosen@example.com"
        db = types.SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *args):
                return None

        with (patch.object(cli, "async_session", return_value=SessionContext()),
              patch.object(cli, "_load_target_and_count", new=AsyncMock(side_effect=[
                  (user, 0), (user, 0),
              ])),
              patch.object(sys.stdin, "isatty", return_value=True),
              patch("builtins.input", return_value="user-1"),
              contextlib.redirect_stdout(io.StringIO())):
            asyncio.run(cli.set_admin_role("grant", "user-1"))
        self.assertTrue(user.is_admin)
        db.commit.assert_awaited_once()

    @staticmethod
    def _load_cli():
        return load_python(
            "tested_admin_cli", BACKEND / "scripts/set_admin_role.py", {
                "sqlalchemy": module("sqlalchemy", select=lambda *args: None, func=types.SimpleNamespace()),
                "app": module("app"),
                "app.database": module("app.database", async_session=None),
                "app.models": module("app.models"),
                "app.models.user": module("app.models.user", User=User),
            },
        )

    @staticmethod
    def _load_real_auth():
        class HTTPBearer:
            def __init__(self, **kwargs):
                pass

        replacements = {
            "fastapi": module("fastapi", Depends=Dependency, HTTPException=HTTPException),
            "fastapi.security": module("fastapi.security", HTTPBearer=HTTPBearer,
                                       HTTPAuthorizationCredentials=object),
            "sqlalchemy": module("sqlalchemy", select=lambda *args: Query()),
            "sqlalchemy.ext": module("sqlalchemy.ext"),
            "sqlalchemy.ext.asyncio": module("sqlalchemy.ext.asyncio", AsyncSession=object),
            "jose": module("jose", jwt=types.SimpleNamespace(), JWTError=Exception),
            "bcrypt": module("bcrypt"),
            "app": module("app"),
            "app.config": module("app.config", settings=types.SimpleNamespace()),
            "app.database": module("app.database", get_db=lambda: None),
            "app.models": module("app.models"),
            "app.models.user": module("app.models.user", User=User),
        }
        return load_python("tested_real_auth", BACKEND / "app/core/auth.py", replacements)

    @staticmethod
    def _load_api(auth_dependency=None):
        service = types.SimpleNamespace(
            restore=AsyncMock(return_value=2), revoke=AsyncMock(return_value=1),
            _has_revocation=AsyncMock(return_value=True),
            check_active_grant=AsyncMock(return_value=(True, {})),
            tier_allows=Mock(return_value=True),
        )
        replacements = {
            "fastapi": module("fastapi", APIRouter=Router, Depends=Dependency,
                              HTTPException=HTTPException),
            "fastapi.responses": module("fastapi.responses", FileResponse=object),
            "sqlalchemy": module("sqlalchemy", select=lambda *args: Query()),
            "sqlalchemy.ext": module("sqlalchemy.ext"),
            "sqlalchemy.ext.asyncio": module("sqlalchemy.ext.asyncio", AsyncSession=object),
            "app": module("app"),
            "app.core": module("app.core"),
            "app.core.auth": module("app.core.auth", get_current_user=auth_dependency or (lambda: None)),
            "app.core.kaf_service": module("app.core.kaf_service", get_kaf_service=lambda: service,
                                           KAFKeyUnavailableError=type("KAFKeyUnavailableError", (RuntimeError,), {}),
                                           KAFContentUnavailableError=type("KAFContentUnavailableError", (RuntimeError,), {})),
            "app.database": module("app.database", get_db=lambda: None),
            "app.models": module("app.models"),
            "app.models.kaf": module("app.models.kaf", KafFile=KafFile),
            "app.models.subscription": module("app.models.subscription", UserTier=object),
            "app.models.user": module("app.models.user", User=User),
        }
        return load_python("tested_kaf_api", BACKEND / "app/api/kaf.py", replacements), service


if __name__ == "__main__":
    unittest.main()
