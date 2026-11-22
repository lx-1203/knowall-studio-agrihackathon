# KnowAll_Studio/backend/tests/test_glossary/test_extractor.py
import pytest
from app.core.glossary.extractor import (
    parse_explanation_bullets,
    extract_aliases_from_title,
    canonicalize,
    build_term_candidates,
    merge_candidate,
    strip_term_definition_prefix,
)


def test_parse_explanation_bullets():
    expl = (
        "#### 遗传物质的定义与特性 🔴【必考】\n"
        "- **详细解释**：遗传物质是生物体遗传信息的载体，目前公认的主要遗传物质是DNA。\n"
        "- **关联概念**：DNA（脱氧核糖核酸）、RNA（核糖核酸）、基因、遗传信息\n"
        "- **示例**：肺炎双球菌转化实验\n"
    )
    result = parse_explanation_bullets(expl)
    assert result["definition"].startswith("遗传物质是生物体遗传信息的载体")
    assert "DNA（脱氧核糖核酸）" in result["related"]
    assert "肺炎双球菌转化实验" in result["example"]
    # 解析后不应残留 ** 标记
    assert "**" not in result["definition"]


def test_parse_explanation_bullets_plain():
    # 无 bullet 格式：整个文本作为 definition
    expl = "DNA的双螺旋结构由两条链组成。"
    result = parse_explanation_bullets(expl)
    assert result["definition"] == "DNA的双螺旋结构由两条链组成。"
    assert result["related"] == []
    assert result["example"] == ""


def test_extract_aliases_from_title():
    title = "DNA（脱氧核糖核酸）"
    aliases = extract_aliases_from_title(title)
    assert "脱氧核糖核酸" in aliases
    assert "DNA" in aliases


def test_extract_aliases_from_title_english_known():
    # 标题里的英文别名（题库题干里常见 "replicon"）
    aliases = extract_aliases_from_title("复制子（replicon）")
    assert "replicon" in aliases
    assert "复制子" in aliases


def test_strip_term_definition_prefix():
    assert strip_term_definition_prefix("请解释以下术语：DNA双螺旋结构") == "DNA双螺旋结构"
    assert strip_term_definition_prefix("请解释以下术语: DNA双螺旋结构") == "DNA双螺旋结构"
    assert strip_term_definition_prefix("以下术语：蛋白质合成") == "蛋白质合成"
    # 无冒号前缀时保留原文本（不误删）
    assert strip_term_definition_prefix("核苷酸序列") == "核苷酸序列"
    assert strip_term_definition_prefix("请解释以下术语DNA双螺旋结构") == "请解释以下术语DNA双螺旋结构"


def test_canonicalize():
    assert canonicalize("  DNA  ") == "dna"
    assert canonicalize("DNA（脱氧核糖核酸）") == "dna脱氧核糖核酸"
    assert canonicalize("DNA,脱氧核糖核酸") == "dna脱氧核糖核酸"
    assert canonicalize("蛋白质—核酸") == "蛋白质核酸"


def test_build_term_candidates_from_node():
    node = {
        "title": "复制子（replicon）",
        "explanation": "#### 复制子（replicon）🔴【必考】\n- **详细解释**：含一个起始区的复制单位。\n"
                       "- **关联概念**：复制叉、复制起始区、Ori\n",
        "level": 3,
        "tags": ["必考"],
        "id": "kp_900e56b4_L3_01_01_01",
        "parent_id": None,
        "sequence": 1,
    }
    candidates = build_term_candidates([node], subject="分子生物学", chapter="第三章 DNA的复制")
    assert len(candidates) >= 1
    c = candidates[0]
    assert c["term"] == "复制子（replicon）"  # 规范名保留标题原样
    assert "replicon" in c["aliases"]
    assert c["definition"].startswith("含一个起始区的复制单位")
    assert c["full_explanation"].startswith("含一个起始区的复制单位")
    assert c["subject"] == "分子生物学"
    assert c["chapter"] == "第三章 DNA的复制"
    assert c["source_node_id"] == "kp_900e56b4_L3_01_01_01"
    assert c["tags"] == ["必考"]
    assert "复制叉" in c["related_terms"]


def test_build_term_candidates_source_fields():
    """build_term_candidates 透传 source_type/source_label 到候选。"""
    node = {
        "title": "复制子（replicon）",
        "explanation": "- **详细解释**：含一个起始区的复制单位。\n",
        "level": 3,
        "tags": [],
        "id": "kp_src_01",
        "summary_id": "sum_src_01",
    }
    candidates = build_term_candidates(
        [node], subject="分子生物学", chapter="第三章",
        source_type="ai", source_label="AI 生成（deepseek-chat）",
    )
    assert len(candidates) == 1
    c = candidates[0]
    assert c["source_type"] == "ai"
    assert c["source_label"] == "AI 生成（deepseek-chat）"
    assert c["source_summary_id"] == "sum_src_01"


def test_build_term_candidates_skips_empty_explanation_nodes():
    """L1/L2 章节节点（explanation 为空）不作为术语；有解释的 L3 正常生成。"""
    chapter_node = {
        "title": "第三章 DNA的复制",
        "explanation": "",
        "level": 2,
        "tags": [],
        "id": "kp_L2_ch3",
    }
    assert build_term_candidates([chapter_node], subject="分子生物学", chapter="") == []
    # 仅有空白的 explanation 同样跳过
    blank_node = dict(chapter_node, explanation="   \n")
    assert build_term_candidates([blank_node], subject="分子生物学", chapter="") == []
    # L3 有解释 -> 1 条候选
    leaf_node = {
        "title": "复制子（replicon）",
        "explanation": "#### 复制子（replicon）🔴【必考】\n"
                       "- **详细解释**：含一个起始区的复制单位。\n",
        "level": 3,
        "tags": ["必考"],
        "id": "kp_L3_rep",
    }
    cands = build_term_candidates([leaf_node], subject="分子生物学", chapter="第三章 DNA的复制")
    assert len(cands) == 1
    assert cands[0]["term"] == "复制子（replicon）"
    assert cands[0]["definition"].startswith("含一个起始区的复制单位")


def test_merge_candidate_keeps_first_source_label():
    """merge 保留首个非空 source_label；source_type 非空优先（a 优先）。"""
    c1 = {"term": "PCR", "aliases": [], "definition": "d", "full_explanation": "",
          "subject": "综合", "chapter": "", "source_node_id": None, "tags": [], "related_terms": [],
          "source_type": "ai", "source_label": "AI 生成（deepseek-chat）"}
    c2 = {"term": "PCR", "aliases": [], "definition": "dd", "full_explanation": "",
          "subject": "综合", "chapter": "", "source_node_id": None, "tags": [], "related_terms": [],
          "source_type": "", "source_label": ""}
    merged = merge_candidate(c1, c2)
    assert merged["source_type"] == "ai"
    assert merged["source_label"] == "AI 生成（deepseek-chat）"


def test_merge_candidate_fills_missing_source_from_second():
    """a 缺 source 标记时，b 的非空值补入。"""
    c1 = {"term": "PCR", "aliases": [], "definition": "d", "full_explanation": "",
          "subject": "综合", "chapter": "", "source_node_id": None, "tags": [], "related_terms": [],
          "source_type": "", "source_label": ""}
    c2 = {"term": "PCR", "aliases": [], "definition": "dd", "full_explanation": "",
          "subject": "综合", "chapter": "", "source_node_id": None, "tags": [], "related_terms": [],
          "source_type": "web", "source_label": "网络出处：example.com"}
    merged = merge_candidate(c1, c2)
    assert merged["source_type"] == "web"
    assert merged["source_label"] == "网络出处：example.com"


def test_merge_candidate_dedup():
    c1 = {"term": "复制子", "aliases": ["replicon"], "definition": "定义A", "full_explanation": "完整A",
          "subject": "分子生物学", "chapter": "第三章", "source_node_id": "n1", "tags": [], "related_terms": ["复制叉"]}
    c2 = {"term": "复制子", "aliases": ["replicon", "复制单位"], "definition": "定义B（更长）", "full_explanation": "完整B更长",
          "subject": "分子生物学", "chapter": "第三章", "source_node_id": "n2", "tags": ["必考"], "related_terms": ["Ori"]}
    merged = merge_candidate(c1, c2)
    assert set(merged["aliases"]) == {"replicon", "复制单位"}
    assert len(merged["definition"]) >= len("定义B（更长）")  # 保留更详细
    assert merged["source_node_id"] == "n1"  # 保留首个来源
    assert "复制叉" in merged["related_terms"] and "Ori" in merged["related_terms"]


def test_read_kgb_snapshot_roundtrip(tmp_path):
    """.kgb 快照导出后可按格式头读取校验。"""
    import json as _json
    from app.core.glossary.extractor import read_kgb_snapshot

    snap = {
        "format": "KGB-1.0",
        "updated_at": "2026-09-05T00:00:00",
        "subjects": ["分子生物学"],
        "total": 1,
        "entries": [{"term": "复制子", "definition": "含一个起始区的复制单位。"}],
    }
    p = tmp_path / "glossary_terms.kgb"
    p.write_text(_json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    data = read_kgb_snapshot(p)
    assert data["format"] == "KGB-1.0"
    assert data["entries"][0]["term"] == "复制子"


def test_read_kgb_snapshot_rejects_wrong_format(tmp_path):
    """非 KGB-1.0 头应报错。"""
    import json as _json
    from app.core.glossary.extractor import read_kgb_snapshot

    p = tmp_path / "glossary_terms.kgb"
    p.write_text(_json.dumps({"format": "OTHER-2.0", "entries": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_kgb_snapshot(p)


def test_read_kgb_snapshot_missing_file(tmp_path):
    """不存在文件报 FileNotFoundError。"""
    from app.core.glossary.extractor import read_kgb_snapshot
    with pytest.raises(FileNotFoundError):
        read_kgb_snapshot(tmp_path / "nope.kgb")


# ============================================================
# Round 26: tests added because these behaviours had NO test that could fail.
#
# Each one below was found by mutating app/core/glossary/extractor.py and
# observing that the suite stayed green. Three of the pre-existing assertions
# looked like they covered the behaviour but could not fail:
#
#   * `assert "**" not in definition` - the input never put `**` INSIDE the
#     captured value, only in the bullet label `**详细解释**`;
#   * `assert set(merged["aliases"]) == {...}` - converting to a set destroys
#     exactly the duplicate-ness that `_uniq` exists to remove;
#   * `assert len(merged["definition"]) >= len(B)` - true under BOTH branches
#     of the length comparison, so it never distinguishes them.
#
# A test that cannot fail on the behaviour it names is not coverage.
# ============================================================


def test_parse_explanation_bullets_strips_markers_inside_the_definition():
    """`**` inside the captured definition must be stripped.

    The older test only had `**` in the bullet LABEL (`**详细解释**`), which the
    regex consumes, so its `"**" not in definition` assertion could not fail.
    Here the emphasis is in the value, which is what the strip is for.
    """
    expl = "- **详细解释**：**DNA**是主要的遗传物质。\n"
    result = parse_explanation_bullets(expl)
    assert result["definition"] == "DNA是主要的遗传物质。"
    assert "**" not in result["definition"]


def test_parse_explanation_bullets_plain_drops_heading_lines():
    """The no-bullet fallback must drop `#` heading lines.

    The older plain test fed text with no headings at all, so the filter had
    nothing to do and removing it changed nothing.
    """
    expl = "#### 第三章 DNA的复制\nDNA的双螺旋结构由两条链组成。\n\n两条链反向平行。"
    result = parse_explanation_bullets(expl)
    assert result["definition"] == "DNA的双螺旋结构由两条链组成。\n两条链反向平行。"
    assert "#" not in result["definition"]


def test_merge_candidate_dedups_aliases_as_an_ordered_list():
    """`_uniq` must remove duplicates while preserving first-seen order.

    `test_merge_candidate_dedup` compares with `set(...)`, which hides exactly
    the duplicates under test — deleting `_uniq` left it green.
    """
    c1 = {"term": "复制子", "aliases": ["replicon"], "definition": "定义A",
          "full_explanation": "完整A", "subject": "分子生物学", "chapter": "第三章",
          "source_node_id": "n1", "tags": ["必考"], "related_terms": ["复制叉"]}
    c2 = {"term": "复制子", "aliases": ["replicon", "复制单位"], "definition": "定义B",
          "full_explanation": "完整B", "subject": "分子生物学", "chapter": "第三章",
          "source_node_id": "n2", "tags": ["必考", "重点"], "related_terms": ["复制叉", "Ori"]}
    merged = merge_candidate(c1, c2)
    assert merged["aliases"] == ["replicon", "复制单位"]  # exact, ordered, deduped
    assert merged["tags"] == ["必考", "重点"]
    assert merged["related_terms"] == ["复制叉", "Ori"]
    assert len(merged["aliases"]) == 2


def test_build_term_candidates_skips_l1_overview_nodes():
    """L1 nodes whose title is a 总结/大全 overview must not become terms.

    This is a deliberate business rule with no test at all: the `continue` could
    be deleted and the whole glossary suite stayed green.
    """
    def _node(title: str, level: int = 1) -> dict:
        return {"title": title, "explanation": "- **详细解释**：有内容。", "level": level,
                "tags": [], "id": f"kp_{title}", "summary_id": "s1"}

    assert build_term_candidates([_node("第三章总结")], subject="分子生物学") == []
    assert build_term_candidates([_node("分子生物学大全")], subject="分子生物学") == []
    # control: an ordinary L1 node is still kept, so the rule is not "skip all L1"
    kept = build_term_candidates([_node("第三章 DNA的复制")], subject="分子生物学")
    assert len(kept) == 1 and kept[0]["term"] == "第三章 DNA的复制"
    # the rule is L1-only: an L3 node literally named 总结 is still a term
    kept_l3 = build_term_candidates([_node("总结", level=3)], subject="分子生物学")
    assert len(kept_l3) == 1


def test_merge_candidate_keeps_the_longer_definition_from_either_side():
    """Merge must keep whichever definition is longer, in both directions.

    The older assertion `len(merged["definition"]) >= len(B)` held under both
    branches of the length comparison, so it never proved the comparison ran.
    """
    def _c(definition: str, full: str, node: str) -> dict:
        return {"term": "PCR", "aliases": [], "definition": definition,
                "full_explanation": full, "subject": "综合", "chapter": "",
                "source_node_id": node, "tags": [], "related_terms": []}

    short_a = _c("短", "完整短", "n1")
    long_b = _c("长定义-更详细", "完整长-更详细", "n2")
    merged = merge_candidate(short_a, long_b)
    assert merged["definition"] == "长定义-更详细", "b is longer, so b wins"
    assert merged["full_explanation"] == "完整长-更详细"
    assert merged["source_node_id"] == "n1", "but the first source is still kept"

    long_a = _c("长定义-更详细", "完整长-更详细", "n1")
    short_b = _c("短", "完整短", "n2")
    merged2 = merge_candidate(long_a, short_b)
    assert merged2["definition"] == "长定义-更详细", "a is longer, so a stays"
    assert merged2["full_explanation"] == "完整长-更详细"