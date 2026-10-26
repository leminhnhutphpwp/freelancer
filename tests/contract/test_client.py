"""Contract tests for the Freelancer client using respx (mocked HTTP)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.freelancer.client import FreelancerClient, FreelancerError, flatten_params


@pytest.fixture
def client():
    return FreelancerClient(
        access_token="tok",
        base_url="https://www.freelancer.com/api",
        max_retries=2,
        client=httpx.AsyncClient(base_url="https://www.freelancer.com/api"),
    )


@pytest.fixture
def mock():
    with respx.mock(base_url="https://www.freelancer.com/api") as m:
        yield m


def test_flatten_params():
    flat = flatten_params({"users": [1, 2], "limit": 10, "flag": True, "none": None})
    assert ("users[]", 1) in flat
    assert ("users[]", 2) in flat
    assert ("limit", 10) in flat
    assert ("flag", "true") in flat
    assert ("none", None) not in flat


async def test_get_self_success(client, mock):
    mock.get("/users/0.1/self/").respond(
        200, json={"status": "success", "result": {"users": [{"id": 7, "username": "fen"}]}}
    )
    me = await client.get_self()
    assert me["id"] == 7


async def test_get_self_inline_user(client, mock):
    mock.get("/users/0.1/self/").respond(200, json={"status": "success", "result": {"id": 7, "username": "fen"}})
    me = await client.get_self()
    assert me["id"] == 7


async def test_auth_error_mapping(client, mock):
    mock.get("/users/0.1/self/").respond(
        401, json={"status": "error", "error": {"code": "UNAUTHORIZED", "detail": "bad token"}}
    )
    with pytest.raises(FreelancerError) as exc_info:
        await client.get_self()
    assert exc_info.value.kind == "auth"
    assert exc_info.value.status_code == 401


async def test_403_maps_to_auth(client, mock):
    mock.get("/users/0.1/self/").respond(403, json={"message": "forbidden"})
    with pytest.raises(FreelancerError) as exc_info:
        await client.get_self()
    assert exc_info.value.kind == "auth"


async def test_retry_on_429_then_success(client, mock):
    route = mock.get("/users/0.1/self/")
    route.side_effect = [
        httpx.Response(429, json={"message": "slow down"}),
        httpx.Response(200, json={"status": "success", "result": {"id": 1}}),
    ]
    me = await client.get_self()
    assert me["id"] == 1
    assert route.call_count == 2


async def test_retry_exhausted_raises(client, mock):
    route = mock.get("/users/0.1/self/")
    route.side_effect = [httpx.Response(500, json={})] * 3
    with pytest.raises(FreelancerError) as exc_info:
        await client.get_self()
    assert exc_info.value.kind == "server"
    assert route.call_count == 3


async def test_envelope_error_status_not_success(client, mock):
    mock.get("/users/0.1/self/").respond(200, json={"status": "error", "error": {"code": "X", "detail": "nope"}})
    with pytest.raises(FreelancerError) as exc_info:
        await client.get_self()
    assert exc_info.value.error_code == "X"


async def test_create_bid_payload(client, mock):
    captured = {}

    def handler(request):
        captured["json"] = request.content
        return httpx.Response(200, json={"status": "success", "result": {"id": 9}})

    mock.post("/projects/0.1/bids/").mock(side_effect=handler)
    result = await client.create_bid({"project_id": 1, "bidder_id": 2, "amount": 100})
    assert result["id"] == 9
    assert b'"project_id":1' in captured["json"]


async def test_bid_action_form(client, mock):
    captured = {}

    def handler(request):
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "success", "result": {}})

    mock.put("/projects/0.1/bids/5/").mock(side_effect=handler)
    await client.bid_action(5, "accept")
    assert "action=accept" in captured["body"]


async def test_list_bids_array_params(client, mock):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"status": "success", "result": {"bids": []}})

    mock.get("/projects/0.1/bids/").mock(side_effect=handler)
    await client.list_bids(bidders=[7], award_statuses=["awarded"])
    assert "bidders%5B%5D=7" in captured["url"] or "bidders[]=7" in captured["url"]
    assert "award_statuses%5B%5D=awarded" in captured["url"] or "award_statuses[]=awarded" in captured["url"]


async def test_skills_body_uses_jobs_key(client, mock):
    captured = {}

    def handler(request):
        captured["json"] = request.content
        return httpx.Response(200, json={"status": "success", "result": {}})

    mock.put("/users/0.1/self/jobs").mock(side_effect=handler)
    await client.set_skills([2, 3])
    assert b'"jobs[]"' in captured["json"]


async def test_network_error_retries(client, mock):
    route = mock.get("/users/0.1/self/")
    route.side_effect = [
        httpx.ConnectError("boom"),
        httpx.Response(200, json={"status": "success", "result": {"id": 1}}),
    ]
    me = await client.get_self()
    assert me["id"] == 1
    assert route.call_count == 2
