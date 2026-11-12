"""VLM band validation for the opt-in row grouping pilot."""
from app.modules.analysis.project_row_grouping import vlm_band_groups


def test_valid_vlm_rows_keep_cell_group_without_guessing_columns():
    text = (
        "2016.8.1~2017.7.30 | 범정부 플랫폼 1단계 | 미래창조과학부\n"
        "2016.2.1~7.30 | 부산 고도화 연구 | KT"
    )
    rows = vlm_band_groups(text, doc_id="doc", page_no=1, first_row=1, last_row=2)
    assert len(rows) == 2
    assert rows[0]["cells"] == [
        "2016.8.1~2017.7.30", "범정부 플랫폼 1단계", "미래창조과학부"
    ]
    assert rows[1]["cells"][0] == "2016.2.1~7.30"
    assert rows[0]["fields"] == {}
    assert not rows[0]["column_mapping_verified"]


def test_reject_vlm_row_count_mismatch():
    text = "2020.2.1~4.30 | 블록체인 연구 | 기관"
    assert vlm_band_groups(text, doc_id="doc", page_no=1,
                           first_row=1, last_row=2) == []
