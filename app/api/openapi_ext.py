# app/api/openapi_ext.py
"""OpenAPI documentation for the untyped legacy endpoints.

The legacy routes read an untyped dict (see `app.api.deps.body_params`) rather
than a Pydantic model, and that is deliberate: one handler has to accept both
form-encoded bodies (the PHP frontend) and JSON (the SPA), and PHP-style
coercion means `client_id=abc` has to resolve to 0 instead of raising 422.
Binding a model to those routes would start rejecting requests the legacy
system accepted.

None of that requires the OpenAPI schema to be empty, though. `openapi_extra`
documents the payload without adding any validation, so `/docs` becomes usable
and the request shape is discoverable, while the handler keeps reading the raw
dict exactly as before. These helpers build those fragments.

Keep the field names here in sync with the `payload.get(...)` calls in the
services — they are documentation, so nothing fails loudly if they drift.
"""
from typing import Any

Fields = dict[str, str]


def body(fields: Fields, *, required: list[str] | None = None) -> dict[str, Any]:
    """Document a request body for both JSON and form encoding.

    `fields` maps field name -> human description. Everything is typed as a
    string because form encoding delivers strings and the services coerce with
    the PHP-compat helpers; declaring `integer` here would misrepresent what
    the endpoint actually accepts.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": desc} for name, desc in fields.items()
        },
    }
    if required:
        schema["required"] = required
    return {
        "requestBody": {
            "content": {
                "application/json": {"schema": schema},
                "application/x-www-form-urlencoded": {"schema": schema},
            }
        }
    }


def query(fields: Fields) -> dict[str, Any]:
    """Document query-string parameters on a GET route."""
    return {
        "parameters": [
            {
                "name": name,
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": desc,
            }
            for name, desc in fields.items()
        ]
    }
