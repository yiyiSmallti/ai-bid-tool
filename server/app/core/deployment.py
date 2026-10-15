"""The deployment mode stored in the database must match the configured one.

A database without the installation row is a personal database. The mode never
changes in place; moving data between modes uses packages.
"""

from app.core.errors import ServiceError

MODE_QUERY = "SELECT mode FROM public.installation"


def stored_mode(row_mode: str | None) -> str:
    return row_mode or "personal"


def require_mode(stored: str, configured: str) -> None:
    if stored != configured:
        raise ServiceError(
            "deployment_mode_mismatch",
            f"The database was initialized in {stored} mode but BID_DEPLOYMENT is {configured}",
            503,
            4,
        )
