"""FastAPI app. Run with:  uvicorn backend.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import api
from .config import ROOT


@asynccontextmanager
async def lifespan(app):
    api.reset_all()  # every start is a clean demo
    yield


app = FastAPI(title="Manager Agent Control Tower", lifespan=lifespan)


class RequestIn(BaseModel):
    text: str


class ApprovalIn(BaseModel):
    approve: bool


class AgeIn(BaseModel):
    hours: float = 25


@app.get("/")
def dashboard():
    return FileResponse(ROOT / "dashboard" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/state")
def state():
    return api.get_state()


@app.post("/api/requests")
def new_request(body: RequestIn):
    try:
        return api.submit_request(body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/scenarios/{n}")
def scenario(n: int):
    try:
        return api.run_scenario(n)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/approvals/{approval_id}")
def approval(approval_id: int, body: ApprovalIn):
    try:
        return api.decide(approval_id, body.approve)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/facts/age")
def age(body: AgeIn):
    return api.age_facts(body.hours)


@app.post("/api/reset")
def reset():
    return api.reset_all()


@app.get("/api/invariants")
def invariants_status():
    """V3: Check all 13 safety invariants."""
    return api.get_invariants_status()


@app.get("/api/events")
def events(limit: int = 50, event_type: str = None):
    """V3: Get recent events from event bus."""
    return api.get_events(limit, event_type)


@app.get("/api/reconcile")
def reconcile():
    """V3: Reconcile connector actions against systems of record."""
    return api.reconcile_connector_actions()
