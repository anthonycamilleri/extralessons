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


def test_missing_or_wrong_token_is_401_with_challenge(client):
    response = rpc(client, "ping", token=None)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")
    assert rpc(client, "ping", token="nope").status_code == 401


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
