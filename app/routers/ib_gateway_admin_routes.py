from fastapi import APIRouter, Request

from app.security import ROUTE_CLASS_LOCAL_ADMIN, require_local_admin, unsafe_route
from app.services.ib_gateway_health_service import check_status
from app.services.ib_gateway_launcher import launch_gateway

router = APIRouter(prefix="/api/ib-gateway", tags=["interactive-brokers"])


@router.get("/status")
def ib_gateway_status(request: Request) -> dict[str, object]:
    return check_status(settings=request.app.state.settings).to_dict()


@router.post("/launch")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="launches the configured local IB Gateway executable",
    csrf_required=True,
    local_admin_required=True,
)
def launch_ib_gateway(request: Request) -> dict[str, object]:
    require_local_admin(
        request,
        enabled=True,
        disabled_message="IB Gateway launch is unavailable.",
        local_only_message="IB Gateway launch is restricted to the local administrator.",
        csrf_message="A valid local administrator CSRF token is required.",
        structured_code="IB_GATEWAY_LAUNCH_FORBIDDEN",
        csrf_required=True,
    )
    return launch_gateway(settings=request.app.state.settings).to_dict()
