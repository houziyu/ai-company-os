"""Audit log read API.

Audit records are append-only: this router exposes reads only. There is
deliberately no PATCH or DELETE, so a management change cannot be hidden after
the fact. ``detail`` strings are written by code (never by a provider) and are
re-scanned here for credential-shaped content before leaving the server.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AuthenticatedDep, DbSession
from app.api.schemas_organization import AuditEventResponse, AuditListResponse
from app.models.entities import AuditEvent, User

router = APIRouter(tags=["audit"])

# Anything matching these markers is replaced wholesale. Audit detail is
# code-owned, so a hit means a bug or an injected value; either way it must not
# be echoed back to the browser.
_REDACT_MARKERS = ("password", "token", "secret", "authorization", "api_key")


def _redact(detail: str | None) -> str | None:
    if not detail:
        return detail
    lowered = detail.lower()
    for marker in _REDACT_MARKERS:
        if marker in lowered:
            # Keep the shape of the record (which field existed) but no value.
            return "（内容已脱敏）"
    return detail


@router.get("/audit/events", response_model=AuditListResponse)
def list_audit_events(
    _authenticated: AuthenticatedDep,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, max_length=48),
) -> AuditListResponse:
    base = select(AuditEvent)
    count_stmt = select(func.count()).select_from(AuditEvent)
    if action:
        base = base.where(AuditEvent.action == action)
        count_stmt = count_stmt.where(AuditEvent.action == action)

    total = int(session.scalar(count_stmt) or 0)
    rows = session.scalars(
        base.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    actor_ids = {row.actor_user_id for row in rows if row.actor_user_id}
    names: dict[int, str] = {}
    if actor_ids:
        for user in session.scalars(select(User).where(User.id.in_(actor_ids))):
            names[user.id] = user.display_name

    return AuditListResponse(
        events=[
            AuditEventResponse(
                id=row.id,
                occurred_at=row.occurred_at,
                actor_type=row.actor_type,
                actor_user_id=row.actor_user_id,
                actor_display_name=names.get(row.actor_user_id or -1),
                action=row.action,
                outcome=row.outcome,
                target_type=row.target_type,
                target_id=row.target_id,
                detail=_redact(row.detail),
                request_id=row.request_id,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
