from app.models.entities import Base

__all__ = ["Base"]

# Register response tables for migration metadata and SQL-level isolation tests.
from app.models import agent as agent
from app.models import annotations as annotations
from app.models import attachments as attachments
from app.models import bid_review as bid_review
from app.models import bid_review_findings as bid_review_findings
from app.models import bid_review_privacy as bid_review_privacy
from app.models import bid_review_run as bid_review_run
from app.models import bid_signature as bid_signature
from app.models import check as check
from app.models import confidential as confidential
from app.models import exports as exports
from app.models import management as management
from app.models import memory as memory
from app.models import provider_configs as provider_configs
from app.models import requirement_confirmation as requirement_confirmation
from app.models import response_cards as response_cards
from app.models import sandbox as sandbox
from app.models import score as score
from app.models import screenshots as screenshots
from app.models import team_workflow as team_workflow
