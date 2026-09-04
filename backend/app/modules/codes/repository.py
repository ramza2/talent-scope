"""Code master / alias DB access."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models.code import CodeAlias, CodeMaster
from app.db.models.revision import AuditLog
from app.modules.codes.normalize import normalize_alias


class CodeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_code(self, code: str) -> CodeMaster | None:
        return self.db.execute(
            select(CodeMaster).where(CodeMaster.code == code)
        ).scalar_one_or_none()

    def list_codes(
        self,
        *,
        code_type: str | None = None,
        q: str | None = None,
        parent_code: str | None = None,
        active: bool | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[CodeMaster]:
        stmt = select(CodeMaster)
        if code_type:
            stmt = stmt.where(CodeMaster.code_type == code_type)
        if parent_code is not None:
            stmt = stmt.where(CodeMaster.parent_code == parent_code)
        if active is not None:
            stmt = stmt.where(CodeMaster.is_active.is_(active))
        if q:
            pattern = f"%{q.strip()}%"
            alias_match = (
                select(CodeAlias.code)
                .where(
                    or_(
                        CodeAlias.alias.ilike(pattern),
                        CodeAlias.normalized_alias.ilike(normalize_alias(q)),
                        CodeAlias.normalized_alias.ilike(f"%{normalize_alias(q)}%"),
                    )
                )
                .distinct()
            )
            stmt = stmt.where(
                or_(
                    CodeMaster.name.ilike(pattern),
                    CodeMaster.code.ilike(pattern),
                    CodeMaster.code.in_(alias_match),
                )
            )
        stmt = (
            stmt.order_by(CodeMaster.sort_order.asc(), CodeMaster.name.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def aliases_for_codes(self, codes: list[str]) -> dict[str, list[str]]:
        if not codes:
            return {}
        rows = self.db.execute(
            select(CodeAlias.code, CodeAlias.alias)
            .where(CodeAlias.code.in_(codes))
            .order_by(CodeAlias.alias.asc())
        ).all()
        result: dict[str, list[str]] = {c: [] for c in codes}
        for code, alias in rows:
            result.setdefault(code, []).append(alias)
        return result

    def create_code(
        self,
        *,
        code: str,
        code_type: str,
        name: str,
        description: str | None,
        parent_code: str | None,
        sort_order: int,
        is_active: bool = True,
    ) -> CodeMaster:
        row = CodeMaster(
            code=code,
            code_type=code_type,
            name=name,
            description=description,
            parent_code=parent_code,
            sort_order=sort_order,
            is_active=is_active,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def replace_aliases(self, code: str, pairs: list[tuple[str, str]]) -> None:
        self.db.execute(delete(CodeAlias).where(CodeAlias.code == code))
        for alias, normalized in pairs:
            self.db.add(
                CodeAlias(code=code, alias=alias, normalized_alias=normalized)
            )
        self.db.flush()

    def add_audit(
        self,
        *,
        action_type: str,
        actor_user_id: UUID | None,
        code: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = {"code": code, **(metadata or {})}
        entry = AuditLog(
            user_id=actor_user_id,
            action_type=action_type,
            target_type="CODE",
            target_id=None,
            before_json=before,
            after_json=after,
            metadata_json=meta,
        )
        self.db.add(entry)

    def count_children(self, parent_code: str) -> int:
        return int(
            self.db.execute(
                select(func.count()).select_from(CodeMaster).where(
                    CodeMaster.parent_code == parent_code
                )
            ).scalar_one()
        )
