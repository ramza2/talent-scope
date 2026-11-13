"""Pure tests for conservative row grouping; no DB/LLM or files."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.analysis.project_row_grouping import native_table_groups


class FakeTable:
    def __init__(self, rows):
        self.rows = [SimpleNamespace(bbox=(1, i * 20, 200, i * 20 + 15))
                     for i in range(len(rows))]
        self.data = rows

    def extract(self):
        return self.data


class FakePage:
    def __init__(self, tables):
        self.tables = tables

    def find_tables(self):
        return SimpleNamespace(tables=self.tables)


def test_native_groups_period_project_role_customer_in_same_row():
    table = FakeTable([
        ["참여기간", "유형", "사   업   명", "담당업무", "발주처"],
        ["2017.10.1~2018.11.30", "개발", "범정부 플랫폼 2단계", "품질관리 PL", "미래창조과학부"],
        ["2016.8.1~2017.7.30", "개발", "범정부 플랫폼 1단계", "분석설계 PL", "미래창조과학부"],
        ["2016.2.1~7.30", "컨설팅", "부산직할시 고도화", "PM", "KT"],
        ["2016.1.10~6.30", "컨설팅", "KT 재난안전", "PM", "KT"],
    ])
    rows = native_table_groups(FakePage([table]), "doc", 1)
    assert len(rows) == 4
    assert rows[0]["fields"]["period"] == "2017.10.1~2018.11.30"
    assert rows[1]["fields"]["project_name"] == "범정부 플랫폼 1단계"
    assert rows[1]["fields"]["period"] == "2016.8.1~2017.7.30"
    assert rows[2]["fields"]["period"] == "2016.2.1~7.30"
    assert rows[3]["fields"]["customer"] == "KT"
    assert rows[0]["bbox"] is not None


def test_unknown_header_fails_closed():
    table = FakeTable([["기간", "구분", "내용"], ["2020.1.1~3.31", "개발", "사업"]])
    assert native_table_groups(FakePage([table]), "d", 2) == []


def test_split_rows_never_fused_by_guessing():
    table = FakeTable([
        ["참여기간", "사업명", "발주처"],
        ["2016.8.1~2017.7.30", "", "미래창조과학부"],
        ["", "범정부 플랫폼 1단계", "미래창조과학부"],
    ])
    assert native_table_groups(FakePage([table]), "d", 1) == []


def test_row_ids_are_stable_for_identical_source():
    table = FakeTable([["참여기간", "사업명"], ["2020.2.1~4.30", "블록체인 연구"]])
    page = FakePage([table])
    assert native_table_groups(page, "d", 1) == native_table_groups(page, "d", 1)


def test_v30_strict_rows_fall_back_on_split_or_incomplete_project():
    table = FakeTable([
        ["참여기간", "사업명", "발주처"],
        ["2020.01~2020.05", "정상프로젝트 사업", "공공기관"],
        ["2020.06~2020.09", "", "공공기관"],
    ])
    # v25 exploratory scans can omit the incomplete row, but v30 must
    # never silently persist the valid subset as *all* source projects.
    assert len(native_table_groups(FakePage([table]), "d", 1)) == 1
    with pytest.raises(ValueError, match="V30_UNVERIFIED_NATIVE_TABLE_ROW"):
        native_table_groups(FakePage([table]), "d", 1, strict_rows=True)
