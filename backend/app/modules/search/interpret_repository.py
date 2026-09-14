"""Read-only code catalog helpers for search interpret."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.code import CodeAlias, CodeMaster
from app.modules.search.interpret_policy import INTERPRET_CODE_TYPES


@dataclass(frozen=True, slots=True)
class CatalogCode:
    code: str
    code_type: str
    name: str
    sort_order: int
    aliases: tuple[str, ...]


class SearchInterpretRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def load_active_catalog(self) -> list[CatalogCode]:
        rows = list(
            self.db.execute(
                select(CodeMaster)
                .where(
                    CodeMaster.code_type.in_(INTERPRET_CODE_TYPES),
                    CodeMaster.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        type_rank = {t: i for i, t in enumerate(INTERPRET_CODE_TYPES)}
        rows.sort(
            key=lambda r: (
                type_rank.get(r.code_type, 999),
                int(r.sort_order or 0),
                (r.name or "").casefold(),
                r.code,
            )
        )
        codes = [r.code for r in rows]
        alias_map = self._aliases_for_codes(codes)
        return [
            CatalogCode(
                code=r.code,
                code_type=r.code_type,
                name=r.name,
                sort_order=int(r.sort_order or 0),
                aliases=tuple(alias_map.get(r.code, ())),
            )
            for r in rows
        ]

    def fetch_active_by_codes(self, codes: list[str]) -> dict[str, CodeMaster]:
        if not codes:
            return {}
        rows = list(
            self.db.execute(
                select(CodeMaster).where(
                    CodeMaster.code.in_(list(codes)),
                    CodeMaster.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        return {r.code: r for r in rows}

    def _aliases_for_codes(self, codes: list[str]) -> dict[str, tuple[str, ...]]:
        if not codes:
            return {}
        rows = list(
            self.db.execute(
                select(CodeAlias.code, CodeAlias.alias)
                .where(CodeAlias.code.in_(codes))
                .order_by(CodeAlias.code.asc(), CodeAlias.alias.asc())
            ).all()
        )
        out: dict[str, list[str]] = {}
        for code, alias in rows:
            text = (alias or "").strip()
            if not text:
                continue
            bucket = out.setdefault(code, [])
            if text not in bucket:
                bucket.append(text)
        return {k: tuple(v) for k, v in out.items()}
