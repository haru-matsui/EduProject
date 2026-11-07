import contextlib
import importlib.util
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import container_entrypoint, deploy, init_env


class EnvironmentInitialization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        (self.directory / ".env.example").write_bytes((ROOT / ".env.example").read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_only_development_password_and_preserves_other_fields(self):
        template = (self.directory / ".env.example").read_text(encoding="utf-8")
        self.assertTrue(init_env.initialize(self.directory))
        result = (self.directory / ".env").read_text(encoding="utf-8")
        self.assertRegex(result, r"(?m)^POSTGRES_PASSWORD=[0-9a-f]{64}$")
        self.assertEqual(re.sub(r"(?m)^POSTGRES_PASSWORD=.*$", "POSTGRES_PASSWORD=", result), template)

    def test_existing_environment_is_never_overwritten(self):
        original = b"EXISTING_PRIVATE_CONFIGURATION=preserve_exactly\r\n"
        (self.directory / ".env").write_bytes(original)
        self.assertFalse(init_env.initialize(self.directory))
        self.assertEqual((self.directory / ".env").read_bytes(), original)

    def test_distinct_environments_receive_distinct_passwords(self):
        init_env.initialize(self.directory)
        first = (self.directory / ".env").read_text()
        (self.directory / ".env").unlink()
        init_env.initialize(self.directory)
        self.assertNotEqual(first, (self.directory / ".env").read_text())

    def test_initializer_refuses_production_or_nonempty_telegram_template(self):
        path = self.directory / ".env.example"
        original = path.read_text(encoding="utf-8")
        for changed in (original.replace("APP_ENV=development", "APP_ENV=production"), original.replace("TELEGRAM_CLIENT_SECRET=", "TELEGRAM_CLIENT_SECRET=do-not-copy")):
            path.write_text(changed, encoding="utf-8")
            with self.assertRaises(ValueError):
                init_env.initialize(self.directory)
            self.assertFalse((self.directory / ".env").exists())


class StartupSafety(unittest.TestCase):
    def test_migration_failure_never_starts_server_or_prints_exception_secret(self):
        error = io.StringIO()
        with patch("alembic.command.upgrade", side_effect=RuntimeError("PRIVATE_DATABASE_SECRET")), patch.object(container_entrypoint.os, "execvp") as execute, contextlib.redirect_stderr(error):
            self.assertEqual(container_entrypoint.main(), 1)
        execute.assert_not_called()
        self.assertNotIn("PRIVATE_DATABASE_SECRET", error.getvalue())

    def test_successful_migration_precedes_exec_with_access_logs_disabled(self):
        with patch("alembic.command.upgrade") as migrate, patch.object(container_entrypoint.os, "execvp") as execute:
            self.assertEqual(container_entrypoint.main(), 0)
        self.assertEqual(migrate.call_args.args[1], "head")
        self.assertIn("--no-access-log", execute.call_args.args[1])
        self.assertIn("app.main:app", execute.call_args.args[1])


class DeploymentSafety(unittest.TestCase):
    def setUp(self):
        self.values = {"APP_ENV": "production", "COOKIE_SECURE": "true", "PUBLIC_ORIGIN": "https://edu.example", "POSTGRES_PASSWORD": "a" * 64}

    def test_rejects_insecure_development_and_mismatched_origins(self):
        for changes, url in (({}, "http://edu.example"), ({"APP_ENV": "development"}, "https://edu.example"), ({"COOKIE_SECURE": "false"}, "https://edu.example"), ({"PUBLIC_ORIGIN": "https://other.example"}, "https://edu.example"), ({"POSTGRES_PASSWORD": "short"}, "https://edu.example")):
            with self.assertRaises(ValueError):
                deploy.validate_configuration({**self.values, **changes}, url)

    def test_accepts_explicit_production_configuration(self):
        deploy.validate_configuration(self.values, "https://edu.example")

    def test_missing_runner_environment_does_not_invoke_docker(self):
        with patch.dict(deploy.os.environ, {}, clear=True), patch.object(deploy.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(deploy.main(), 2)
        run.assert_not_called()

    def test_runner_environment_cannot_override_validated_production_file(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / "production.env"
            checkout = Path(temp) / "checkout"
            env_file.write_text("\n".join(key + "=" + value for key, value in self.values.items()), encoding="utf-8")
            runner_env = {"EDUPROJECT_ENV_FILE": str(env_file), "EDUPROJECT_PUBLIC_URL": "https://edu.example", "COOKIE_SECURE": "false", "APP_ENV": "development", "POSTGRES_PASSWORD": "wrong-runner-password", "COMPOSE_FILE": "unrelated.yml"}
            done = type("Result", (), {"returncode": 1})()
            with patch.object(deploy, "ROOT", checkout), patch.dict(deploy.os.environ, runner_env, clear=True), patch.object(deploy.subprocess, "run", return_value=done) as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(deploy.main(), 1)
            passed = run.call_args.kwargs["env"]
            self.assertEqual(passed["COOKIE_SECURE"], "true")
            self.assertEqual(passed["APP_ENV"], "production")
            self.assertEqual(passed["POSTGRES_PASSWORD"], self.values["POSTGRES_PASSWORD"])
            self.assertNotIn("COMPOSE_FILE", passed)
            self.assertIn(str(checkout / "compose.yml"), run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
