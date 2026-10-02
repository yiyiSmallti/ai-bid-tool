#!/usr/bin/env python3
"""Run only on the separately provisioned sandbox execution node."""

import asyncio

from app.providers.sandbox_runtime import RuntimeConfig, SandboxFailure
from app.sandbox.supervisor import Supervisor

if __name__ == "__main__":
    try:
        asyncio.run(Supervisor(RuntimeConfig.from_env()).serve())
    except SandboxFailure as exc:
        raise SystemExit(exc.code) from None
