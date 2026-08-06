"""Local entry point for the optional dashboard API."""

from __future__ import annotations


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("Install the dashboard extra first: uv sync --extra dashboard") from exc
    uvicorn.run(
        "advisorai.api.dashboard_server:app",
        host="127.0.0.1",
        port=8787,
        reload=False,
        proxy_headers=False,
    )


try:
    from .dashboard import create_dashboard_app

    app = create_dashboard_app()
except RuntimeError:
    # Keep importing the rest of advisorai possible without optional web deps.
    app = None


if __name__ == "__main__":  # pragma: no cover
    main()
