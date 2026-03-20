import unittest

from firefox_extension_proxy import ProxyConfig, html_page, parse_args, validate_upstream_url


class UrlValidationTests(unittest.TestCase):
    def test_accepts_https_url(self) -> None:
        self.assertEqual(validate_upstream_url("https://example.com/path?q=1"), "https://example.com/path?q=1")

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(ValueError):
            validate_upstream_url("file:///tmp/test.html")


class HtmlPageTests(unittest.TestCase):
    def test_page_contains_stream_endpoints(self) -> None:
        payload = html_page(ProxyConfig(), "https://example.com/", None).decode("utf-8")
        self.assertIn("/stream.mjpg", payload)
        self.assertIn("/snapshot.jpg", payload)
        self.assertIn("https://example.com/", payload)

    def test_page_renders_error_message(self) -> None:
        payload = html_page(ProxyConfig(), "https://example.com/", "bad input").decode("utf-8")
        self.assertIn("Browser error", payload)
        self.assertIn("bad input", payload)


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_supports_multiple_addons(self) -> None:
        config = parse_args(
            [
                "--start-url",
                "https://example.com/",
                "--addon",
                "uBlock.xpi",
                "--addon",
                "other.xpi",
                "--listen-port",
                "9000",
                "--no-headless",
            ]
        )
        self.assertEqual(config.listen_port, 9000)
        self.assertEqual(config.addons, ["uBlock.xpi", "other.xpi"])
        self.assertFalse(config.headless)


if __name__ == "__main__":
    unittest.main()
