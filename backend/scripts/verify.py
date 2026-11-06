"""Repeatable backend QA. Never prints credentials or touches existing containers.

Run with backend/.venv Python. --postgres requests an isolated Docker PostgreSQL
on 127.0.0.1:55432; if Engine is unavailable, PostgreSQL tests remain explicit skips.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
import xml.etree.ElementTree as ET
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "eduproject-test-db"


def invoke(args, *, env=None, timeout=30):
    try:
        return subprocess.run(args, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 124, type(exc).__name__)


def redact(value, secret_values=()):
    for secret in secret_values:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return re.sub(r"(postgres(?:ql)?(?:\+psycopg)?://[^:/\s]+:)[^@\s]+(@)", r"\1[REDACTED]\2", value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true", help="Try an isolated PostgreSQL 17 Docker container")
    parser.add_argument("--require-postgres", action="store_true", help="Exit 2 if PostgreSQL could not run")
    parser.add_argument("--output", type=Path, default=ROOT / "qa" / "latest")
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error("QA output must be inside backend/")
    output.mkdir(parents=True, exist_ok=True)
    temp = ROOT / ".tmp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", TMP=str(temp), TEMP=str(temp))
    # Do not accidentally exercise some unrelated database inherited by a shell.
    env.pop("TEST_DATABASE_URL", None)
    secrets_to_redact = []
    postgres = {"requested": args.postgres, "exercised": False, "container_created": False, "container_removed": None}
    container_created = False
    run_log = []

    if args.postgres:
        version = invoke(["docker", "version", "--format", "{{.Server.Version}}"], timeout=15)
        if version.returncode != 0:
            postgres["reason"] = "docker_engine_unavailable"
            run_log.append("Docker Engine unavailable; PostgreSQL tests will be reported as skipped.\n" + version.stdout)
        else:
            postgres["docker_server_version"] = version.stdout.strip()
            existing = invoke(["docker", "container", "inspect", CONTAINER, "--format", "{{.Id}}"])
            if existing.returncode == 0:
                postgres["reason"] = "container_name_already_exists_not_modified"
                run_log.append("Existing eduproject-test-db was left untouched. PostgreSQL tests skipped.")
            else:
                password = secrets.token_urlsafe(32)
                invocation = uuid4().hex
                secrets_to_redact.append(password)
                docker_env = dict(env, POSTGRES_PASSWORD=password)
                launch = invoke(["docker", "run", "--detach", "--name", CONTAINER,
                                 "--label", "edu.project=eduproject", "--label", "edu.purpose=backend-tests", "--label", "edu.verification=" + invocation,
                                 "--publish", "127.0.0.1:55432:5432", "--env", "POSTGRES_PASSWORD",
                                 "--env", "POSTGRES_USER=eduproject_test", "--env", "POSTGRES_DB=eduproject_test",
                                 "postgres:17"], env=docker_env, timeout=180)
                if launch.returncode == 0:
                    container_created = True
                    postgres["container_created"] = True
                    for _ in range(30):
                        ready = invoke(["docker", "exec", CONTAINER, "pg_isready", "-U", "eduproject_test", "-d", "eduproject_test"], timeout=5)
                        if ready.returncode == 0:
                            postgres["exercised"] = True
                            env["TEST_DATABASE_URL"] = "postgresql+psycopg://eduproject_test:" + password + "@127.0.0.1:55432/eduproject_test"
                            break
                        time.sleep(1)
                    if not postgres["exercised"]:
                        postgres["reason"] = "postgres_not_ready"
                else:
                    postgres["reason"] = "container_start_failed"
                    run_log.append(launch.stdout)
                    # Docker may create a stopped container before reporting a
                    # bind/start error. Remove it only with our unique label.
                    ownership = invoke(["docker", "container", "inspect", CONTAINER, "--format", '{{index .Config.Labels "edu.verification"}}'])
                    if ownership.returncode == 0 and ownership.stdout.strip() == invocation:
                        container_created = True
                        postgres["container_created"] = True

    started = datetime.now(UTC)
    junit = output / "pytest.xml"
    try:
        result = invoke([sys.executable, "-u", "-m", "pytest", "-q", "-o", "faulthandler_timeout=30", "--junitxml=" + str(junit)], env=env, timeout=240)
        run_log.append(result.stdout)
    finally:
        if container_created:
            # This exact container was created by this invocation only.
            cleanup = invoke(["docker", "rm", "--force", CONTAINER])
            postgres["container_removed"] = cleanup.returncode == 0
            if cleanup.returncode:
                run_log.append("Unable to remove the test container: " + cleanup.stdout)

    if junit.exists():
        junit.write_text(redact(junit.read_text(encoding="utf-8"), secrets_to_redact), encoding="utf-8")
        suites = ET.parse(junit).getroot()
        counts = {name: sum(int(s.get(name, 0)) for s in suites.findall("testsuite")) for name in ("tests", "failures", "errors", "skipped")}
        counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    else:
        counts = {"tests": None, "failures": None, "errors": None, "skipped": None, "passed": None}
    summary = {
        "started_at_utc": started.isoformat(), "finished_at_utc": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0], "pytest_exit_code": result.returncode,
        "counts": counts, "postgresql": postgres,
        "telegram_real_e2e": "pending_client_credentials_and_registered_https_callback",
        "oidc_unit_integration": "signed_fixture_jwt_and_mocked_official_jwks_token_endpoints",
        "runtime_test_login": False,
        "verification_status": "failed" if result.returncode else ("incomplete_external_dependencies" if counts["skipped"] else "passed"),
        "helper_exit_code": result.returncode or (2 if args.require_postgres and not postgres["exercised"] else 0),
    }
    (output / "verify.log").write_text(redact("\n".join(run_log), secrets_to_redact), encoding="utf-8")
    (output / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.returncode:
        return result.returncode
    if args.require_postgres and not postgres["exercised"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
