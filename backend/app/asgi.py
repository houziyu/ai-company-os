"""ASGI entrypoint used by uvicorn.

Kept separate from :mod:`app.main` so importing the factory in tests does not
require a configured database.
"""

from __future__ import annotations

from app.main import create_app

app = create_app()
