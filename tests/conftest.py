"""
Pytest configuration for Prediction Build smoke tests.

Sets DATABASE_URL to a temp file BEFORE any project modules are imported,
so all modules that call init_db() / get_session() use the test database.
"""
from __future__ import annotations

import os
import tempfile

# Must happen before any local project imports
_TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "prediction_test_smoke.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_FILE}"
# Ensure no live API keys accidentally trigger real network calls
os.environ.setdefault("NEWSAPI_KEY", "")
os.environ.setdefault("ODDS_API_KEY", "")
os.environ.setdefault("FRED_API_KEY", "")
os.environ.setdefault("AUTO_PAPER_BET", "false")
