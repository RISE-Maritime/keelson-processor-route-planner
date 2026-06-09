"""FastAPI planning API (HTTP integration layer).

P1 exposes only ``GET /health``. The planning endpoints from docs/DESIGN.md §4 —
``POST /api/routes/plan``, ``POST /api/routes/{id}/validate``, ``POST /api/routes/{id}/select``,
``GET /api/routes/{id}``, ``GET /api/routes/{id}/geojson``, ``GET|POST /api/vessel-profiles`` —
are added in P1+ feature work and are intentionally not implemented in the skeleton. Crowsnest
talks to the planner over the keelson RPC layer (see rpc/); this HTTP API is for direct/testing
use.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


def create_app(conf: dict):
    """Build the FastAPI app. ``conf`` is the runtime config (see config.py)."""
    from fastapi import FastAPI

    app = FastAPI(
        title="keelson-processor-route-planner",
        version="0.1.0",
        description="Voyage route planning + validation (NOT collision avoidance).",
    )

    @app.get("/health")
    def health() -> dict:
        """Liveness probe — returns ok once the service is up."""
        return {"status": "ok", "service": "route-planner", "version": "0.1.0"}

    return app


class ServerHandle:
    """Handle to a uvicorn server running on a background thread; call ``shutdown()`` to stop."""

    def __init__(self, server, thread):
        self._server = server
        self._thread = thread

    def shutdown(self) -> None:
        """Ask uvicorn to exit and join its thread."""
        self._server.should_exit = True
        self._thread.join(timeout=5)


def serve_in_thread(app, host: str, port: int) -> ServerHandle:
    """Run uvicorn in a daemon thread and return a ServerHandle.

    uvicorn only installs its own signal handlers when started on the main thread, so running it
    on a background thread leaves keelson's GracefulShutdown handlers intact.
    """
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return ServerHandle(server, thread)
