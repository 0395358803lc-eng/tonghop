import unittest

import httpx

from .flow_bridge_client import FlowBridgeError, _raise_for_bridge_response


class FlowBridgeClientResponseTests(unittest.TestCase):
    @staticmethod
    def _response(status: int, payload=None, text: str | None = None):
        request = httpx.Request("GET", "http://127.0.0.1:8765/v1/jobs/job-test")
        if payload is not None:
            return httpx.Response(status, json=payload, request=request)
        return httpx.Response(status, text=text or "", request=request)

    def test_poll_409_is_session_expired(self):
        response = self._response(409, {"detail": "Dedicated Flow session cần đăng nhập lại."})
        with self.assertRaisesRegex(FlowBridgeError, r"^SESSION_EXPIRED:"):
            _raise_for_bridge_response(
                response,
                session_detail="Flow session hết hạn trong lúc đang chờ generation.",
            )

    def test_poll_401_is_bridge_auth_error(self):
        response = self._response(401, {"detail": "Unauthorized"})
        with self.assertRaisesRegex(FlowBridgeError, r"^BRIDGE_AUTH_ERROR:"):
            _raise_for_bridge_response(response)

    def test_poll_other_http_error_keeps_status_code(self):
        response = self._response(503, text="temporarily unavailable")
        with self.assertRaisesRegex(FlowBridgeError, r"^FLOW_BRIDGE_HTTP_503:"):
            _raise_for_bridge_response(response)

    def test_success_response_does_not_raise(self):
        _raise_for_bridge_response(self._response(200, {"status": "running"}))


if __name__ == "__main__":
    unittest.main()
