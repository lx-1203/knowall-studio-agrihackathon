"""The skip gate must reject coverage reductions before a test suite runs."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.check_test_skips import check_skips


class CheckTestSkipsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_approved_markers_with_reasons_pass(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "import pytest\n@pytest.mark.skip(reason='fixture missing')\ndef test_one(): pass\n",
        )
        self.write(
            "frontend/tests/e2e/one.spec.ts",
            "test.skip(!hasBackend, 'backend required')\n",
        )
        approved = {
            "backend/tests/test_one.py::test_one": "fixture missing",
            "frontend/tests/e2e/one.spec.ts::test.skip": "!hasBackend, 'backend required'",
        }
        self.assertEqual(check_skips(self.root, approved), [])

    def test_new_python_skip_and_xfail_are_rejected(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "import pytest\n"
            "@pytest.mark.skip(reason='later')\ndef test_one(): pass\n"
            "@pytest.mark.xfail(reason='later')\ndef test_two(): pass\n",
        )
        issues = check_skips(self.root, {})
        self.assertEqual(len(issues), 2)
        self.assertTrue(all("not approved" in issue for issue in issues))

    def test_bare_python_skip_decorator_is_rejected(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "import pytest\n@pytest.mark.skip\ndef test_one(): pass\n",
        )
        issues = check_skips(self.root, {})
        self.assertTrue(any("not approved" in issue for issue in issues))

    def test_pytest_module_alias_skip_is_rejected(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "import pytest as pt\n@pt.mark.skip(reason='later')\ndef test_one(): pass\n",
        )
        issues = check_skips(self.root, {})
        self.assertTrue(any("pytest.mark.skip is not approved" in issue for issue in issues))

    def test_pytest_mark_alias_skipif_checks_approved_reason(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "from pytest import mark as m\n"
            "@m.skipif(True, reason='changed')\ndef test_one(): pass\n",
        )
        issues = check_skips(
            self.root, {"backend/tests/test_one.py::test_one": "original"}
        )
        self.assertTrue(any("approved skip reason changed" in issue for issue in issues))

    def test_pytest_skip_alias_is_rejected(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "from pytest import skip as ignore\n"
            "def test_one(): ignore('later')\n",
        )
        issues = check_skips(self.root, {})
        self.assertTrue(any("pytest.skip is not approved" in issue for issue in issues))

    def test_unrelated_mark_alias_is_not_pytest(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "from unrelated import mark as m\n"
            "@m.skip(reason='later')\ndef test_one(): pass\n",
        )
        self.assertEqual(check_skips(self.root, {}), [])

    def test_playwright_expected_failure_is_rejected(self) -> None:
        self.write("frontend/tests/e2e/one.spec.ts", "test.fail(true, 'later')\n")
        issues = check_skips(self.root, {})
        self.assertTrue(any("not approved" in issue for issue in issues))

    def test_playwright_comments_are_not_skip_markers(self) -> None:
        self.write(
            "frontend/tests/e2e/one.spec.ts",
            "// test.skip(true, 'example')\n"
            "/* test.fail(true, 'example') */\n",
        )
        self.assertEqual(check_skips(self.root, {}), [])

    def test_approved_marker_without_its_reason_is_rejected(self) -> None:
        self.write(
            "backend/tests/test_one.py",
            "import pytest\n@pytest.mark.skip(reason='')\ndef test_one(): pass\n",
        )
        issues = check_skips(
            self.root, {"backend/tests/test_one.py::test_one": "fixture missing"}
        )
        self.assertTrue(any("reason" in issue for issue in issues))

    def test_duplicate_frontend_skip_is_rejected(self) -> None:
        self.write(
            "frontend/tests/e2e/one.spec.ts",
            "test.skip(!hasBackend, 'backend required')\n"
            "test.skip(!hasBackend, 'backend required')\n",
        )
        issues = check_skips(
            self.root, {"frontend/tests/e2e/one.spec.ts::test.skip": "backend required"}
        )
        self.assertTrue(any("duplicate" in issue for issue in issues))

    def test_approved_frontend_skip_becoming_unconditional_is_rejected(self) -> None:
        self.write(
            "frontend/tests/e2e/one.spec.ts",
            "test.skip(true, 'backend required')\n",
        )
        issues = check_skips(
            self.root,
            {"frontend/tests/e2e/one.spec.ts::test.skip": "!hasBackend, 'backend required'"},
        )
        self.assertTrue(any("condition changed" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
