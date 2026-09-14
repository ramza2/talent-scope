"""Search HTTP endpoints — hybrid people search + NL interpret."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai.providers.llm import OpenAICompatibleLLMProvider
from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_authenticated_user,
)
from app.modules.search.interpret_schemas import (
    SearchInterpretRequest,
    SearchInterpretResponse,
)
from app.modules.search.interpret_service import SearchInterpretService
from app.modules.search.query_schemas import SearchPeopleRequest, SearchPeopleResponse
from app.modules.search.query_service import SearchQueryService

router = APIRouter(prefix="/search", tags=["search"])


def get_search_query_service(db: Session = Depends(get_db)) -> SearchQueryService:
    return SearchQueryService(db)


def get_search_interpret_service(
    db: Session = Depends(get_db),
) -> SearchInterpretService:
    settings = get_settings()
    return SearchInterpretService(
        db,
        settings=settings,
        llm=OpenAICompatibleLLMProvider(settings),
    )


@router.post("/people", response_model=SearchPeopleResponse)
def search_people(
    body: SearchPeopleRequest,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: SearchQueryService = Depends(get_search_query_service),
) -> SearchPeopleResponse:
    """Read-only hybrid search. No CSRF (safe POST search)."""
    _ = ctx
    return service.search_people(body)


@router.post("/interpret", response_model=SearchInterpretResponse)
def search_interpret(
    body: SearchInterpretRequest,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: SearchInterpretService = Depends(get_search_interpret_service),
) -> SearchInterpretResponse:
    """Read-only NL → Query JSON. No CSRF. No DB mutation. No search execution."""
    _ = ctx
    return service.interpret(body)
