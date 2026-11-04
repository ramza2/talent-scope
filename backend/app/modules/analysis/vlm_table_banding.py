"""Opt-in row-banded image crops for scanned PDF tables.

Only strong, long horizontal rules are used. Ambiguous pages are not split.
No data is written here; callers decide whether to use the resulting crops.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class TableRowBand:
    """Crop coordinates in PDF page points, containing complete table rows."""

    clip: pymupdf.Rect
    first_row: int
    last_row: int


def detect_table_row_bands(
    page: pymupdf.Page,
    *,
    dpi: int,
    rows_per_band: int = 5,
    max_bands: int = 6,
) -> list[TableRowBand]:
    """Find ruled table rows from a grayscale raster, or fail closed.

    Requiring at least five aligned, long horizontal rules intentionally
    excludes borderless tables and uncertain scans.
    """
    if rows_per_band < 2 or max_bands < 2:
        return []
    dpi = max(120, min(int(dpi), 240))
    zoom = dpi / 72.0
    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    width, height = pix.width, pix.height
    if width < 400 or height < 400 or width * height > 8_000_000:
        return []

    pixels = memoryview(pix.samples)
    x_start, x_end = width // 12, width - width // 12
    stride = 3
    # Scanned document rules can be narrower and lighter than vector rules.
    min_hits = int(((x_end - x_start) / stride) * 0.42)
    candidates: list[tuple[int, int, int]] = []
    for y in range(height):
        row_start = y * pix.stride
        hits = 0
        first = width
        last = 0
        for x in range(x_start, x_end, stride):
            if pixels[row_start + x] < 205:
                hits += 1
                if x < first:
                    first = x
                last = x
        if hits >= min_hits:
            candidates.append((y, first, last))

    # Coalesce a printed rule spanning 1-3 raster rows into one boundary.
    rules: list[tuple[int, int, int]] = []
    group: list[tuple[int, int, int]] = []
    for item in candidates:
        if group and item[0] > group[-1][0] + 2:
            if len(group) <= 8:
                best = max(group, key=lambda entry: entry[2] - entry[1])
                rules.append(best)
            group = []
        group.append(item)
    if group and len(group) <= 8:
        rules.append(max(group, key=lambda entry: entry[2] - entry[1]))
    if len(rules) < 5:
        return []

    # Choose the longest table run, without crossing a large whitespace gap.
    # Also separates the table from wide footer verification/barcode blocks.
    max_row_gap = int(dpi * 0.70)
    runs: list[list[tuple[int, int, int]]] = []
    run: list[tuple[int, int, int]] = []
    for rule in rules:
        if run and rule[0] - run[-1][0] > max_row_gap:
            runs.append(run)
            run = []
        run.append(rule)
    if run:
        runs.append(run)
    rules = max(runs, key=len)
    if len(rules) < 5:
        return []

    # Reject scattered underlines that do not share the same table width.
    left = sorted(rule[1] for rule in rules)[len(rules) // 2]
    right = sorted(rule[2] for rule in rules)[len(rules) // 2]
    if right - left < width * 0.40:
        return []
    if any(
        abs(rule[1] - left) > width * 0.16
        or abs(rule[2] - right) > width * 0.10
        for rule in rules
    ):
        return []
    if rules[-1][0] - rules[0][0] < height * 0.25:
        return []

    row_count = len(rules) - 1
    band_count = (row_count + rows_per_band - 1) // rows_per_band
    if band_count < 2 or band_count > max_bands:
        return []

    bands: list[TableRowBand] = []
    for first_row in range(0, row_count, rows_per_band):
        last_row = min(row_count, first_row + rows_per_band)
        y_top = (rules[first_row][0] + 2) / zoom
        y_bottom = (rules[last_row][0] - 2) / zoom
        if y_bottom <= y_top:
            return []
        # Keep the full table width, including any small overhanging text.
        clip = pymupdf.Rect(page.rect.x0, y_top, page.rect.x1, y_bottom)
        bands.append(
            TableRowBand(clip=clip, first_row=first_row + 1, last_row=last_row)
        )
    return bands


def is_valid_table_band_transcription(text: str, *, expected_rows: int) -> bool:
    """Require exactly one multi-column output line per detected table row."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != expected_rows or any("|" not in line for line in lines):
        return False
    rows = [line.strip("|").split("|") for line in lines]
    counts = [len(cells) for cells in rows]
    expected = max(set(counts), key=counts.count)
    if expected < 3 or expected > 12 or any(count != expected for count in counts):
        return False
    return all(any(cell.strip() for cell in cells) for cells in rows)
