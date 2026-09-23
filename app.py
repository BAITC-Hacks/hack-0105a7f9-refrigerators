"""Vercel entrypoint; also serves the existing team website locally."""
from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from contractor_match.api import create_app

ROOT = Path(__file__).resolve().parent
app = create_app()


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/web/")


@app.get("/integration/api-client.mjs", include_in_schema=False)
def browser_client():
    return FileResponse(ROOT / "integration" / "api-client.mjs", media_type="text/javascript")


class WebsiteFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Adding docs, tests or a local config under web/ must not publish it.
        if path not in {".", "index.html", "app.js", "styles.css"}:
            raise HTTPException(404)
        return await super().get_response(path, scope)


app.mount("/web", WebsiteFiles(directory=ROOT / "web", html=True), name="website")
