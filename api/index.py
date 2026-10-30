"""Vercel serverless entry point — wraps the FastAPI app."""
from backend.main import app  # noqa: F401 — Vercel picks up `app`

