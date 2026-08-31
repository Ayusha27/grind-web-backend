# app/core/responses.py
"""Project-owned orjson response class.

Why this exists rather than `fastapi.responses.ORJSONResponse`: FastAPI has
deprecated its version, on the grounds that it now serialises directly via
Pydantic when a return type or response model is set. That reasoning does not
apply here — the legacy endpoints return bare dicts with no response model
precisely because the key order and value types have to match PHP's
`json_encode` byte for byte, so there is nothing for Pydantic to serialise
through. Taking FastAPI's advice would change the serialiser under a contract
that is asserted byte-for-byte.

`render` is a copy of FastAPI's implementation, so output is unchanged; the
only difference is that using it does not emit a deprecation warning on every
single request.
"""
from typing import Any

import orjson
from starlette.responses import JSONResponse


class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(
            content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY
        )
