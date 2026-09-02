"""Serve the catalogue to an AI assistant over the Model Context Protocol.

    python manage.py mcp_server

Speaks MCP over stdio, which is how Claude Code and Claude Desktop launch
local servers: they start this process themselves and talk JSON-RPC on its
stdin/stdout. It runs in-process against whatever DATABASE_URL points at, so
it carries exactly the trust of ``manage.py shell`` and needs no extra
credentials. The tools themselves live in apps.catalog.mcp_server.
"""
import logging
import os
import sys

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the MCP server (stdio) that lets Claude read and populate the catalogue."

    def handle(self, *args, **options):
        # stdout is the wire: anything logged there corrupts the protocol
        # stream, so swap the configured stdout handler for stderr.
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        logging.basicConfig(stream=sys.stderr, level=root.level or logging.INFO)

        # FastMCP calls our synchronous tools from inside its asyncio loop,
        # where Django refuses ORM access by default. A stdio server handles
        # one request at a time on a single thread, so no connection is ever
        # shared between coroutines and the guard has nothing to protect.
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

        try:
            from apps.catalog.mcp_server import build_server
            server = build_server()
        except ImportError as e:
            raise CommandError(
                "The MCP SDK is not installed. Run: pip install -e \".[mcp]\""
            ) from e

        server.run(transport="stdio")
