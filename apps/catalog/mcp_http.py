"""The catalogue tools over HTTPS: a remote MCP endpoint at /mcp.

Same tools as ``manage.py mcp_server`` (apps.catalog.mcp_server), but served by
the web service itself, so Claude Desktop, Cowork and claude.ai reach the hosted
app as a *custom connector*: no Python on the laptop, no database exposed to the
internet, one URL and one token.

Transport. MCP's Streamable HTTP in its stateless form: every POST carries one
JSON-RPC message and receives one JSON response. There is no session id and no
server-initiated stream (GET answers 405), which is the entire protocol surface
a tools-only server needs. Doing this as a plain Django view, rather than
mounting the SDK's ASGI app, keeps the app a single WSGI process under gunicorn.

Auth. One bearer token, MCP_API_TOKEN, which the connector sends as a fixed
request header. The tools can create and publish classes, so the token carries
the trust of a school-office login: keep it in Render and in the connector
settings, nowhere else. An empty token switches the endpoint off (404).
"""
import asyncio
import json
import logging
import os
import secrets

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .mcp_server import SERVER_INSTRUCTIONS, SERVER_NAME, build_server

logger = logging.getLogger(__name__)

# Newest first. The client proposes one; we answer with it if we know it,
# otherwise with our newest. The stateless JSON exchange used here has been
# the same across all of them.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_VERSION = "1"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603,
)

_server = None


def get_server():
    global _server
    if _server is None:
        _server = build_server()
    return _server


def _run(coroutine):
    """Run one SDK coroutine to completion on this request's thread.

    FastMCP is asyncio-native and calls our synchronous tools from inside its
    loop, where Django's ORM guard would refuse to work. The guard exists to
    stop blocking calls stalling a shared event loop; this loop lives for one
    request on one gunicorn thread and is shared with nothing, so there is
    nothing to protect. Same reasoning as the stdio command.
    """
    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    return asyncio.run(coroutine)


# --------------------------------------------------------------------------- #
# JSON-RPC plumbing
# --------------------------------------------------------------------------- #


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _error_body(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _authorised(request, token):
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return secrets.compare_digest(presented.strip().encode(), token.encode())


def _tool_result(result):
    """Shape FastMCP's return value into a CallToolResult.

    call_tool() hands back a list of content blocks, a (content, structured)
    pair when the tool declares an output schema, or a bare dict.
    """
    content, structured = [], None
    if isinstance(result, tuple):
        content, structured = result
    elif isinstance(result, dict):
        structured = result
    else:
        content = list(result)
    body = {"content": [block.model_dump(by_alias=True, exclude_none=True) for block in content]}
    if structured is not None:
        body["structuredContent"] = structured
        if not body["content"]:
            body["content"] = [{"type": "text", "text": json.dumps(structured, default=str)}]
    return body


def _dispatch(method, params):
    from mcp.server.fastmcp.exceptions import ToolError

    server = get_server()
    if method == "initialize":
        requested = str(params.get("protocolVersion", ""))
        return {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        tools = _run(server.list_tools())
        return {"tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "tools/call needs a tool name and an arguments object")
        logger.info("mcp tools/call %s", name)
        try:
            return _tool_result(_run(server.call_tool(name, arguments)))
        except ToolError as exc:
            # The SDK's own convention: a tool that failed is a successful
            # response carrying isError, so the assistant can read the message
            # and try again, rather than a protocol failure.
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _handle(message):
    """One JSON-RPC message in, one response body out (None for notifications)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error_body(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message")
    method = message.get("method")
    if method is None:
        # A response from the client to something we never sent; nothing to say.
        return None
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error_body(request_id, INVALID_PARAMS, "params must be an object")
    if request_id is None:
        # Notifications (notifications/initialized, notifications/cancelled)
        # are acknowledged by the 202 the caller sends; there is no body.
        return None
    try:
        return {"jsonrpc": "2.0", "id": request_id, "result": _dispatch(method, params)}
    except RpcError as exc:
        return _error_body(request_id, exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp %s failed", method)
        return _error_body(request_id, INTERNAL_ERROR, "Internal error")


# --------------------------------------------------------------------------- #
# The view
# --------------------------------------------------------------------------- #


@csrf_exempt
def mcp_endpoint(request):
    token = settings.MCP_API_TOKEN
    if not token:
        raise Http404("MCP endpoint is not enabled")
    if request.method != "POST":
        # No server-to-client stream and no sessions to delete.
        return HttpResponse(status=405, headers={"Allow": "POST"})
    if not _authorised(request, token):
        return HttpResponse(
            status=401,
            headers={"WWW-Authenticate": f'Bearer realm="{SERVER_NAME}"'},
        )
    try:
        payload = json.loads(request.body or b"")
    except ValueError:
        return JsonResponse(_error_body(None, PARSE_ERROR, "Parse error"), status=400)

    batch = isinstance(payload, list)
    responses = [r for r in map(_handle, payload if batch else [payload]) if r is not None]
    if not responses:
        return HttpResponse(status=202)
    return JsonResponse(responses if batch else responses[0], safe=False)
