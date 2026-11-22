"""The synthetic table must not overlap neighboring cells."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import pytest


GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "generate_parsing_fixtures.py"


def test_table_fixture_keeps_every_label_inside_its_cell(tmp_path):
    assert GENERATOR.exists(), "table fixture generator is missing"
    spec = spec_from_file_location("generate_parsing_fixtures", GENERATOR)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    manifest = module.generate_table(tmp_path)
    assert manifest["rows"] == 6
    assert manifest["columns"] == 4
    assert Path(manifest["image"]).is_file()
    assert len(manifest["cells"]) == 24

    for cell in manifest["cells"]:
        left, top, right, bottom = cell["bounds"]
        text_left, text_top, text_right, text_bottom = cell["text_bounds"]
        assert left + 12 <= text_left < text_right <= right - 12, cell
        assert top + 8 <= text_top < text_bottom <= bottom - 8, cell


def test_table_fixture_rejects_overlong_course_name(tmp_path):
    spec = spec_from_file_location("generate_parsing_fixtures_invalid", GENERATOR)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TABLE_ROWS[1][0] = "无机及分析化学" * 4
    with pytest.raises(ValueError, match="Text crosses cell border"):
        module.generate_table(tmp_path)
