"""Code business logic."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db_errors import is_unique_violation
from app.core.exceptions import (
    CodeAlreadyExistsError,
    CodeHierarchyCycleError,
    InvalidCodeParentError,
    InvalidCodeTypeError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models.code import CodeMaster
from app.modules.codes.normalize import prepare_aliases
from app.modules.codes.repository import CodeRepository
from app.modules.codes.schemas import (
    ALLOWED_CODE_TYPES,
    AliasReplaceRequest,
    CodeCreateRequest,
    CodeItem,
    CodeUpdateRequest,
)


class CodeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = CodeRepository(db)

    def _to_item(self, row: CodeMaster, aliases: list[str]) -> CodeItem:
        return CodeItem(
            code=row.code,
            type=row.code_type,  # type: ignore[arg-type]
            name=row.name,
            description=row.description,
            parent_code=row.parent_code,
            sort_order=row.sort_order,
            aliases=aliases,
            is_active=row.is_active,
        )

    def _snapshot(self, row: CodeMaster, aliases: list[str]) -> dict:
        return {
            "code": row.code,
            "type": row.code_type,
            "name": row.name,
            "description": row.description,
            "parent_code": row.parent_code,
            "sort_order": row.sort_order,
            "aliases": aliases,
            "is_active": row.is_active,
        }

    def _validate_parent(
        self,
        *,
        code: str | None,
        code_type: str,
        parent_code: str | None,
    ) -> None:
        if parent_code is None:
            return
        if code is not None and parent_code == code:
            raise InvalidCodeParentError("자기 자신을 상위 코드로 설정할 수 없습니다.")
        parent = self.repo.get_by_code(parent_code)
        if parent is None:
            raise InvalidCodeParentError("상위 코드가 존재하지 않습니다.")
        if parent.code_type != code_type:
            raise InvalidCodeParentError("상위 코드와 유형이 일치해야 합니다.")
        if code is not None and self._creates_cycle(code, parent_code):
            raise CodeHierarchyCycleError()

    def _creates_cycle(self, code: str, new_parent: str) -> bool:
        current: str | None = new_parent
        seen: set[str] = set()
        while current:
            if current == code:
                return True
            if current in seen:
                return True
            seen.add(current)
            row = self.repo.get_by_code(current)
            if row is None:
                break
            current = row.parent_code
        return False

    def list_codes(
        self,
        *,
        code_type: str | None = None,
        q: str | None = None,
        parent_code: str | None = None,
        active: bool | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[CodeItem]:
        if code_type is not None and code_type not in ALLOWED_CODE_TYPES:
            raise InvalidCodeTypeError()
        limit = 1000
        offset = 0
        if page is not None or page_size is not None:
            size = page_size or 50
            if size < 1 or size > 200:
                raise ValidationAppError("page_size는 1~200 사이여야 합니다.")
            p = page or 1
            if p < 1:
                raise ValidationAppError("page는 1 이상이어야 합니다.")
            limit = size
            offset = (p - 1) * size

        rows = self.repo.list_codes(
            code_type=code_type,
            q=q,
            parent_code=parent_code,
            active=active,
            limit=limit,
            offset=offset,
        )
        aliases_map = self.repo.aliases_for_codes([r.code for r in rows])
        return [self._to_item(r, aliases_map.get(r.code, [])) for r in rows]

    def get_code(self, code: str) -> CodeItem:
        row = self.repo.get_by_code(code)
        if row is None:
            raise NotFoundError("코드를 찾을 수 없습니다.")
        aliases = self.repo.aliases_for_codes([code]).get(code, [])
        return self._to_item(row, aliases)

    def create_code(self, payload: CodeCreateRequest, actor_user_id: UUID) -> CodeItem:
        if payload.type not in ALLOWED_CODE_TYPES:
            raise InvalidCodeTypeError()
        if self.repo.get_by_code(payload.code) is not None:
            raise CodeAlreadyExistsError()
        self._validate_parent(
            code=payload.code,
            code_type=payload.type,
            parent_code=payload.parent_code,
        )
        pairs = prepare_aliases(payload.aliases, standard_name=payload.name)
        try:
            row = self.repo.create_code(
                code=payload.code,
                code_type=payload.type,
                name=payload.name,
                description=payload.description,
                parent_code=payload.parent_code,
                sort_order=payload.sort_order,
            )
            self.repo.replace_aliases(payload.code, pairs)
            aliases = [a for a, _ in pairs]
            self.repo.add_audit(
                action_type="CODE_CREATE",
                actor_user_id=actor_user_id,
                code=payload.code,
                after=self._snapshot(row, aliases),
            )
            self.db.commit()
            self.db.refresh(row)
        except IntegrityError as exc:
            self.db.rollback()
            if is_unique_violation(exc, "code_master_pkey"):
                raise CodeAlreadyExistsError() from exc
            raise
        return self._to_item(row, aliases)

    def update_code(
        self,
        code: str,
        payload: CodeUpdateRequest,
        actor_user_id: UUID,
    ) -> CodeItem:
        row = self.repo.get_by_code(code)
        if row is None:
            raise NotFoundError("코드를 찾을 수 없습니다.")
        aliases = self.repo.aliases_for_codes([code]).get(code, [])
        before = self._snapshot(row, aliases)

        fields_set = payload.model_fields_set
        if "name" in fields_set and payload.name is not None:
            row.name = payload.name
        if "description" in fields_set:
            row.description = payload.description
        if "sort_order" in fields_set and payload.sort_order is not None:
            row.sort_order = payload.sort_order
        if "is_active" in fields_set and payload.is_active is not None:
            row.is_active = payload.is_active
        if "parent_code" in fields_set:
            self._validate_parent(
                code=code,
                code_type=row.code_type,
                parent_code=payload.parent_code,
            )
            row.parent_code = payload.parent_code

        self.db.add(row)
        self.db.flush()
        after_aliases = self.repo.aliases_for_codes([code]).get(code, [])
        after = self._snapshot(row, after_aliases)
        self.repo.add_audit(
            action_type="CODE_UPDATE",
            actor_user_id=actor_user_id,
            code=code,
            before=before,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._to_item(row, after_aliases)

    def replace_aliases(
        self,
        code: str,
        payload: AliasReplaceRequest,
        actor_user_id: UUID,
    ) -> CodeItem:
        row = self.repo.get_by_code(code)
        if row is None:
            raise NotFoundError("코드를 찾을 수 없습니다.")
        before_aliases = self.repo.aliases_for_codes([code]).get(code, [])
        before = self._snapshot(row, before_aliases)
        pairs = prepare_aliases(payload.aliases, standard_name=row.name)
        self.repo.replace_aliases(code, pairs)
        aliases = [a for a, _ in pairs]
        after = self._snapshot(row, aliases)
        self.repo.add_audit(
            action_type="CODE_ALIAS_REPLACE",
            actor_user_id=actor_user_id,
            code=code,
            before=before,
            after=after,
        )
        self.db.commit()
        return self._to_item(row, aliases)
