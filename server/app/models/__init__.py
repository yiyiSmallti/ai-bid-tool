from app.models.entities import Base

__all__ = ["Base"]

# Register response tables for migration metadata and SQL-level isolation tests.
from app.models import response_cards as response_cards
from app.models import sandbox as sandbox
