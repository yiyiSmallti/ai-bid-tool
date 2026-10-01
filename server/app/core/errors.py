class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.exit_code = exit_code


def not_found() -> ServiceError:
    return ServiceError("not_found", "Resource not found", 404, 4)
