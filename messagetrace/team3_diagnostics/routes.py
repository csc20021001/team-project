"""HTTP interface for the US-01..US-04 diagnosis and fault lifecycle."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .diagnostics import clear, diagnose, inject


class FaultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    kind: str
    params: dict = Field(default_factory=dict)


def register_routes(app: FastAPI, get_store):
    """Register diagnosis endpoints using a getter so tests can swap SQLite stores."""
    @app.post("/api/faults")
    def create_fault(req: FaultIn):
        try:
            return inject(get_store(), req.target, req.kind, req.params)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/faults/{fault_id}")
    def clear_fault(fault_id: str):
        if not clear(get_store(), fault_id):
            raise HTTPException(404, "active fault not found")
        return {"fault_id": fault_id, "active": False}

    @app.get("/api/diagnostics")
    def diagnostics(message_id: str | None = None, target: str | None = None):
        try:
            return diagnose(get_store(), message_id, target)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc
