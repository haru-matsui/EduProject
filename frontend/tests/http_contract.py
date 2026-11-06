"""Read-only HTTP integration against the actual FastAPI static/API contract.

Uses TestClient in-process. It does not launch a browser, create a login/session,
write business records or imply a real PostgreSQL/Telegram end-to-end result.
"""
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND.parent / "backend"))

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.inline_scripts = 0
        self.inline_handlers = []
        self.inline_styles = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.inline_handlers.extend(key for key, _ in attrs if key.startswith("on"))
        if "style" in values or tag == "style":
            self.inline_styles += 1
        if tag == "script":
            if values.get("src"):
                self.assets.append(values["src"])
            else:
                self.inline_scripts += 1
        if tag == "link" and values.get("rel") == "stylesheet":
            self.assets.append(values["href"])


class StaticContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = Settings(
            app_env="test", database_url="sqlite+pysqlite:///:memory:",
            public_origin="http://testserver", cookie_secure=False,
            telegram_client_id="", telegram_client_secret="",
            frontend_dir=FRONTEND, _env_file=None,
        )
        cls.app = create_app(settings)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.app.state.engine.dispose()

    def test_login_html_and_csp_match_local_resources(self):
        reply = self.client.get("/login")
        self.assertEqual(reply.status_code, 200)
        self.assertIn("text/html", reply.headers["content-type"])
        parser = AssetParser()
        parser.feed(reply.text)
        self.assertCountEqual(parser.assets, ["/assets/vendor/bootstrap.min.css", "/assets/app.css", "/assets/app.js"])
        self.assertEqual(parser.inline_scripts, 0)
        self.assertEqual(parser.inline_styles, 0)
        self.assertEqual(parser.inline_handlers, [])
        csp = reply.headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("data:", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(reply.headers["referrer-policy"], "no-referrer")
        self.assertEqual(reply.headers["cache-control"], "no-store")

    def test_all_spa_routes_deliver_the_actual_entrypoint(self):
        entry = (FRONTEND / "index.html").read_bytes()
        paths = ["/projects", "/projects/11111111-1111-4111-8111-111111111111", "/invitations/valid-invitation-token-long-enough"]
        for path in paths:
            with self.subTest(path=path):
                reply = self.client.get(path)
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.content, entry)

    def test_assets_are_served_locally_with_expected_content_types(self):
        for relative, expected in [("app.js", "javascript"), ("api.js", "javascript"), ("app.css", "text/css"), ("vendor/bootstrap.min.css", "text/css")]:
            with self.subTest(asset=relative):
                reply = self.client.get("/assets/" + relative)
                self.assertEqual(reply.status_code, 200)
                self.assertIn(expected, reply.headers["content-type"])
                self.assertEqual(reply.content, (FRONTEND / "assets" / relative).read_bytes())
                self.assertEqual(reply.headers["x-content-type-options"], "nosniff")

    def test_missing_auth_configuration_is_an_explicit_public_boolean(self):
        reply = self.client.get("/api/v1/auth/configuration")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json(), {"telegram_login_configured": False})
        self.assertNotIn("set-cookie", reply.headers)

    def test_unconfigured_telegram_returns_503_without_a_session(self):
        reply = self.client.get("/api/v1/auth/telegram/start?return_path=/projects", follow_redirects=False)
        self.assertEqual(reply.status_code, 503)
        self.assertEqual(reply.json()["error"]["code"], "auth_not_configured")
        self.assertNotIn("set-cookie", reply.headers)
        self.assertEqual(len(self.client.cookies), 0)

    def test_anonymous_session_projects_and_logout_are_not_successful(self):
        for method, path in [("GET", "/api/v1/auth/session"), ("GET", "/api/v1/projects"), ("POST", "/api/v1/auth/logout")]:
            with self.subTest(method=method, path=path):
                reply = self.client.request(method, path)
                self.assertEqual(reply.status_code, 401)
                self.assertEqual(reply.json()["error"]["code"], "authentication_required")

    def test_task_routes_methods_and_fields_match_the_frontend(self):
        spec = self.client.get("/api/openapi.json").json()
        paths = spec["paths"]
        prefix = "/api/v1/projects/{project_id}/tasks/{task_id}"
        self.assertIn("get", paths[prefix])
        self.assertIn("put", paths[prefix + "/assignee"])
        self.assertIn("post", paths[prefix + "/start"])
        schemas = spec["components"]["schemas"]
        self.assertEqual(set(schemas["ProjectCreate"]["required"]), {"title", "description", "goal", "expected_result", "due_at"})
        self.assertEqual(set(schemas["TaskCreate"]["required"]), {"title", "description", "expected_result", "due_at", "priority"})
        self.assertIn("csrf_token", schemas["SessionView"]["properties"])
        self.assertIn("current_role", schemas["ProjectView"]["properties"])
        self.assertIn("user", schemas["MemberView"]["properties"])
        self.assertEqual(set(schemas["AssigneeUpdate"]["properties"]), {"assignee_member_id"})

    def test_bootstrap_is_pinned_and_documented(self):
        source = (FRONTEND / "assets/vendor/bootstrap.min.css").read_text(encoding="utf-8")
        self.assertIn("Bootstrap  v5.3.3", source[:250])
        self.assertIn("Licensed under MIT", source[:400])
        self.assertIn("Copyright", (FRONTEND / "assets/vendor/LICENSE.bootstrap.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StaticContract)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    payload = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "kind": "in_process_fastapi_read_only_http_contract",
        "tests": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "browser_opened": False,
        "production_login_created": False,
        "postgresql_e2e": False,
    }
    out = FRONTEND / "tests/results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "http-contract.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
