"""Central error handling: every failure becomes a JSON response; nothing escapes as a crash or a bare 500 page.

Response shape (all errors):  {"detail": "<human message>", "error": "<code>", "request_id": "<id>"}
`detail` keeps FastAPI's own key, so existing clients that read `detail` keep working.
"""
import functools
import inspect
import logging
import uuid

import psycopg2
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg2 import pool
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("docuintel.core.errors")


def error_response(status_code: int, detail, code: str, request_id: str = "", headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "error": code, "request_id": request_id},
        headers=headers,
    )


def classify(exc: BaseException):
    """Maps an exception to (status, public message, code). Internal details are logged, never returned."""
    if isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError, pool.PoolError)):
        return 503, "The database is temporarily unavailable. Please try again in a moment.", "database_unavailable"
    if isinstance(exc, psycopg2.IntegrityError):
        return 409, "The request conflicts with existing data.", "conflict"
    if isinstance(exc, psycopg2.DataError):
        return 422, "Some of the supplied values are not valid.", "invalid_data"
    if isinstance(exc, psycopg2.Error):
        return 500, "A database error occurred.", "database_error"
    if isinstance(exc, RuntimeError) and "database pool" in str(exc).lower():
        return 503, "The database is not available right now. Please try again shortly.", "database_unavailable"
    if type(exc).__name__ == "EmbeddingUnavailable":
        return 503, "The semantic search service is temporarily unavailable. Try the Words or Exact search instead.", "semantic_unavailable"
    if isinstance(exc, (TimeoutError,)):
        return 504, "The operation timed out. Please try again.", "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return 502, "A required external service could not be reached.", "upstream_unavailable"
    return 500, "Internal server error. The incident has been logged.", "internal_error"


def register_error_handling(app: FastAPI) -> None:
    """Call BEFORE adding CORSMiddleware so the catch-all sits inside it and error replies still carry CORS headers."""

    @app.middleware("http")
    async def catch_all(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - last line of defence: the server must keep running
            status, message, code = classify(exc)
            logger.exception("Unhandled error [%s] on %s %s", request_id, request.method, request.url.path)
            return error_response(status, message, code, request_id)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, exc.detail, "http_error", getattr(request.state, "request_id", ""), getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(exc.errors()), "error": "validation_error",
                     "request_id": getattr(request.state, "request_id", "")},
        )

    @app.exception_handler(Exception)
    async def unhandled_exc(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12]
        status, message, code = classify(exc)
        logger.exception("Unhandled error [%s] on %s %s", rid, request.method, request.url.path)
        return error_response(status, message, code, rid)


def safe_call(fn, *args, default=None, what: str = "operation", **kwargs):
    """Runs a best-effort step (indexing, cleanup) and returns `default` instead of raising."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s failed: %s", what, exc)
        return default


def http_error(exc: BaseException) -> HTTPException:
    """Converts any exception into the matching HTTPException (used inside endpoints)."""
    status, message, _ = classify(exc)
    return HTTPException(status_code=status, detail=message)


def handle_errors(fn):
    """Route decorator (place it directly above the function, below @router.xxx): HTTPExceptions pass through,
    any other failure is logged with its traceback and returned as the matching JSON error. Works for sync and async routes."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error in endpoint %s", fn.__name__)
                raise http_error(exc)
        return awrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in endpoint %s", fn.__name__)
            raise http_error(exc)
    return wrapper
