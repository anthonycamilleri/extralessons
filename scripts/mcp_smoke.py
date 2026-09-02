"""Start `manage.py mcp_server` over stdio and call one tool, like Claude would.

Used by CI and handy after a fresh install:

    python scripts/mcp_smoke.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main():
    # A throwaway SQLite database unless the caller points DATABASE_URL at
    # something real, migrated first so get_overview has tables to read.
    env = dict(os.environ)
    if "DATABASE_URL" not in env or env["DATABASE_URL"].endswith(":memory:"):
        env["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/smoke.sqlite3"
    subprocess.run(
        [sys.executable, "manage.py", "migrate", "--verbosity", "0"], cwd=ROOT, env=env, check=True
    )
    params = StdioServerParameters(
        command=sys.executable, args=["manage.py", "mcp_server"], cwd=str(ROOT), env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            print("tools:", ", ".join(names))
            result = await session.call_tool("get_overview", {})
            if result.isError:
                raise SystemExit(f"get_overview failed: {result.content[0].text}")
            overview = json.loads(result.content[0].text)
            print("school:", overview["school_name"], "| terms:", len(overview["terms"]))


if __name__ == "__main__":
    asyncio.run(main())
