"""Vercel entrypoint; also serves the existing team website locally."""
from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from contractor_match.api import create_app

ROOT = Path(__file__).resolve().parent
app = create_app()


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/web/")


@app.get("/integration/api-client.mjs", include_in_schema=False)
def browser_client():
    return FileResponse(ROOT / "integration" / "api-client.mjs", media_type="text/javascript")


# Only these public assets are served; never mount the repository root.
app.mount("/web", StaticFiles(directory=ROOT / "web", html=True), name="website")
