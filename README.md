# Pepesto Stub Server

An offline, stateful mock of the Pepesto grocery-shopping API, built from the
published protocols at https://www.pepesto.com/ai-grocery-shopping-agent/.
Use it to test your app's end-of-journey basket-filling flow without paying for
live API calls.

## Run

```bash
python3 "Pepesto Stub Server.py"                 # listens on 127.0.0.1:8977
PEPESTO_STUB_PORT=9000 python3 "Pepesto Stub Server.py"
```

Point your app at it:

```bash
PEPESTO_BASE_URL=http://127.0.0.1:8977
```

Optional environment variables:

| Variable                        | Effect                                                        |
|---------------------------------|---------------------------------------------------------------|
| `PEPESTO_STUB_PORT`             | Listen port (default 8977)                                     |
| `PEPESTO_STUB_API_KEY`          | Enforce a bearer token (otherwise any token is accepted)       |
| `PEPESTO_STUB_ALLOW_FULL_CATALOG` | `1` = allow full-catalog requests (default: 403, like Starter Pack) |
| `PEPESTO_STUB_HOSTED_BASE`      | Base URL used in hosted/redirect links (default same host)     |

## Endpoints

| Endpoint            | Method | Notes                                                              |
|---------------------|--------|--------------------------------------------------------------------|
| `/api/supermarkets` | GET    | Static list of supported supermarket domains.                      |
| `/api/parse`        | POST   | Accepts `recipe_url` / `recipe_text` / `content_image`; returns a deterministic `kg_token` plus a deprecated `recipe` payload. |
| `/api/products`     | POST   | Builds products from `manual_shopping_list` (understands quantities like "4 eggs", "500g pasta") or `recipe_kg_tokens`. Unknown items come back in `not_indexed_items`. |
| `/api/session`      | POST   | Creates a session; enforces the documented limits (max 50 total items, max 10 `unresolved_items`, `supermarket_domain` required). Returns `session_id` + `mobile_hosted_url`. |
| `/api/mcheckout`    | POST   | Alias of `/api/session`.                                           |
| `/api/checkout`     | POST   | Sends `continue_session_id`; each call returns the next instruction in a scripted checkout flow: `load_page`, `await_element`, `prompt_user_action` (sign-in), `run_js` add-to-basket, `await_js_out_change`, confirm, `done`. Unknown sessions return 404. |
| `/api/oneshot`      | POST   | `content_url` / `content_text` / `content_image` -> `redirect_url` to the stub's fake hosted UI. |
| `/api/predirect`    | POST   | `shopping_list` -> deferred deep link (`pepesto://checkout?stub_session=...`). |
| `/api/suggest`      | POST   | Returns two canned recipes for any `query`.                        |
| `/api/catalog`      | POST   | `promo_only` and `product_urls` (mutually exclusive); full-catalog and promo requests return 403 unless `PEPESTO_STUB_ALLOW_FULL_CATALOG=1`, mirroring the real Starter Pack restriction. |

## Fake product index

15 deterministic products (eggs, pasta, chicken breast, tomatoes, mozzarella,
basil, olive oil, flour, milk, bread, avocados, rice, onions, garlic, salt).
Edit `FAKE_INDEX` in the server file to add more. Currency is derived from the
supermarket domain (UK = GBP, CH = CHF, DE/FR = EUR).

## Tests

Run the pytest suite:

```bash
pip install pytest requests
python3 -m pytest "Pepesto Stub Tests.py" -v
```

The test suite starts its own server instance on a free port.
