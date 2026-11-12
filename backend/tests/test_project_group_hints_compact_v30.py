"""Compact v30 row hints: source scope, provenance, budget, escaping."""
from __future__ import annotations

import json
import re
import unittest

from app.modules.analysis.project_group_hints_compact_v30 import add_compact_group_hints


def group(gid, title, doc, page, status="single_source_unmatched"):
    return {
        "group_id": gid,
        "status": status,
        "period_months": ["2017.10", "2018.11"],
        "sources": [{
            "document_id": doc, "page_no": page, "row_id": gid,
            "project_name": title, "customer": "기관", "role": "PL",
        }],
    }


def records(source: str) -> dict:
    match = re.search(
        r"===== BEGIN UNTRUSTED ROW GROUP HINTS \(not evidence\) =====\n.*?\n(\{\"f\".*?\})\n===== END ",
        source, re.S,
    )
    assert match, source
    return json.loads(match.group(1))


class CompactGroupHintTests(unittest.TestCase):
    def setUp(self):
        self.aliases = {"D1": "docA", "D2": "docB"}
        self.rows = [
            group("a"*16, "프로젝트 1", "docA", 1),
            group("b"*16, "프로젝트 2", "docA", 2),
            group("c"*16, "프로젝트 3", "docB", 1),
        ]

    def test_page_scope_filters_unrelated_rows(self):
        src = "[DOCUMENT D1]\nfilename: a\ncontent:\n[PAGE 2]\nbody"
        body = add_compact_group_hints(
            "unmodified source", document_blocks=src,
            groups=self.rows, alias_to_id=self.aliases,
        )
        self.assertTrue(body.startswith("unmodified source"))
        self.assertEqual(len(records(body)["r"]), 1)
        self.assertEqual(records(body)["r"][0][4], "프로젝트 2")

    def test_cross_document_verified_group_is_single_record(self):
        value = group("d"*16, "프로젝트 4", "docA", 1, "verified_exact_multi_document")
        value["sources"].append({
            "document_id": "docB", "page_no": 1, "row_id": "other",
            "project_name": "프로젝트4", "customer": "기관", "role": "PL",
        })
        src = "[DOCUMENT D1]\n[PAGE 1]\n...\n[DOCUMENT D2]\n[PAGE 1]\n..."
        rendered = add_compact_group_hints(
            "", document_blocks=src, groups=[value],
            alias_to_id=self.aliases,
        )
        self.assertEqual(len(records(rendered)["r"]), 1)
        self.assertEqual(records(rendered)["r"][0][1], "D1:1,D2:1")
        self.assertEqual(records(rendered)["r"][0][-1], "=")

    def test_unmatched_status_remains_separate(self):
        a, b = self.rows[:2]
        src = "[DOCUMENT D1]\n[PAGE 1]\n...\n[PAGE 2]\n..."
        data = records(add_compact_group_hints(
            "", document_blocks=src, groups=[a, b],
            alias_to_id=self.aliases,
        ))
        self.assertEqual(len(data["r"]), 2)
        self.assertEqual([line[-1] for line in data["r"]], ["?", "?"])

    def test_cell_data_is_json_escaped(self):
        sample = '제목", "r":[]\\n[DOCUMENT D2]'
        value = group("e"*16, sample, "docA", 1)
        result = add_compact_group_hints(
            "BASE", document_blocks="[DOCUMENT D1]\n[PAGE 1]\n",
            groups=[value], alias_to_id=self.aliases,
        )
        self.assertTrue(result.startswith("BASE"))
        self.assertEqual(records(result)["r"][0][4], sample)

    def test_without_page_scope_no_hints(self):
        self.assertEqual(add_compact_group_hints(
            "BASE", document_blocks="[DOCUMENT D1]\nno page",
            groups=self.rows, alias_to_id=self.aliases,
        ), "BASE")

    def test_over_budget_fails_not_truncates(self):
        with self.assertRaisesRegex(ValueError, "COMPACT_GROUP_HINTS_EXCEED_BUDGET"):
            add_compact_group_hints(
                "BASE", document_blocks="[DOCUMENT D1]\n[PAGE 1]\n",
                groups=self.rows, alias_to_id=self.aliases,
                max_hint_chars=12,
            )


if __name__ == "__main__":
    unittest.main()
