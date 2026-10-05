import logging
import traceback


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.exit_code = exit_code


def not_found() -> ServiceError:
    return ServiceError("not_found", "Resource not found", 404, 4)


def log_unexpected(logger: logging.Logger, context: str, exc: BaseException) -> None:
    """Log an unexpected failure by type and stack only; messages may quote tender text."""
    stack = "".join(traceback.format_tb(exc.__traceback__))
    logger.error("%s failed with %s\n%s", context, type(exc).__name__, stack)


def invalid_document() -> ServiceError:
    return ServiceError(
        "invalid_document",
        "Upload must be a readable, unencrypted PDF or DOCX within limits",
        400,
        2,
    )
