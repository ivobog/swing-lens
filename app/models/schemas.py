from pydantic import BaseModel


class HealthResponse(BaseModel):
    app: str
    status: str
    database_configured: bool
    ib_host: str
    ib_port: int
