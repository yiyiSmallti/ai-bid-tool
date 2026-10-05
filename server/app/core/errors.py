import logging
import traceback


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        exit_code: int = 2,
        *,
        job_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.exit_code = exit_code
        self.job_id = job_id


def not_found() -> ServiceError:
    return ServiceError("not_found", "Resource not found", 404, 4)


def log_unexpected(logger: logging.Logger, context: str, exc: BaseException) -> None:
    """Log an unexpected failure by type and stack only; messages may quote tender text."""
    stack = "".join(traceback.format_tb(exc.__traceback__))
    logger.error("%s failed with %s\n%s", context, type(exc).__name__, stack)
