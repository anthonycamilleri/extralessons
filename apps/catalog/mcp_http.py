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

Tool calls run synchronously on the request thread, on purpose. The SDK's own
call path is asyncio, and inside a running event loop Django scopes database
connections per async context rather than per thread: the tool would get a
connection object of its own, unreachable once the loop ends and never returned
to the pool. Four such calls emptied the production pool and took the site down
with "couldn't get a connection after 10.00 sec". Calling the function directly
means it uses the request's own connection, which Django closes at request end
like any other. Only the tool *listing* still goes through the SDK, and that
touches no database.

Auth. One token, MCP_API_TOKEN, which the connector sends as a fixed request
header, either `Authorization: Bearer <token>` or `X-API-Key: <token>`. The
tools can create and publish classes, so the token carries the trust of a
school-office login: keep it in Render and in the connector settings, nowhere
else. An empty token switches the endpoint off (404).
"""
import asyncio
import json
import logging
import secrets

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .mcp_server import SERVER_INSTRUCTIONS, SERVER_NAME, TOOLS, build_server

logger = logging.getLogger(__name__)

# Newest first. The client proposes one; we answer with it if we know it,
# otherwise with our newest. The stateless JSON exchange used here has been
# the same across all of them.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_VERSION = "1"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603,
)

_tool_descriptions = None
_tool_index = None


def _tool_list():
    """The tools/list payload, built once from the same FastMCP server the stdio
    command runs, so both transports advertise identical names and schemas."""
    global _tool_descriptions
    if _tool_descriptions is None:
        tools = asyncio.run(build_server().list_tools())
        _tool_descriptions = [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]
    return _tool_descriptions


def _tools():
    """name -> (function, argument metadata), using the SDK's own validation
    helper so arguments are checked exactly as they would be over stdio."""
    global _tool_index
    if _tool_index is None:
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata

        _tool_index = {fn.__name__: (fn, func_metadata(fn)) for fn in TOOLS}
    return _tool_index


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
    """Accept the token as `Authorization: Bearer <token>` or `X-API-Key: <token>`.

    Two spellings because connector dialogs differ: some let you add an
    Authorization header, others reserve that name for OAuth and only allow
    custom ones. Either way the comparison is constant-time.
    """
    presented = ""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer":
        presented = value.strip()
    if not presented:
        presented = request.headers.get("X-API-Key", "").strip()
    if not presented:
        return False
    return secrets.compare_digest(presented.encode(), token.encode())


def _tool_error(name, message):
    # The SDK's own convention: a tool that failed is a successful response
    # carrying isError, so the assistant can read the message and try again,
    # rather than a protocol failure.
    return {"content": [{"type": "text", "text": f"Error executing tool {name}: {message}"}], "isError": True}


def _call_tool(name, arguments):
    entry = _tools().get(name)
    if entry is None:
        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}
    fn, meta = entry
    logger.info("mcp tools/call %s", name)
    try:
        parsed = meta.arg_model.model_validate(meta.pre_parse_json(arguments))
        result = fn(**parsed.model_dump_one_level())
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the assistant
        return _tool_error(name, exc)
    # Mirror FastMCP's shaping: a dict is the structured result as-is, anything
    # else is wrapped under "result"; the text block carries the JSON for
    # clients that only read text.
    structured = result if isinstance(result, dict) else {"result": result}
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
        "structuredContent": structured,
    }


def _dispatch(method, params):
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
        return {"tools": _tool_list()}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "tools/call needs a tool name and an arguments object")
        return _call_tool(name, arguments)
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
        # Deliberately no WWW-Authenticate header: to an MCP client that is the
        # cue to start OAuth discovery, and Claude's connector dialog then
        # reports the server as "authentication always required". This is an
        # API-key server; a bare 401 says "wrong or missing key" and no more.
        return HttpResponse(status=401)
    try:
        payload = json.loads(request.body or b"")
    except ValueError:
        return JsonResponse(_error_body(None, PARSE_ERROR, "Parse error"), status=400)

    batch = isinstance(payload, list)
    responses = [r for r in map(_handle, payload if batch else [payload]) if r is not None]
    if not responses:
        return HttpResponse(status=202)
    return JsonResponse(responses if batch else responses[0], safe=False)
