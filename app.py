"""PrestoPresentations FastAPI application."""

import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from routers import wikkit  # noqa: E402  (loaded after .env so routers see its settings)
from memory import memory_context  # noqa: E402
from sessions import PostgresSessionMiddleware, configured_session_store  # noqa: E402

app = FastAPI(
    title="PrestoPresentations",
    dependencies=[Depends(memory_context, scope="function")],
)
secure_cookie = os.environ.get("SESSION_COOKIE_SECURE", "true")
if secure_cookie not in ("true", "false"):
    raise ValueError("SESSION_COOKIE_SECURE must be true or false")
app.add_middleware(
    PostgresSessionMiddleware, store=configured_session_store(),
    secure=secure_cookie == "true",
)
mimetypes.add_type("text/javascript", ".does")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(wikkit.router)


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/wikkit", status_code=307)
