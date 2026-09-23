import os
import unittest
from unittest.mock import patch

from fastapi import Request
from fastapi.responses import JSONResponse

from .main import desktop_runtime_auth


def make_request(headers=None):
    raw_headers = [
        (str(key).lower().encode("latin1"), str(value).encode("latin1"))
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/health",
        "raw_path": b"/api/health",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
        "root_path": "",
    })
class RequestContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_supplied_request_id(self):
        request = make_request({"X-Request-ID": "req-123"})

        async def call_next(_request):
            return JSONResponse({"ok": True})

        with patch.dict(os.environ, {"TH_MEDIA_AUTH_TOKEN": "", "TH_MEDIA_DESKTOP_MODE": "0"}, clear=False):
            response = await desktop_runtime_auth(request, call_next)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "req-123")
        self.assertEqual(request.state.request_id, "req-123")

    async def test_unauthorized_response_still_has_request_id(self):
        request = make_request({"X-Request-ID": "req-unauthorized"})

        async def call_next(_request):
            raise AssertionError("call_next must not be reached")

        with patch.dict(os.environ, {"TH_MEDIA_AUTH_TOKEN": "expected-token"}, clear=False):
            response = await desktop_runtime_auth(request, call_next)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["X-Request-ID"], "req-unauthorized")


if __name__ == "__main__":
    unittest.main()
