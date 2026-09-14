"""Natural-language search interpretation service (LLM → validated Query JSON)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.prompts import search_interpret_v1 as prompt_mod
from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.ai.providers.llm import LLMProvider, OpenAICompatibleLLMProvider
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    SearchInterpretationInvalidError,
    SearchInterpretationUnavailableError,
    SearchInvalidCodeError,
)
from app.modules.codes.normalize import normalize_alias
from app.modules.search.interpret_policy import (
    CODE_FIELD_EXPECTED_TYPE,
    SEARCH_INTERPRET_MAX_CODE_CONTEXT_CHARS,
    SEARCH_INTERPRET_PROMPT_VERSION,
    SEARCH_QUERY_VERSION,
    collect_codes_from_query_dict,
    executable_query_dict,
    normalize_assumptions,
)
from app.modules.search.interpret_repository import CatalogCode, SearchInterpretRepository
from app.modules.search.interpret_schemas import (
    SearchInterpretData,
    SearchInterpretLLMOutput,
    SearchInterpretRequest,
    SearchInterpretResponse,
)
from app.modules.search.query_schemas import SearchPeopleRequest

logger = logging.getLogger(__name__)


class SearchInterpretService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        repo: SearchInterpretRepository | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.llm = llm or OpenAICompatibleLLMProvider(self.settings)
        self.repo = repo or SearchInterpretRepository(db)

    def interpret(self, request: SearchInterpretRequest) -> SearchInterpretResponse:
        started = time.perf_counter()
        previous = request.previous_query

        if previous is not None:
            self._assert_client_query_codes(
                previous.model_dump(mode="python"),
                source="previous_query",
            )

        catalog = self.repo.load_active_catalog()
        # Close read TX before external LLM HTTP.
        self.db.commit()

        text_tokens = self._tokens_from_text(request.text)
        priority_codes = (
            collect_codes_from_query_dict(previous.model_dump(mode="python"))
            if previous is not None
            else set()
        )
        (
            catalog_text,
            catalog_truncated,
            catalog_code_count,
            included_catalog,
        ) = self._format_catalog(
            catalog,
            text_tokens=text_tokens,
            priority_codes=priority_codes,
        )

        previous_for_prompt = (
            executable_query_dict(previous.model_dump(mode="python"))
            if previous is not None
            else None
        )

        raw = self._call_llm(
            system_prompt=prompt_mod.SYSTEM_PROMPT,
            user_prompt=prompt_mod.build_user_prompt(
                text=request.text,
                code_catalog=catalog_text,
                previous_query=previous_for_prompt,
            ),
        )

        try:
            llm_out = SearchInterpretLLMOutput.model_validate(raw)
        except ValidationError:
            logger.info(
                "search_interpret invalid_llm_schema",
                extra={
                    "operation": "search_interpret",
                    "prompt_version": SEARCH_INTERPRET_PROMPT_VERSION,
                    "query_version": SEARCH_QUERY_VERSION,
                },
            )
            raise SearchInterpretationInvalidError() from None

        # Resolve only against codes actually present in the prompt subset.
        resolved = self._resolve_ai_codes(
            llm_out.model_dump(mode="python"),
            catalog=included_catalog,
        )

        try:
            people_req = SearchPeopleRequest.model_validate(
                executable_query_dict(resolved)
            )
        except ValidationError:
            logger.info(
                "search_interpret people_request_invalid",
                extra={
                    "operation": "search_interpret",
                    "prompt_version": SEARCH_INTERPRET_PROMPT_VERSION,
                },
            )
            raise SearchInterpretationInvalidError() from None

        self._assert_ai_codes_active(people_req)

        data = SearchInterpretData(
            query_version=SEARCH_QUERY_VERSION,  # type: ignore[arg-type]
            required=people_req.required,
            preferred=people_req.preferred,
            skill_match_mode=people_req.skill_match_mode,
            semantic_query=people_req.semantic_query,
            keyword_query=people_req.keyword_query,
            sort=people_req.sort,
            assumptions=normalize_assumptions(list(llm_out.assumptions)),
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "search_interpret completed",
            extra={
                "operation": "search_interpret",
                "prompt_version": SEARCH_INTERPRET_PROMPT_VERSION,
                "query_version": SEARCH_QUERY_VERSION,
                "text_char_count": len(request.text),
                "has_previous_query": previous is not None,
                "catalog_code_count": catalog_code_count,
                "catalog_truncated": catalog_truncated,
                "elapsed_ms": elapsed_ms,
                "llm_model": self.settings.llm_model,
            },
        )
        return SearchInterpretResponse(data=data)

    def _call_llm(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        try:
            result = self.llm.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                log_context={
                    "operation": "search_interpret",
                    "prompt_version": SEARCH_INTERPRET_PROMPT_VERSION,
                },
            )
        except AIResponseValidationError:
            # Subclass of AIProviderError — catch before the provider-unavailable branch.
            logger.info(
                "search_interpret provider_invalid_json",
                extra={
                    "operation": "search_interpret",
                    "prompt_version": SEARCH_INTERPRET_PROMPT_VERSION,
                },
            )
            raise SearchInterpretationInvalidError() from None
        except AIProviderError:
            logger.warning(
                "search_interpret provider_unavailable",
                extra={
                    "operation": "search_interpret",
                    "prompt_version": SEARCH_INTERPRET_PROMPT_VERSION,
                },
            )
            raise SearchInterpretationUnavailableError() from None
        if not isinstance(result, dict):
            raise SearchInterpretationInvalidError()
        return result

    def _assert_client_query_codes(self, data: dict[str, Any], *, source: str) -> None:
        try:
            people_req = SearchPeopleRequest.model_validate(executable_query_dict(data))
        except ValidationError as exc:
            raise SearchInvalidCodeError(
                f"{source}가 유효한 검색 조건이 아닙니다."
            ) from exc

        for block_name, block in (
            ("required", people_req.required),
            ("preferred", people_req.preferred),
        ):
            for field, expected_type in CODE_FIELD_EXPECTED_TYPE.items():
                values = list(getattr(block, field, []) or [])
                if not values:
                    continue
                active = self.repo.fetch_active_by_codes(values)
                for code in values:
                    row = active.get(code)
                    if row is None or row.code_type != expected_type:
                        raise SearchInvalidCodeError(
                            f"{source}에 유효하지 않은 코드가 포함되어 있습니다."
                        )

    def _assert_ai_codes_active(self, people_req: SearchPeopleRequest) -> None:
        for block in (people_req.required, people_req.preferred):
            for field, expected_type in CODE_FIELD_EXPECTED_TYPE.items():
                values = list(getattr(block, field, []) or [])
                if not values:
                    continue
                active = self.repo.fetch_active_by_codes(values)
                for code in values:
                    row = active.get(code)
                    if row is None or row.code_type != expected_type:
                        raise SearchInterpretationInvalidError()

    def _resolve_ai_codes(
        self,
        data: dict[str, Any],
        *,
        catalog: list[CatalogCode],
    ) -> dict[str, Any]:
        indexes = self._build_indexes(catalog)
        out: dict[str, Any] = {
            "required": dict(data.get("required") or {}),
            "preferred": dict(data.get("preferred") or {}),
            "skill_match_mode": data.get("skill_match_mode", "ANY"),
            "semantic_query": data.get("semantic_query"),
            "keyword_query": data.get("keyword_query"),
            "sort": data.get("sort", "RELEVANCE"),
            "assumptions": list(data.get("assumptions") or []),
        }
        for block_name in ("required", "preferred"):
            block = out[block_name]
            for field, expected_type in CODE_FIELD_EXPECTED_TYPE.items():
                raw_values = block.get(field) or []
                if not isinstance(raw_values, list):
                    raise SearchInterpretationInvalidError()
                resolved: list[str] = []
                seen: set[str] = set()
                for token in raw_values:
                    if not isinstance(token, str):
                        raise SearchInterpretationInvalidError()
                    code = self._resolve_token(
                        token, expected_type=expected_type, indexes=indexes
                    )
                    if code is None:
                        raise SearchInterpretationInvalidError()
                    if code not in seen:
                        seen.add(code)
                        resolved.append(code)
                block[field] = resolved
        return out

    def _build_indexes(self, catalog: list[CatalogCode]) -> dict[str, Any]:
        by_code: dict[str, CatalogCode] = {}
        name_index: dict[tuple[str, str], list[str]] = {}
        alias_index: dict[tuple[str, str], list[str]] = {}
        for item in catalog:
            by_code[item.code] = item
            name_key = (item.code_type, normalize_alias(item.name))
            name_index.setdefault(name_key, []).append(item.code)
            for alias in item.aliases:
                alias_key = (item.code_type, normalize_alias(alias))
                alias_index.setdefault(alias_key, []).append(item.code)
        return {
            "by_code": by_code,
            "name_index": name_index,
            "alias_index": alias_index,
        }

    def _resolve_token(
        self,
        token: str,
        *,
        expected_type: str,
        indexes: dict[str, Any],
    ) -> str | None:
        cleaned = token.strip()
        if not cleaned:
            return None
        by_code: dict[str, CatalogCode] = indexes["by_code"]
        hit = by_code.get(cleaned)
        if hit is not None:
            return hit.code if hit.code_type == expected_type else None

        norm = normalize_alias(cleaned)
        name_hits = indexes["name_index"].get((expected_type, norm), [])
        if len(name_hits) == 1:
            return name_hits[0]
        if len(name_hits) > 1:
            return None
        alias_hits = indexes["alias_index"].get((expected_type, norm), [])
        if len(alias_hits) == 1:
            return alias_hits[0]
        return None

    @staticmethod
    def _phrase_tokens(text: str) -> list[str]:
        """Deterministic tokenize for direct phrase mention matching.

        - normalize_alias for casefold/whitespace
        - split on non-alphanumeric boundaries (no external tokenizer)
        - keeps Korean/ASCII word tokens; rejects bare substring matches
        """
        normalized = normalize_alias(text)
        if not normalized:
            return []
        return re.findall(r"\w+", normalized, flags=re.UNICODE)

    @staticmethod
    def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
        """True when needle is a contiguous token subsequence of haystack."""
        if not needle:
            return False
        n = len(needle)
        limit = len(haystack) - n + 1
        for i in range(max(limit, 0)):
            if haystack[i : i + n] == needle:
                return True
        return False

    @classmethod
    def _tokens_from_text(cls, text: str) -> list[str]:
        """Ordered phrase tokens from user text (direct-mention matching)."""
        return cls._phrase_tokens(text)

    @staticmethod
    def _catalog_line(
        item: CatalogCode, *, max_len: int
    ) -> tuple[str, tuple[str, ...]] | None:
        """Build one catalog line; return line + aliases actually rendered."""
        aliases = list(item.aliases)
        while True:
            alias_part = "|".join(aliases)
            line = (
                f"{item.code}\t{item.name}\t{alias_part}"
                if alias_part
                else f"{item.code}\t{item.name}"
            )
            if len(line) <= max_len:
                return line, tuple(aliases)
            if not aliases:
                return None
            aliases.pop()  # aliases are already sorted; drop last for determinism

    def _format_catalog(
        self,
        catalog: list[CatalogCode],
        *,
        text_tokens: list[str],
        priority_codes: set[str],
        max_chars: int | None = None,
    ) -> tuple[str, bool, int, list[CatalogCode]]:
        """Format prompt catalog and return the exact included CatalogCode subset.

        Included items are sanitized so aliases match what was actually rendered
        into the prompt line (after deterministic alias shrinking).
        """
        budget = (
            SEARCH_INTERPRET_MAX_CODE_CONTEXT_CHARS if max_chars is None else max_chars
        )

        def mentions(item: CatalogCode) -> bool:
            # Direct phrase mention: candidate token sequence appears contiguously
            # in the user token sequence (code / standard name / alias).
            for cand in (item.code, item.name, *item.aliases):
                needle = SearchInterpretService._phrase_tokens(cand)
                if SearchInterpretService._contains_phrase(text_tokens, needle):
                    return True
            return False

        # Preserve priority: previous_query codes → text mentions → remainder.
        tier1 = [c for c in catalog if c.code in priority_codes]
        tier1_set = {c.code for c in tier1}
        tier2 = [c for c in catalog if c.code not in tier1_set and mentions(c)]
        tier2_set = {c.code for c in tier2}
        tier3 = [
            c for c in catalog if c.code not in tier1_set and c.code not in tier2_set
        ]
        ordered = tier1 + tier2 + tier3

        lines: list[str] = []
        included_items: list[CatalogCode] = []
        used = 0
        truncated = False
        current_type: str | None = None
        for item in ordered:
            header: str | None = None
            header_cost = 0
            if item.code_type != current_type:
                header = f"[{item.code_type}]"
                header_cost = len(header) + (1 if lines else 0)

            newline_before_line = 1 if (lines or header) else 0
            remaining = budget - used - header_cost - newline_before_line
            if remaining <= 0:
                truncated = True
                break

            rendered = self._catalog_line(item, max_len=remaining)
            if rendered is None:
                truncated = True
                break
            line, kept_aliases = rendered

            if header is not None:
                if used + header_cost > budget:
                    truncated = True
                    break
                lines.append(header)
                used += header_cost
                current_type = item.code_type

            need = len(line) + 1
            if used + need > budget:
                truncated = True
                break
            lines.append(line)
            used += need
            # Store only aliases that actually appeared in the prompt line.
            included_items.append(
                CatalogCode(
                    code=item.code,
                    code_type=item.code_type,
                    name=item.name,
                    sort_order=item.sort_order,
                    aliases=kept_aliases,
                )
            )

        return "\n".join(lines), truncated, len(included_items), included_items
