"""Regression tests for KAF grant memo validation without optional app dependencies."""

import asyncio
import hashlib
import importlib.util
import itertools
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value

    def desc(self):
        return self.name, "desc"


class KafFile:
    file_id = Column("file_id")

    def __init__(self, file_id):
        self.file_id = file_id
        self.sha256 = hashlib.sha256(b"test package").hexdigest()
        self.is_published = True
        self.enabled_tiers = "pro"


class KafGrant:
    id = Column("id")
    created_at = Column("created_at")
    file_id = Column("file_id")
    user_id = Column("user_id")
    revoked = Column("revoked")
    _ids = itertools.count(1)

    def __init__(self, **values):
        self.__dict__.update(values)
        self.id = f"grant-{next(self._ids)}"
        self.revoked = values.get("revoked", False)
        self.created_at = values.get("created_at", datetime.now(timezone.utc).replace(tzinfo=None))


class Query:
    def __init__(self, model, is_update=False):
        self.model = model
        self.is_update = is_update
        self.predicates = ()
        self.update_values = {}
        self.max_rows = None
        self.ordering = ()

    def where(self, *predicates):
        self.predicates += predicates
        return self

    def execution_options(self, **options):
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def order_by(self, *columns):
        self.ordering = columns
        return self

    def values(self, **values):
        self.update_values = values
        return self


class Result:
    def __init__(self, rows, rowcount=0):
        self.rows = rows
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        if len(self.rows) > 1:
            raise RuntimeError("MultipleResultsFound")
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, file):
        self.file = file
        self.grants = []

    async def execute(self, query):
        rows = [self.file] if query.model is KafFile else self.grants
        matches = [row for row in rows
                   if all(getattr(row, name) == value for name, value in query.predicates)]
        if query.is_update:
            for row in matches:
                row.__dict__.update(query.update_values)
            return Result([], rowcount=len(matches))
        for name, direction in reversed(query.ordering):
            matches.sort(key=lambda row: getattr(row, name), reverse=(direction == "desc"))
        if query.max_rows is not None:
            matches = matches[:query.max_rows]
        return Result(matches)

    def add(self, grant):
        self.grants.append(grant)

    async def commit(self):
        pass


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class GrantCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        kaf_dir = base / "data" / "documents" / "kaf"
        kaf_dir.mkdir(parents=True)
        (kaf_dir / "file-1.kaf").write_bytes(b"test package")

        modules = {
            "sqlalchemy": module("sqlalchemy", select=lambda model: Query(model),
                                 update=lambda model: Query(model, is_update=True)),
            "sqlalchemy.ext": module("sqlalchemy.ext"),
            "sqlalchemy.ext.asyncio": module("sqlalchemy.ext.asyncio", AsyncSession=object),
            "app": module("app"),
            "app.config": module("app.config", BASE_DIR=base, DATA_DIR=base / "data", settings=types.SimpleNamespace(document_dir=str(base / "data" / "documents"))),
            "app.core": module("app.core"),
            "app.core.crypto": module("app.core.crypto", get_fernet=lambda: None),
            "app.models": module("app.models"),
            "app.models.kaf": module("app.models.kaf", KafFile=KafFile, KafGrant=KafGrant,
                                     utcnow=lambda: self.now),
            "kaf": module("kaf"),
            "kaf.format": module("kaf.format", inspect_kaf=lambda data: {"header": {"file_id": "file-1"}}),
            "kaf.errors": module("kaf.errors", KAFDecryptError=type("KAFDecryptError", (Exception,), {})),
        }
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

        path = Path(__file__).resolve().parents[1] / "app" / "core" / "kaf_service.py"
        spec = importlib.util.spec_from_file_location("_tested_kaf_service", path)
        service_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service_module)
        self.service = service_module.KAFService()
        self.service._load_or_create_keypair = lambda: None
        self.service._unwrap_content_key = lambda data: b"\x11" * 32
        self.db = FakeDB(KafFile("file-1"))

    def grant(self):
        return asyncio.run(self.service.grant(self.db, "file-1", "user-1", "pro"))

    def revoke(self, user_id="user-1"):
        return asyncio.run(self.service.revoke(self.db, "file-1", user_id))

    def restore(self, user_id="user-1"):
        return asyncio.run(self.service.restore(self.db, "file-1", user_id))

    def test_revoked_grant_blocks_new_grants_until_restored(self):
        self.grant()
        self.assertEqual(self.revoke(), 1)

        with self.assertRaisesRegex(PermissionError, "已被管理员吊销"):
            self.grant()
        self.assertEqual(len(self.db.grants), 1)

        self.assertEqual(self.restore(), 1)
        self.assertIsNone(self.db.grants[0].revoked)
        restored = self.grant()
        self.assertFalse(restored["cached"])
        self.assertEqual(len(self.db.grants), 2)
        self.assertFalse(self.db.grants[1].revoked)

    def test_cross_worker_revocation_blocks_cached_response(self):
        self.grant()
        self.db.grants[0].revoked = True  # a different worker updated the database

        with self.assertRaisesRegex(PermissionError, "已被管理员吊销"):
            self.grant()
        self.assertEqual(len(self.db.grants), 1)

    def test_revoke_without_prior_grant_returns_zero_and_does_not_block(self):
        self.assertEqual(self.revoke(), 0)
        self.assertEqual(self.restore(), 0)
        self.assertFalse(self.grant()["cached"])

    def test_empty_user_id_does_not_revoke_every_user(self):
        self.grant()
        self.assertEqual(self.revoke(user_id=""), 0)
        self.assertFalse(self.db.grants[0].revoked)
        self.assertTrue(self.grant()["cached"])

    def test_expired_database_grant_invalidates_cached_key(self):
        self.grant()
        self.db.grants[0].expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)

        second = self.grant()

        self.assertFalse(second["cached"])
        self.assertGreater(self.db.grants[0].expires_at, datetime.now(timezone.utc).replace(tzinfo=None))

    def test_old_revoked_history_blocks_newer_active_grant_until_restored(self):
        self.grant()
        old = self.db.grants[0]
        old.revoked = True
        replacement = KafGrant(file_id="file-1", user_id="user-1", tier="pro",
                               expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2))
        self.db.add(replacement)

        with self.assertRaisesRegex(PermissionError, "已被管理员吊销"):
            self.grant()
        self.assertEqual(self.restore(), 1)
        second = self.grant()

        self.assertFalse(second["cached"])
        self.assertIsNone(old.revoked)
        self.assertFalse(replacement.revoked)
        self.assertEqual(len(self.db.grants), 2)

    def test_old_revoked_history_blocks_check_even_with_active_replacement(self):
        self.grant()
        self.db.grants[0].revoked = True
        self.db.add(KafGrant(file_id="file-1", user_id="user-1", tier="pro",
                             expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)))

        ok, info = asyncio.run(self.service.check_active_grant(self.db, "file-1", "user-1"))

        self.assertFalse(ok)
        self.assertEqual(info, {})

    def test_duplicate_active_grants_refreshes_latest_row(self):
        self.grant()
        older = self.db.grants[0]
        older.created_at = self.now.replace(tzinfo=None) - timedelta(minutes=2)
        older_expiry = older.expires_at
        newer = KafGrant(file_id="file-1", user_id="user-1", tier="pro",
                         created_at=self.now.replace(tzinfo=None) - timedelta(minutes=1),
                         expires_at=self.now.replace(tzinfo=None) + timedelta(minutes=10))
        self.db.add(newer)
        self.service._grant_cache.clear()

        refreshed = self.grant()

        self.assertFalse(refreshed["cached"])
        self.assertEqual(older.expires_at, older_expiry)
        self.assertEqual(newer.expires_at,
                         datetime.fromisoformat(refreshed["expires_at"]).replace(tzinfo=None))

    def test_duplicate_active_grants_check_uses_id_to_break_timestamp_tie(self):
        self.grant()
        older = self.db.grants[0]
        older.created_at = self.now.replace(tzinfo=None)
        older.id = "grant-a"
        newer = KafGrant(file_id="file-1", user_id="user-1", tier="enterprise",
                         created_at=older.created_at,
                         expires_at=self.now.replace(tzinfo=None) + timedelta(hours=2))
        newer.id = "grant-z"
        self.db.add(newer)

        ok, info = asyncio.run(self.service.check_active_grant(self.db, "file-1", "user-1"))

        self.assertTrue(ok)
        self.assertEqual(info["tier"], "enterprise")
        self.assertEqual(info["expires_at"], newer.expires_at.isoformat())

    def test_revoke_after_initial_check_prevents_reissued_key_return(self):
        self.grant()
        self.service._grant_cache.clear()
        original_check = self.service._has_revocation
        revoked_counts = []

        async def revoke_after_first_check(db, file_id, user_id):
            blocked = await original_check(db, file_id, user_id)
            if not blocked and not revoked_counts:
                revoked_counts.append(await self.service.revoke(self.db, file_id, user_id))
            return blocked

        self.service._has_revocation = revoke_after_first_check

        with self.assertRaisesRegex(PermissionError, "已被管理员吊销"):
            self.grant()
        self.assertEqual(revoked_counts, [1])
        self.assertTrue(self.db.grants[0].revoked)
        self.assertEqual(len(self.db.grants), 2)
        self.assertNotIn(("file-1", "user-1"), self.service._grant_cache)

    def test_filewide_revoke_and_targeted_restore_keep_other_user_blocked(self):
        self.grant()
        asyncio.run(self.service.grant(self.db, "file-1", "user-2", "pro"))

        self.assertEqual(self.revoke(user_id=None), 2)
        self.assertEqual(self.restore(user_id="user-1"), 1)
        self.assertFalse(self.grant()["cached"])
        with self.assertRaisesRegex(PermissionError, "已被管理员吊销"):
            asyncio.run(self.service.grant(self.db, "file-1", "user-2", "pro"))
        self.assertEqual(self.restore(user_id=None), 1)
        self.assertFalse(asyncio.run(self.service.grant(self.db, "file-1", "user-2", "pro"))["cached"])

    def test_valid_cache_response_matches_first_response_shape(self):
        first = self.grant()
        second = self.grant()

        self.assertTrue(second["cached"])
        self.assertEqual(set(second), set(first))
        self.assertEqual(second["file_id"], first["file_id"])
        self.assertEqual(second["expires_at"], first["expires_at"])
        self.assertIsInstance(second["expires_at"], str)

    def test_cache_response_does_not_extend_shortened_database_expiry(self):
        self.grant()
        shorter = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1)
        self.db.grants[0].expires_at = shorter

        second = self.grant()

        self.assertIsInstance(second["expires_at"], str)
        self.assertLessEqual(datetime.fromisoformat(second["expires_at"]), shorter.replace(tzinfo=timezone.utc))

    def test_cache_expires_after_five_minutes_but_grant_remains_valid_for_24_hours(self):
        first = self.grant()
        self.now += timedelta(seconds=301)

        second = self.grant()

        self.assertFalse(second["cached"])
        self.assertEqual(second["expires_at"], (self.now + timedelta(hours=24)).isoformat())
        self.assertGreater(datetime.fromisoformat(second["expires_at"]), datetime.fromisoformat(first["expires_at"]))


if __name__ == "__main__":
    unittest.main()
