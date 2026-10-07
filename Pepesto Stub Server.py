#!/usr/bin/env python3
"""
Pepesto API stub — a stateful, offline mock of the Pepesto grocery-shopping API,
built from the published protocols at https://www.pepesto.com/ai-grocery-shopping-agent/

Why: the real API charges per call (e.g. EUR 0.32 per /parse, EUR 1.20 per /session,
EUR 9.60 per full /catalog), so testing against the live service is expensive. This
stub implements the documented request/response shapes so your app's end-of-journey
basket-filling flow can be tested for free.

Run:
    python3 "Pepesto Stub Server.py"            # listens on 127.0.0.1:8977
    PEPESTO_STUB_PORT=9000 python3 "Pepesto Stub Server.py"

Point your client at it by swapping the base URL:
    PEPESTO_BASE_URL=http://127.0.0.1:8977

Behaviour notes
---------------
- /api/supermarkets -> static list of supported supermarket domains.
- /api/parse        -> returns a kg_token derived deterministically from the input.
- /api/products     -> returns indexed fake products per item; unknown items land in
                      not_indexed_items. Understands crude quantities in free text.
- /api/session      -> creates a session (max 50 items, max 10 unresolved, like the
                      real API) and returns session_id + mobile_hosted_url.
- /api/mcheckout    -> alias of /api/session (same types per docs).
- /api/checkout     -> advances a checkout-driving session one "turn" per call and
                      returns exactly one instruction (load_page / await_element /
                      run_js / prompt_user_action / await_js_out_change / done) plus
                      attach_screenshot_on_next_turn. Errors on unknown sessions.
- /api/oneshot      -> returns a redirect_url pointing at the stub's fake hosted UI.
- /api/predirect    -> returns a deferred deep link (free-endpoint semantics).
- /api/suggest      -> returns canned recipes for the query.
- /api/catalog      -> full catalog / promo_only / preselected product_urls behaviour,
                      mirroring the docs: full + promo need an "active discount
                      multiplier" (stub: set PEPESTO_STUB_ALLOW_FULL_CATALOG=1).

Auth: accepts any bearer token; set PEPESTO_STUB_API_KEY to enforce a value, handy
for testing your auth handling.
"""

import json, os, re, sys, threading, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PEPESTO_STUB_PORT", "8977"))
API_KEY = os.environ.get("PEPESTO_STUB_API_KEY")
ALLOW_FULL_CATALOG = os.environ.get("PEPESTO_STUB_ALLOW_FULL_CATALOG") == "1"
HOSTED_BASE = os.environ.get("PEPESTO_STUB_HOSTED_BASE", f"http://127.0.0.1:{PORT}")

SUPERMARKETS = ["tesco.com", "sainsburys.co.uk", "coop.ch", "rewe.de", "carrefour.fr"]

# A tiny deterministic fake index: name -> (category, price, unit)
FAKE_INDEX = {
    "eggs":           ("eggs", 2.35, "10 x free range eggs"),
    "pasta":          ("pasta", 1.10, "500 g penne"),
    "chicken breast": ("meat", 5.49, "450 g chicken breast fillets"),
    "tomatoes":       ("vegetables", 1.85, "6 pack tomatoes"),
    "mozzarella":     ("cheese", 1.65, "125 g mozzarella"),
    "basil":          ("herbs", 0.95, "30 g fresh basil"),
    "olive oil":      ("oils", 6.20, "500 ml olive oil"),
    "flour":          ("baking", 1.05, "1 kg plain flour"),
    "milk":           ("dairy", 1.15, "2 l semi-skimmed milk"),
    "bread":          ("bakery", 1.40, "800 g white loaf"),
    "avocados":       ("fruit", 2.00, "2 ripe avocados"),
    "rice":           ("grains", 2.25, "1 kg long grain rice"),
    "onions":         ("vegetables", 0.89, "1 kg onions"),
    "garlic":         ("vegetables", 0.60, "1 garlic bulb"),
    "salt":           ("pantry", 0.55, "750 g table salt"),
}

def _token(seed: str) -> str:
    """Deterministic opaque token, like the real KgToken/SessionToken."""
    return "kg_" + hashlib.sha1(seed.encode()).hexdigest()[:24]

def _currency(domain: str) -> str:
    return {"uk": "GBP", "ch": "CHF", "de": "EUR", "fr": "EUR"}.get(domain.rsplit(".", 1)[-1], "EUR")

def _fake_product(item: str, domain: str) -> dict:
    cat, price, qty = FAKE_INDEX.get(item, ("groceries", 1.99, "1 pack"))
    slug = re.sub(r"[^a-z0-9]+", "-", item.lower()).strip("-")
    return {
        "product_name": f"{item.capitalize()} - {qty}",
        "category": cat,
        "quantity": {"value": 1, "unit": "unit", "display": qty},
        "price": {"value": price, "currency": _currency(domain)},
        "product_id": f"https://www.{domain}/product/{slug}",
        "image_url": f"{HOSTED_BASE}/images/{slug}.jpg",
        "pepesto_hosted_image_url": f"{HOSTED_BASE}/images/{slug}.jpg",
        "universal_id": abs(hash((item, domain))) % 10_000_000_000,
        "gtin14": [],
        "session_token": _token(f"{item}@{domain}"),
    }

# ---------------------------------------------------------------- sessions ----
_lock = threading.Lock()
_sessions = {}     # session_id -> basket info
_checkout = {}     # continue_session_id -> turn index
_next_sid = [1000]

class Handler(BaseHTTPRequestHandler):
    server_version = "PepestoStub/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.command, fmt % args))

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self) -> bool:
        if not API_KEY:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {API_KEY}"

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        if self.path.rstrip("/") == "/api/supermarkets":
            return self._json(200, {"supermarkets": SUPERMARKETS})
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._auth():
            return self._json(401, {"error": "unauthorized", "message": "invalid bearer token"})
        body = self._body()
        if body is None:
            return self._json(400, {"error": "bad_json"})
        route = {
            "/api/parse": self._parse,
            "/api/products": self._products,
            "/api/session": self._session,
            "/api/mcheckout": self._session,
            "/api/checkout": self._checkout,
            "/api/oneshot": self._oneshot,
            "/api/predirect": self._predirect,
            "/api/suggest": self._suggest,
            "/api/catalog": self._catalog,
        }.get(self.path.rstrip("/"))
        if route is None:
            return self._json(404, {"error": "not_found", "path": self.path})
        return route(body)

    # endpoints --------------------------------------------------------------
    def _parse(self, body):
        src = body.get("recipe_url") or body.get("recipe_text") or body.get("content_image") or ""
        if not src:
            return self._json(400, {"error": "bad_request",
                "message": "exactly one of recipe_url, recipe_text, content_image is required"})
        return self._json(200, {
            "kg_token": _token("parse:" + src),
            "recipe": {  # deprecated field, kept for compatibility
                "title": "Stubbed Recipe",
                "ingredients": sorted(FAKE_INDEX)[:8],
                "author_ingredients": [],
                "steps": ["(stub) step one", "(stub) step two"],
            },
        })

    def _products(self, body):
        domain = body.get("supermarket_domain") or SUPERMARKETS[0]
        manual = (body.get("manual_shopping_list") or "").lower()
        names = []
        for chunk in re.split(r"[,\n]", manual):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.match(r"^(?:\d+\s*(?:g|kg|ml|l)?\s*of\s*|\d+\s*x\s*|\d+\s*(?:g|kg|ml|l)?\s+|\d+(?:g|kg|ml|l)\s*|\d+\s+)?(.+)$", chunk)
            if m:
                names.append(m.group(1).strip())
        if not names:
            kg_tokens = body.get("recipe_kg_tokens") or []
            if kg_tokens:
                # token -> fixed basket for determinism
                names = sorted(FAKE_INDEX)[:5]
        items, not_indexed = [], []
        for name in names[:50]:
            if name in FAKE_INDEX:
                p = _fake_product(name, domain)
                items.append({"item_name": name, "products": [{
                    "product": p,
                    "session_token": p["session_token"],
                    "suggested_quantity": {"num_units_to_buy": 1, "rationale": "stub default"},
                }]})
            else:
                not_indexed.append(name)
        return self._json(200, {"items": items, "not_indexed_items": not_indexed,
                                "currency": _currency(domain)})

    def _session(self, body):
        skus = body.get("skus") or []
        dep = body.get("items") or []
        unresolved = body.get("unresolved_items") or []
        total = len(skus) + len(dep) + len(unresolved)
        if total == 0:
            return self._json(400, {"error": "bad_request",
                "message": "at least one of skus, items or unresolved_items is required"})
        if total > 50:
            return self._json(400, {"error": "too_many_items",
                "message": "at most 50 total basket items across skus, items and unresolved_items"})
        if len(unresolved) > 10:
            return self._json(400, {"error": "too_many_unresolved",
                "message": "at most 10 unresolved_items are allowed"})
        if not body.get("supermarket_domain"):
            return self._json(400, {"error": "bad_request", "message": "supermarket_domain is required"})
        with _lock:
            _next_sid[0] += 1
            sid = f"stub-session-{_next_sid[0]}"
            _sessions[sid] = {
                "supermarket_domain": body["supermarket_domain"],
                "skus": skus, "items": dep, "unresolved_items": unresolved,
                "redirect_url": body.get("redirect_url"),
                "skip_confirmation_screen": bool(body.get("skip_confirmation_screen")),
            }
        return self._json(200, {"session_id": sid,
                                "mobile_hosted_url": f"{HOSTED_BASE}/hosted/{sid}"})

    def _checkout(self, body):
        sid = body.get("continue_session_id")
        if not sid:
            return self._json(400, {"error": "bad_request",
                "message": "continue_session_id is required"})
        if sid not in _sessions and not sid.startswith("app-"):
            return self._json(404, {"error": "unknown_session",
                "message": "sessions must be obtained from Pepesto (or the stub) first"})
        with _lock:
            turn = _checkout.get(sid, 0)
            _checkout[sid] = turn + 1
        script = self._checkout_script(sid)
        if turn >= len(script):
            instr = {"type": "done"}
        else:
            instr = script[turn]
        instr.setdefault("attach_screenshot_on_next_turn", False)
        return self._json(200, {"session_id": sid, "proto": instr})

    def _checkout_script(self, sid):
        """A realistic end-of-journey: load supermarket, sign in, add items, done."""
        sess = _sessions.get(sid, {})
        domain = sess.get("supermarket_domain", "tesco.com")
        tokens = [s.get("session_token", "?") for s in sess.get("skus", [])]
        unresolved = sess.get("unresolved_items", [])
        script = [
            {"type": "load_page", "url": f"https://www.{domain}/", "reason": "open storefront"},
            {"type": "await_element", "selector": "[data-testid='header-basket']", "timeout_ms": 5000},
            {"type": "prompt_user_action", "message": "Please sign in to your supermarket account",
             "action": "enter_credentials"},
        ]
        for tok in tokens:
            script.append({"type": "run_js", "js": f"window.__addToBasket('{tok}')",
                           "expected_out": "ok"})
            script.append({"type": "await_js_out_change",
                           "selector": "[data-testid='basket-count']",
                           "timeout_ms": 3000, "attach_screenshot_on_next_turn": True})
        for term in unresolved:
            script.append({"type": "run_js", "js": f"window.__searchAndAdd({term!r})",
                           "expected_out": "ok"})
        script.append({"type": "prompt_user_action", "message": "Review the basket and confirm",
                       "action": "confirm_basket"})
        script.append({"type": "done", "note": "payment is always completed by the user"})
        return script

    def _oneshot(self, body):
        src = body.get("content_url") or body.get("content_text") or body.get("content_image") or ""
        if not src:
            return self._json(400, {"error": "bad_request",
                "message": "one of content_url, content_text, content_image is required"})
        with _lock:
            _next_sid[0] += 1
            sid = f"stub-oneshot-{_next_sid[0]}"
        return self._json(200, {"redirect_url": f"{HOSTED_BASE}/s/{sid}"})

    def _predirect(self, body):
        if not body.get("shopping_list"):
            return self._json(400, {"error": "bad_request", "message": "shopping_list is required"})
        with _lock:
            _next_sid[0] += 1
            sid = f"stub-predirect-{_next_sid[0]}"
        return self._json(200, {"redirect_url": f"pepesto://checkout?stub_session={sid}"})

    def _suggest(self, body):
        q = body.get("query") or ""
        return self._json(200, {
            "recipes": [
                {"title": "Quick Pasta Pomodoro",
                 "ingredients": ["pasta", "tomatoes", "garlic", "olive oil", "basil"],
                 "steps": ["boil pasta", "make sauce", "combine"]},
                {"title": "Simple Chicken & Rice",
                 "ingredients": ["chicken breast", "rice", "onions"],
                 "steps": ["sear chicken", "simmer rice", "serve"]},
            ],
            "short_response": f"(stub) Here are two ideas for: {q[:80]}",
        })

    def _catalog(self, body):
        domain = body.get("supermarket_domain") or ""
        promo = bool(body.get("promo_only"))
        urls = body.get("product_urls") or []
        if urls and promo:
            return self._json(400, {"error": "bad_request",
                "message": "specify only one of promo_only and product_urls"})
        if not urls and (promo or not ALLOW_FULL_CATALOG):
            return self._json(403, {"error": "plan_required",
                "message": "full catalog and promotional-product requests require an active "
                           "discount multiplier (set PEPESTO_STUB_ALLOW_FULL_CATALOG=1 in the stub)"})
        if urls:
            if len(urls) > 50:
                return self._json(400, {"error": "too_many_urls", "message": "at most 50 product_urls"})
            items = [re.sub(r".*/product/", "", u).replace("-", " ") for u in urls]
        else:
            items = sorted(FAKE_INDEX)
        return self._json(200, {"products": [_fake_product(i, domain or SUPERMARKETS[0]) for i in items]})


if __name__ == "__main__":
    print(f"Pepesto stub listening on http://127.0.0.1:{PORT}")
    print(f"  supermarkets: {', '.join(SUPERMARKETS)}")
    print(f"  api key enforcement: {'ON' if API_KEY else 'off'}")
    print(f"  full catalog: {'allowed' if ALLOW_FULL_CATALOG else 'blocked (403, like Starter Pack)'}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
