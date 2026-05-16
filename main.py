#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.agent import UpdateAgent


async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    agent = UpdateAgent(config_path)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
