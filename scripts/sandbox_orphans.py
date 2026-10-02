"""Inspect or remove only unreferenced, aged sandbox ciphertext for one organization."""

import argparse
import asyncio
import json
from uuid import UUID

from app.core.config import Settings
from app.core.db import Database
from app.providers.storage import create_storage
from app.services.sandbox_gc import reap_orphans


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", type=UUID, required=True)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    settings = Settings.load()
    db = Database(settings)
    try:
        await db.verify_role()
        result = await reap_orphans(
            db, create_storage(settings), args.org, delete=args.delete, limit=args.limit
        )
        print(json.dumps(result, sort_keys=True))
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
