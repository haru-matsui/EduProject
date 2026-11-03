from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(request: Request, status: int, code: str, message: str, details: dict | None = None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": details or {},
        "request_id": getattr(request.state, "request_id", str(uuid4())),
    }})


async def api_error_handler(request: Request, exc: APIError):
    return error_response(request, exc.status, exc.code, exc.message, exc.details)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Never echo request input: authorization codes, invitation tokens and secrets
    # can appear in query/path/body validation errors.
    fields = [{"field": ".".join(str(x) for x in err["loc"]), "type": err["type"]} for err in exc.errors()]
    return error_response(request, 422, "validation_error", "Проверьте поля запроса.", {"fields": fields})
