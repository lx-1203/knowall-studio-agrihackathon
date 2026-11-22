"""Conservative extraction of explicitly connected diagram cards."""

from PIL import Image, ImageDraw

from app.core.parsing.diagram_relations import extract_diagram_relations
from app.core.parsing.local_ocr import OCRLine, OCRPage


def _cards(connected=True):
    image = Image.new("RGB", (380, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 35, 140, 105), outline="black", width=3)
    draw.rectangle((240, 35, 350, 105), outline="black", width=3)
    if connected:
        draw.line((140, 70, 240, 70), fill="black", width=3)
    page = OCRPage(
        [OCRLine("原因", (59, 61, 96, 79), 0.99), OCRLine("结果", (278, 61, 314, 79), 0.99)],
        0,
    )
    return image, page


def test_visible_connector_between_two_framed_labels_is_reported():
    image, page = _cards()

    result = extract_diagram_relations(image, page)

    assert [node.text for node in result.nodes] == ["原因", "结果"]
    assert len(result.edges) == 1
    assert result.edges[0].source == result.nodes[0].id
    assert result.edges[0].target == result.nodes[1].id
    assert result.edges[0].relation == "connected"


def test_adjacent_cards_without_visible_connector_have_no_relation():
    image, page = _cards(connected=False)

    result = extract_diagram_relations(image, page)

    assert len(result.nodes) == 2
    assert result.edges == []


def test_broken_connector_is_not_inferred_from_alignment():
    image, page = _cards()
    ImageDraw.Draw(image).rectangle((180, 65, 205, 75), fill="white")

    assert extract_diagram_relations(image, page).edges == []


def test_plain_text_does_not_become_diagram_nodes():
    image = Image.new("RGB", (380, 150), "white")
    page = OCRPage(
        [OCRLine("原因", (59, 61, 96, 79), 0.99), OCRLine("结果", (278, 61, 314, 79), 0.99)],
        0,
    )

    result = extract_diagram_relations(image, page)

    assert result.nodes == []
    assert result.edges == []


def test_table_grid_is_not_interpreted_as_connected_cards():
    image = Image.new("RGB", (380, 150), "white")
    draw = ImageDraw.Draw(image)
    for x in (30, 190, 350):
        draw.line((x, 35, x, 105), fill="black", width=3)
    for y in (35, 105):
        draw.line((30, y, 350, y), fill="black", width=3)
    page = OCRPage(
        [OCRLine("课程", (75, 61, 111, 79), 0.99), OCRLine("学分", (245, 61, 281, 79), 0.99)],
        0,
    )

    result = extract_diagram_relations(image, page)

    assert result.edges == []


def test_nearby_rows_do_not_create_a_spurious_spanning_node():
    image = Image.new("RGB", (250, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 180, 95), outline="black", width=3)
    draw.rectangle((30, 110, 180, 175), outline="black", width=3)
    page = OCRPage(
        [OCRLine("上节点", (65, 53, 125, 72), 0.99),
         OCRLine("下节点", (65, 133, 125, 152), 0.99)],
        0,
    )

    result = extract_diagram_relations(image, page)

    assert [node.text for node in result.nodes] == ["上节点", "下节点"]
    assert result.edges == []


def test_ocr_label_crossing_frame_border_cannot_anchor_an_edge():
    image, page = _cards()
    page.lines[0] = OCRLine("跨框标签", (45, 61, 165, 79), 0.99)

    result = extract_diagram_relations(image, page)

    assert [node.text for node in result.nodes] == ["结果"]
    assert result.edges == []
