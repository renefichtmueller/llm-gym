import json
import struct
import unittest

from fastapi.testclient import TestClient

from llm_gym import terminal


class TerminalPwaTests(unittest.TestCase):
    def setUp(self) -> None:
        terminal.config.update(
            token="pwa",
            access_team="",
            access_aud="",
            access_only=False,
        )
        self.client = TestClient(terminal.app)

    def authorize(self) -> None:
        self.client.cookies.set(terminal.COOKIE, "pwa")

    def test_terminal_exposes_clipboard_and_pwa_controls(self) -> None:
        self.authorize()
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="paste"', response.text)
        self.assertIn('id="pwa-update"', response.text)
        self.assertIn("navigator.clipboard?.readText", response.text)
        self.assertIn("term.paste(text)", response.text)
        self.assertIn('register("/service-worker.js"', response.text)
        self.assertIn('rel="manifest"', response.text)

    def test_service_worker_requires_auth_and_disables_http_cache(self) -> None:
        self.assertEqual(self.client.get("/service-worker.js").status_code, 403)

        self.authorize()
        response = self.client.get("/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["cache-control"],
            "no-cache, no-store, must-revalidate",
        )
        self.assertEqual(response.headers["service-worker-allowed"], "/")
        self.assertIn('fetch(request, { cache: "no-store" })', response.text)

    def test_terminal_manifest_is_installable(self) -> None:
        response = self.client.get("/static/terminal.webmanifest")
        manifest = json.loads(response.text)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(
            [icon["sizes"] for icon in manifest["icons"]],
            ["192x192", "512x512"],
        )

    def test_manifest_icon_files_match_declared_sizes(self) -> None:
        for filename, expected in (("icon-192.png", 192), ("icon-512.png", 512)):
            data = (terminal.STATIC / "terminal-icons" / filename).read_bytes()
            width, height = struct.unpack(">II", data[16:24])
            self.assertEqual(data[1:4], b"PNG")
            self.assertEqual((width, height), (expected, expected))


if __name__ == "__main__":
    unittest.main()
