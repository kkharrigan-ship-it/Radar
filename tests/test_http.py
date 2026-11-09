import io
import socket
import unittest
import urllib.error
from unittest import mock

from radar import http


class UrllibTransportErrors(unittest.TestCase):
    URL = "https://example.invalid/secret-path-9000000000000000001"

    def call(self, effect):
        with mock.patch("urllib.request.urlopen", side_effect=effect):
            return http.UrllibTransport().request("GET", self.URL, {}, None, 1.0)

    def test_http_error_status_is_returned_not_raised(self):
        err = urllib.error.HTTPError(self.URL, 503, "busy", {"Retry-After": "3"}, io.BytesIO(b"{}"))
        r = self.call(err)
        self.assertEqual(r.status, 503)
        self.assertEqual(r.headers.get("Retry-After"), "3")

    def test_timeout_after_send(self):
        for effect in (TimeoutError(), socket.timeout()):
            with self.subTest(effect=effect), self.assertRaises(http.Timeout) as cm:
                self.call(effect)
            self.assertNotIn("example", str(cm.exception))

    def test_dns_refused_tls_are_no_connection(self):
        for reason in (socket.gaierror("dns"), ConnectionRefusedError(), __import__("ssl").SSLError("tls")):
            with self.subTest(reason=reason), self.assertRaises(http.NoConnection) as cm:
                self.call(urllib.error.URLError(reason))
            self.assertNotIn("example", str(cm.exception))

    def test_url_error_wrapping_timeout_is_timeout(self):
        with self.assertRaises(http.Timeout):
            self.call(urllib.error.URLError(socket.timeout("x")))
