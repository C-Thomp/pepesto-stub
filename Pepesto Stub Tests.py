"""Tests for the Pepesto stub server. Starts its own instance on a free port."""
import json, socket, subprocess, sys, threading, time
from http.server import HTTPServer

import pytest
import requests

sys.path.insert(0, "/rool-drive/pepesto-stub")
import importlib.util
spec = importlib.util.spec_from_file_location(
    "stub", "/rool-drive/pepesto-stub/Pepesto Stub Server.py")
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)


@pytest.fixture(scope="module")
def base_url():
    # free port
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    stub.PORT = port
    stub.HOSTED_BASE = f"http://127.0.0.1:{port}"
    server = HTTPServer(("127.0.0.1", port), stub.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_supermarkets(base_url):
    r = requests.get(f"{base_url}/api/supermarkets")
    assert r.status_code == 200 and "tesco.com" in r.json()["supermarkets"]


def test_parse_requires_source(base_url):
    assert requests.post(f"{base_url}/api/parse", json={}).status_code == 400


def test_parse_returns_token(base_url):
    r = requests.post(f"{base_url}/api/parse", json={"recipe_text": "eggs and pasta"})
    assert r.status_code == 200
    body = r.json()
    assert body["kg_token"].startswith("kg_")
    # deterministic
    r2 = requests.post(f"{base_url}/api/parse", json={"recipe_text": "eggs and pasta"})
    assert r2.json()["kg_token"] == body["kg_token"]


def test_products_indexed_and_not_indexed(base_url):
    r = requests.post(f"{base_url}/api/products", json={
        "supermarket_domain": "tesco.com",
        "manual_shopping_list": "4 eggs, 500g pasta, mystery spice"})
    body = r.json()
    names = {i["item_name"] for i in body["items"]}
    assert names == {"eggs", "pasta"}
    assert body["not_indexed_items"] == ["mystery spice"]


def test_products_gbp_for_uk_domain(base_url):
    r = requests.post(f"{base_url}/api/products", json={
        "supermarket_domain": "sainsburys.co.uk", "manual_shopping_list": "milk"})
    assert r.json()["items"][0]["products"][0]["product"]["price"]["currency"] == "GBP"


def test_session_limits(base_url):
    r = requests.post(f"{base_url}/api/session", json={"supermarket_domain": "tesco.com"})
    assert r.status_code == 400
    r = requests.post(f"{base_url}/api/session", json={
        "supermarket_domain": "tesco.com",
        "unresolved_items": [f"item{i}" for i in range(11)]})
    assert r.status_code == 400
    ok = requests.post(f"{base_url}/api/session", json={
        "supermarket_domain": "tesco.com", "skus": [{"session_token": "kg_x"}]})
    body = ok.json()
    assert ok.status_code == 200 and body["session_id"].startswith("stub-session-")
    assert "mobile_hosted_url" in body


def test_checkout_script_shape(base_url):
    sid = requests.post(f"{base_url}/api/session", json={
        "supermarket_domain": "coop.ch",
        "skus": [{"session_token": "kg_a"}],
        "unresolved_items": ["nutmeg"]}).json()["session_id"]
    types = []
    while True:
        proto = requests.post(f"{base_url}/api/checkout",
                              json={"continue_session_id": sid}).json()["proto"]
        types.append(proto["type"])
        assert "attach_screenshot_on_next_turn" in proto
        if proto["type"] == "done":
            break
        assert types.count("done") == 0
    assert types[0] == "load_page" and "coop.ch" in types[0] or "load_page" == types[0]
    assert "run_js" in types and "prompt_user_action" in types


def test_checkout_unknown_session(base_url):
    r = requests.post(f"{base_url}/api/checkout", json={"continue_session_id": "nope"})
    assert r.status_code == 404


def test_oneshot(base_url):
    r = requests.post(f"{base_url}/api/oneshot", json={"content_url": "https://x.example/r"})
    assert r.status_code == 200 and "redirect_url" in r.json()
    assert requests.post(f"{base_url}/api/oneshot", json={}).status_code == 400


def test_predirect(base_url):
    r = requests.post(f"{base_url}/api/predirect", json={"shopping_list": "milk, bread"})
    assert r.json()["redirect_url"].startswith("pepesto://checkout")
    assert requests.post(f"{base_url}/api/predirect", json={}).status_code == 400


def test_catalog_promo_blocked_without_multiplier(base_url, monkeypatch):
    stub.ALLOW_FULL_CATALOG = False
    assert requests.post(f"{base_url}/api/catalog", json={"promo_only": True}).status_code == 403
    assert requests.post(f"{base_url}/api/catalog", json={}).status_code == 403
    # preselected product_urls always allowed
    r = requests.post(f"{base_url}/api/catalog", json={
        "supermarket_domain": "tesco.com",
        "product_urls": ["https://www.tesco.com/product/bread"]})
    assert r.status_code == 200 and r.json()["products"][0]["product_name"].startswith("Bread")


def test_catalog_promo_and_urls_mutually_exclusive(base_url):
    r = requests.post(f"{base_url}/api/catalog", json={
        "promo_only": True, "product_urls": ["https://x/product/a"]})
    assert r.status_code == 400


def test_suggest(base_url):
    r = requests.post(f"{base_url}/api/suggest", json={"query": "dinner"})
    assert r.status_code == 200 and len(r.json()["recipes"]) == 2


def test_auth_enforced_when_key_set(base_url, monkeypatch):
    stub.API_KEY = "secret"
    try:
        r = requests.post(f"{base_url}/api/parse", json={"recipe_text": "x"},
                          headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = requests.post(f"{base_url}/api/parse", json={"recipe_text": "x"},
                          headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200
    finally:
        stub.API_KEY = None
