"""Opt-in row-banded image crops for scanned PDF tables.

Only strong, long horizontal rules are used. Ambiguous pages are not split.
No data is written here; callers decide whether to use the resulting crops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import pymupdf

# KOSA career pages that lost real cells often keep only Markdown scaffolding.
_MD_PIPE_LINE_RE = re.compile(r"\|")
_MD_EMPTYISH_LINE_RE = re.compile(r"^[\s|:\-]+$")
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_CAREER_ANCHOR_RE = re.compile(r"근무경력|재직기간|경력사항")
_KOSA_EMPLOYMENT_ANCHOR_RE = re.compile(
    r"근\s*무\s*경\s*력|재\s*직\s*기\s*간|경\s*력\s*사\s*항|"
    r"근\s*무\s*처|근\s*무\s*기\s*간|입\s*사\s*일|퇴\s*사\s*일"
)
_KOSA_DATED_CELL_RE = re.compile(r"(?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?")


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


def normalize_table_band_transcription(text: str) -> str:
    """Repair two repeated, unambiguous KOSA-style cell-boundary errors.

    All source characters are preserved. Mixed or uncertain layouts are left
    unchanged so that callers can reject them or use full-page fallback.
    """
    rows = [line.strip() for line in (text or "").splitlines() if line.strip()]
    cells_by_row = [
        [cell.strip() for cell in line.strip("|").split("|")] for line in rows
    ]
    if cells_by_row and all(
        len(cells) >= 4 and cells[-1].startswith(">") for cells in cells_by_row
    ):
        for cells in cells_by_row:
            trailing = cells.pop()
            cells[-1] = f"{cells[-1]} {trailing}".strip()
    # A narrow '확인' status cell can be joined to the project-name cell.
    # Split only when *every* row shows the same six-cell pattern.
    if cells_by_row and all(
        len(cells) == 6
        and cells[0].startswith("확인 ")
        and cells[0][len("확인 "):].strip()
        for cells in cells_by_row
    ):
        for cells in cells_by_row:
            cells[0:1] = ["확인", cells[0][len("확인 "):].strip()]
    return "\n".join(" | ".join(cells) for cells in cells_by_row)


def is_valid_table_band_transcription(
    text: str, *, expected_rows: int, expected_columns: int | None = None
) -> bool:
    """Require exactly one multi-column output line per detected table row."""
    normalized = normalize_table_band_transcription(text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) != expected_rows or any("|" not in line for line in lines):
        return False
    rows = [line.strip("|").split("|") for line in lines]
    counts = [len(cells) for cells in rows]
    expected = max(set(counts), key=counts.count)
    if expected < 3 or expected > 12 or any(count != expected for count in counts):
        return False
    if expected_columns is not None and expected != expected_columns:
        return False
    return all(any(cell.strip() for cell in cells) for cells in rows)


def is_kosa_empty_markdown_table_vlm_quality_issue(text: str) -> bool:
    """Detect DOC-KOSA VLM pages that are mostly empty Markdown table chrome.

    Real failure shape: thousands of characters / hundreds of lines that are
    almost only ``|`` scaffolding, while career anchors (근무경력) and year
    tokens (1990/2009) are missing. Fail closed on ambiguous pages.
    """
    raw = text or ""
    lines = raw.splitlines()
    if len(lines) < 40:
        return False
    pipe_lines = sum(1 for line in lines if _MD_PIPE_LINE_RE.search(line))
    if pipe_lines < len(lines) * 0.45:
        return False
    emptyish = 0
    content_chars = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or _MD_EMPTYISH_LINE_RE.match(stripped):
            emptyish += 1
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        nonempty = [
            cell
            for cell in cells
            if cell and not re.fullmatch(r"[\-:]+", cell)
        ]
        if not nonempty:
            emptyish += 1
        else:
            content_chars += sum(len(cell) for cell in nonempty)
    if emptyish < len(lines) * 0.50:
        return False
    if content_chars > max(220, int(len(raw) * 0.08)):
        return False
    has_career = bool(_CAREER_ANCHOR_RE.search(raw))
    has_year = bool(_YEAR_TOKEN_RE.search(raw))
    # Empty chrome pages typically lack both career headers and year tokens.
    return not (has_career and has_year)



def is_plausible_kosa_employment_recovery(text: str) -> bool:
    """Accept a re-read scanned career table only with independently visible dates.

    A lone birth date, a heading-only answer, empty Markdown scaffolding, or
    a project-only transcription must never replace the stored page content.
    This is a quality gate, not an employer-row count assertion.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 24000:
        return False
    if is_kosa_empty_markdown_table_vlm_quality_issue(raw):
        return False
    if not _KOSA_EMPLOYMENT_ANCHOR_RE.search(raw):
        return False
    dated = _KOSA_DATED_CELL_RE.findall(raw)
    if len(set(dated)) < 4:
        return False
    return sum(bool(_KOSA_DATED_CELL_RE.search(line)) for line in raw.splitlines()) >= 2



_KOSA_COMPLETE_WORK_RANGE_RE = re.compile(
    r"^\s*((?:19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2})"
    r"\s*[~～\-–—]\s*"
    r"((?:19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2})\s*$"
)
_KOSA_PROJECT_ONLY_FIELD_RE = re.compile(
    r"참여사업|참여기간|발주처|발주기관|고객사|프로젝트|기술경력|사업명"
)


def normalize_kosa_cropped_employment_rows(text: str) -> str | None:
    """Validate rule-free KOSA p1 work-table crop before replacing LLM input.

    A *dedicated, top-of-page* KOSA crop must transcribe at least three
    company / complete start-end date rows. Reject partial or mixed project
    tables. The leading section label is metadata for downstream source
    classification, never a reconstructed employer, date, or quote.
    This cannot assert that every row on the original image was recovered.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 12000 or is_kosa_empty_markdown_table_vlm_quality_issue(raw):
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines and lines[0].strip().replace(" ", "") == "근무경력":
        lines = lines[1:]
    if not 3 <= len(lines) <= 20:
        return None
    rows: list[str] = []
    for line in lines:
        cells = [part.strip() for part in line.strip("|").split("|")]
        if len(cells) not in (2, 3):
            return None
        company, period = cells[0], cells[1]
        if not 2 <= len(company) <= 100 or _KOSA_PROJECT_ONLY_FIELD_RE.search(line):
            return None
        match = _KOSA_COMPLETE_WORK_RANGE_RE.fullmatch(period)
        if not match:
            return None
        try:
            start = date.fromisoformat(match[1].replace(".", "-").replace("/", "-"))
            end = date.fromisoformat(match[2].replace(".", "-").replace("/", "-"))
        except ValueError:
            return None
        if end < start:
            return None
        rows.append(line)
    if len(set(rows)) != len(rows):
        return None
    return "근무경력\n" + "\n".join(rows)
