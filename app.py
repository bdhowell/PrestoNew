"""PrestoPresentations FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from content import SourceFiles  # noqa: E402
from routers import account, conferences, content, posters, wikkit  # noqa: E402  (loaded after .env so routers see its settings)
from identity import identity_context  # noqa: E402
from posters import poster_context  # noqa: E402
from memory import memory_context  # noqa: E402
from sessions import PostgresSessionMiddleware, configured_session_store  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    SourceFiles().prepare_directories()
    yield


app = FastAPI(
    title="PrestoPresentations",
    lifespan=lifespan,
    # identity_context installs the trusted user_id that memory_context relies on.
    dependencies=[
        Depends(identity_context),
        Depends(poster_context),
        Depends(memory_context, scope="function"),
    ],
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
app.include_router(account.router)
app.include_router(posters.router)
app.include_router(content.router)
app.include_router(conferences.router)


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/wikkit/magic", status_code=307)
