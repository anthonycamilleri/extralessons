"""The remote MCP endpoint (apps.catalog.mcp_http): auth, JSON-RPC, tools."""
import json

import pytest

from apps.catalog import mcp_server as tools
from apps.catalog.models import Provider

pytestmark = pytest.mark.django_db

TOKEN = "test-mcp-token"
URL = "/mcp"


@pytest.fixture(autouse=True)
def enabled(settings):
    settings.MCP_API_TOKEN = TOKEN


def rpc(client, method, params=None, request_id=1, token=TOKEN, path=URL):
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if request_id is not None:
        body["id"] = request_id
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(path, data=json.dumps(body), content_type="application/json", headers=headers)


# --- Gate ---------------------------------------------------------------------


def test_disabled_without_a_token(client, settings):
    settings.MCP_API_TOKEN = ""
    assert rpc(client, "ping").status_code == 404


def test_missing_or_wrong_token_is_a_bare_401(client):
    response = rpc(client, "ping", token=None)
    assert response.status_code == 401
    # No WWW-Authenticate: that header makes MCP clients start OAuth discovery.
    assert "WWW-Authenticate" not in response
    assert rpc(client, "ping", token="nope").status_code == 401


def test_x_api_key_header_is_accepted_too(client):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    ok = client.post(URL, data=body, content_type="application/json", headers={"X-API-Key": TOKEN})
    assert ok.status_code == 200
    bad = client.post(URL, data=body, content_type="application/json", headers={"X-API-Key": "nope"})
    assert bad.status_code == 401
    # A wrong bearer token is not rescued by a correct X-API-Key: the
    # Authorization header wins when it is present and well-formed.
    mixed = client.post(
        URL, data=body, content_type="application/json",
        headers={"Authorization": "Bearer nope", "X-API-Key": TOKEN},
    )
    assert mixed.status_code == 401


def test_only_post_is_served(client):
    response = client.get(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 405
    assert response["Allow"] == "POST"
    assert client.delete(URL, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 405


def test_trailing_slash_also_works(client):
    assert rpc(client, "ping", path=URL + "/").status_code == 200


def test_no_csrf_token_needed(client):
    client.handler.enforce_csrf_checks = True
    assert rpc(client, "ping").status_code == 200


# --- JSON-RPC -----------------------------------------------------------------


def test_initialize_negotiates_version_and_advertises_tools(client):
    response = rpc(client, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "claude", "version": "1"},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    result = body["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == tools.SERVER_NAME
    assert result["instructions"] == tools.SERVER_INSTRUCTIONS


def test_unknown_protocol_version_gets_our_newest(client):
    result = rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert result["protocolVersion"] == "2025-11-25"


def test_notifications_are_accepted_with_no_body(client):
    response = rpc(client, "notifications/initialized", request_id=None)
    assert response.status_code == 202
    assert response.content == b""


def test_ping(client):
    assert rpc(client, "ping").json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_unknown_method(client):
    body = rpc(client, "resources/list").json()
    assert body["error"]["code"] == -32601


def test_parse_error(client):
    response = client.post(
        URL, data="{not json", content_type="application/json",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


def test_not_jsonrpc(client):
    response = client.post(
        URL, data=json.dumps({"hello": "world"}), content_type="application/json",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.json()["error"]["code"] == -32600


def test_batch_returns_a_list(client):
    body = [
        {"jsonrpc": "2.0", "id": "a", "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "b", "method": "ping"},
    ]
    response = client.post(
        URL, data=json.dumps(body), content_type="application/json",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert [r["id"] for r in response.json()] == ["a", "b"]


# --- Tools --------------------------------------------------------------------


def test_tools_list_matches_the_stdio_server(client):
    listed = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert set(listed) == {fn.__name__ for fn in tools.TOOLS}
    assert listed["upsert_class"]["inputSchema"]["type"] == "object"
    assert "term" in listed["upsert_class"]["inputSchema"]["properties"]


def test_tools_call_reads_and_writes_through_the_real_tools(client):
    created = rpc(client, "tools/call", {
        "name": "upsert_provider",
        "arguments": {"name": "Chess Club Ltd", "contact_email": "hi@chess.test"},
    }).json()["result"]
    assert created.get("isError") is not True
    assert Provider.objects.filter(name="Chess Club Ltd").exists()

    overview = rpc(client, "tools/call", {"name": "get_overview", "arguments": {}}).json()["result"]
    assert overview["content"][0]["type"] == "text"
    text = overview["content"][0]["text"]
    assert "Chess Club Ltd" in text


def test_tool_call_uses_the_request_connection_not_a_private_one(client):
    """Regression for a production outage. Running the tool inside an event loop
    made Django hand it a connection object of its own, scoped to the loop's
    context; it was never closed and, with a pool, never returned. The tool must
    run on the request thread and touch only the request's connection, so no
    new connection object may appear during the call."""
    from django.db import connection
    from django.db.backends.signals import connection_created

    connection.ensure_connection()  # the request thread's connection exists already
    opened = []

    def capture(sender, connection, **kwargs):
        opened.append(connection)

    connection_created.connect(capture)
    try:
        result = rpc(client, "tools/call", {"name": "get_overview", "arguments": {}}).json()["result"]
    finally:
        connection_created.disconnect(capture)

    assert result.get("isError") is not True
    assert opened == [], "the tool opened a connection of its own instead of using the request's"


def test_tool_arguments_are_validated_like_the_stdio_server(client):
    result = rpc(client, "tools/call", {"name": "get_class", "arguments": {"class_id": "not-a-number"}}).json()["result"]
    assert result["isError"] is True
    assert "class_id" in result["content"][0]["text"]
    # Coercion the SDK does over stdio happens here too: a numeric string is fine.
    result = rpc(client, "tools/call", {"name": "get_class", "arguments": {"class_id": "999"}}).json()["result"]
    assert "No class with id 999" in result["content"][0]["text"]


def test_list_results_are_wrapped_like_the_stdio_server(client):
    result = rpc(client, "tools/call", {"name": "list_classes", "arguments": {}}).json()["result"]
    assert result["structuredContent"] == {"result": []}
    assert json.loads(result["content"][0]["text"]) == []


def test_tool_failure_is_a_result_with_is_error(client):
    result = rpc(client, "tools/call", {"name": "get_class", "arguments": {"class_id": 999}}).json()["result"]
    assert result["isError"] is True
    assert "No class with id 999" in result["content"][0]["text"]


def test_unknown_tool_is_a_result_with_is_error(client):
    result = rpc(client, "tools/call", {"name": "delete_everything", "arguments": {}}).json()["result"]
    assert result["isError"] is True


def test_tools_call_without_a_name_is_invalid_params(client):
    body = rpc(client, "tools/call", {"arguments": {}}).json()
    assert body["error"]["code"] == -32602
