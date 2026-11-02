"""Health endpoints.

* ``/api/v1/health/live`` never touches the database; it answers only "this
  process is running". Container liveness probes use it so a database outage
  does not cause a restart loop.
* ``/api/v1/health/ready`` is the real gate: database reachable, migrations at
  head. It returns 503 with the failing check when not ready.
* ``/api/v1/system/status`` exposes non-secret deployment facts for the
  settings page and is guarded like the rest of the business API.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import AuthenticatedDep
from app.api.schemas import LivenessResponse, ReadinessResponse, ServiceStatus
from app.services.health_service import HealthService

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=LivenessResponse)
def liveness(request: Request, response: Response) -> LivenessResponse:
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "no-store"
    return LivenessResponse(status="ok", service=settings.app_name, version=settings.app_version)


@router.get("/health/ready", response_model=ReadinessResponse)
def readiness(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    service: HealthService = request.app.state.health_service
    report = service.readiness(require_worker=False)
    body = ReadinessResponse(
        ready=report.ready,
        service=settings.app_name,
        version=settings.app_version,
        checks=report.checks,
        evaluated_at=report.as_dict()["evaluated_at"],
    )
    return JSONResponse(
        status_code=200 if report.ready else 503,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/system/status", response_model=ServiceStatus)
def system_status(request: Request, _authenticated: AuthenticatedDep) -> JSONResponse:
    service: HealthService = request.app.state.health_service
    return JSONResponse(
        status_code=200,
        content=service.service_status(),
        headers={"Cache-Control": "no-store"},
    )
