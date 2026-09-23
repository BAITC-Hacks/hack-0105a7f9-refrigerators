"""Serve the event planner and its API from one local address."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from threading import Thread
from time import sleep
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

os.environ.setdefault("AI_PROVIDER", "local")
os.environ.setdefault("RANKING_PROVIDER", "tfidf")

from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from contractor_match.api import create_app


ROOT = Path(__file__).resolve().parent
app = create_app()


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(url="/web/")


app.mount("/web", StaticFiles(directory=ROOT / "web", html=True), name="web")
app.mount("/integration", StaticFiles(directory=ROOT / "integration"), name="integration")


def open_when_ready(url: str) -> None:
    health_url = url.replace("/web/", "/health")
    for _ in range(60):
        try:
            with urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, URLError):
            sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description="Запустить сайт и API на одном локальном адресе")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/web/"
    if not args.no_browser:
        Thread(target=open_when_ready, args=(url,), daemon=True).start()
    print(f"Open {url}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

