"""Search HTTP endpoints — hybrid people search."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_authenticated_user,
)
from app.modules.search.query_schemas import SearchPeopleRequest, SearchPeopleResponse
from app.modules.search.query_service import SearchQueryService

router = APIRouter(prefix="/search", tags=["search"])


def get_search_query_service(db: Session = Depends(get_db)) -> SearchQueryService:
    return SearchQueryService(db)


@router.post("/people", response_model=SearchPeopleResponse)
def search_people(
    body: SearchPeopleRequest,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: SearchQueryService = Depends(get_search_query_service),
) -> SearchPeopleResponse:
    """Read-only hybrid search. No CSRF (safe POST search)."""
    _ = ctx
    return service.search_people(body)
