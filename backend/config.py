"""Loads settings, company policies and the agent registry."""
import os
from pathlib import Path

import yaml

try:  # .env is optional; mock mode needs no key
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _load(name):
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


POLICIES = _load("policies.yaml")
REGISTRY = _load("registry.yaml")

# "mock" = scripted agents, no API key needed (reliable demo). "llm" = real model agents.
MODE = os.getenv("AGENT_MODE", "mock").lower()
MODEL = os.getenv("MODEL", "claude-sonnet-5-5")
DB_PATH = os.getenv("DB_PATH", str(ROOT / "governance.db"))
