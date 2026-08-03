from pydantic import BaseModel


class HealthResponse(BaseModel):
    app: str
    status: str
    database_configured: bool
    ib_host: str
    ib_port: int


class ReadinessResponse(BaseModel):
    app: str
    status: str
    database_ok: bool
    local_dirs_ok: bool
    migrations_ok: bool | None = None
    worker_ok: bool | None = None
    jobs_ok: bool | None = None
    checks: dict[str, str]
