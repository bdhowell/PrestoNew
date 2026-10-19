"""PrestoPresentations FastAPI application."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from routers import wikkit  # noqa: E402  (loaded after .env so routers see its settings)

app = FastAPI(title="PrestoPresentations")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(wikkit.router)


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/wikkit", status_code=307)
