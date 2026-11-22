"""Identify PDF pages whose visible content lacks a useful text layer."""


def _image_coverage(page, images) -> float:
    """Area of the union of raster image boxes, clipped to the page."""
    bounds = page.rect
    rectangles = []
    for info in images:
        x0, y0, x1, y1 = info["bbox"]
        x0, x1 = max(bounds.x0, x0), min(bounds.x1, x1)
        y0, y1 = max(bounds.y0, y0), min(bounds.y1, y1)
        if x1 > x0 and y1 > y0:
            rectangles.append((x0, y0, x1, y1))
    if not rectangles:
        return 0.0
    edges = sorted({x for x0, _, x1, _ in rectangles for x in (x0, x1)})
    area = 0.0
    for left, right in zip(edges, edges[1:]):
        middle = (left + right) / 2
        spans = sorted((y0, y1) for x0, y0, x1, y1 in rectangles if x0 <= middle < x1)
        if not spans:
            continue
        low, high = spans[0]
        height = 0.0
        for y0, y1 in spans[1:]:
            if y0 > high:
                height += high - low
                low, high = y0, y1
            else:
                high = max(high, y1)
        area += (height + high - low) * (right - left)
    return area / max(1.0, bounds.get_area())


def page_needs_ocr(page) -> bool:
    text = page.get_text().strip()
    images = page.get_image_info()
    if not images:
        return not text and bool(page.get_drawings())
    if not text:
        return True
    coverage = _image_coverage(page, images)
    return (coverage >= 0.35 and len(text) < 80) or (coverage >= 0.65 and len(text) < 500) or (
        0.35 <= coverage < 0.65 and len(text) >= 80
    )


def page_has_partial_scan(page) -> bool:
    """A substantial raster section shares this page with searchable prose."""
    text = page.get_text().strip()
    if len(text) < 80:
        return False
    return 0.35 <= _image_coverage(page, page.get_image_info()) < 0.65


def partial_scan_bbox(page):
    """Bound raster regions for a supplementary OCR pass on a mixed page."""
    import fitz

    bounds = page.rect
    regions = [fitz.Rect(info["bbox"]) & bounds for info in page.get_image_info()]
    regions = [region for region in regions if region.get_area() > 0]
    if not regions:
        return bounds
    substantial = [region for region in regions
                   if region.get_area() >= bounds.get_area() * 0.02]
    if sum(region.get_area() for region in substantial) >= bounds.get_area() * 0.25:
        regions = substantial
    combined = regions[0]
    for region in regions[1:]:
        combined |= region
    return combined


def inspect_pdf(file_path: str) -> tuple[list[bool], bool]:
    """Classify all pages without holding a PyMuPDF document across threads."""
    import fitz

    with fitz.open(file_path) as document:
        flags = [page_needs_ocr(page) for page in document]
        has_digital = any(
            (not flags[index] or page_has_partial_scan(page)) and page.get_text().strip()
            for index, page in enumerate(document)
        )
    return flags, bool(has_digital)
