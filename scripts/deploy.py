"""Manual self-hosted deployment using an external, preconfigured environment.

Requires EDUPROJECT_ENV_FILE (outside checkout), EDUPROJECT_PUBLIC_URL (HTTPS),
Docker/Compose, Python 3.12 and an already configured HTTPS reverse proxy.
"""
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_FIELDS = {"APP_ENV", "PUBLIC_ORIGIN", "COOKIE_SECURE", "SESSION_TTL_HOURS", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "TELEGRAM_CLIENT_ID", "TELEGRAM_CLIENT_SECRET"}


def read_environment(path):
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Environment must use KEY=value lines")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def validate_configuration(values, public_url):
    if set(values) - ENVIRONMENT_FIELDS:
        raise ValueError("Unexpected fields in deployment environment")
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("An HTTPS origin is required")
    if values.get("APP_ENV") != "production" or values.get("COOKIE_SECURE", "").lower() != "true":
        raise ValueError("Production security settings are required")
    if values.get("PUBLIC_ORIGIN") != public_url:
        raise ValueError("PUBLIC_ORIGIN must match the HTTPS healthcheck origin")
    if not re.fullmatch(r"[a-fA-F0-9]{64,}", values.get("POSTGRES_PASSWORD", "")):
        raise ValueError("Configure a strong hexadecimal PostgreSQL password outside the repository")
    for key in ("POSTGRES_USER", "POSTGRES_DB"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", values.get(key, "eduproject")):
            raise ValueError("PostgreSQL identifiers must be safe unquoted names")


def main():
    try:
        env_file = Path(os.environ["EDUPROJECT_ENV_FILE"])
        public_url = os.environ["EDUPROJECT_PUBLIC_URL"]
        if not env_file.is_absolute() or not env_file.is_file() or ROOT in env_file.resolve().parents:
            raise ValueError("Use an existing environment file outside the checkout")
        values = read_environment(env_file)
        validate_configuration(values, public_url)
    except (KeyError, OSError, ValueError):
        print("Deployment not started: configure an external production environment and matching HTTPS origin on the dedicated runner.", file=sys.stderr)
        return 2
    # Compose gives shell variables priority over --env-file. Remove conflicting
    # runner values and inject precisely the configuration validated above.
    deployment_env = {key: value for key, value in os.environ.items() if key not in ENVIRONMENT_FIELDS and not key.startswith("COMPOSE_")}
    deployment_env.update(values)
    base = ["docker", "compose", "--file", str(ROOT / "compose.yml"), "--env-file", str(env_file), "--project-name", "eduproject-staging"]
    try:
        for args, timeout in ((["config", "--quiet"], 30), (["up", "--detach", "--build", "--wait", "--wait-timeout", "120"], 600)):
            done = subprocess.run(base + args, cwd=ROOT, env=deployment_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            if done.returncode:
                print("Deployment failed: configuration/build/runtime readiness was not confirmed. Inspect Docker service status on the runner.", file=sys.stderr)
                return 1
        for _ in range(15):
            try:
                with urllib.request.urlopen(public_url + "/health/ready", timeout=5, context=ssl.create_default_context()) as response:
                    body = json.load(response)
                    if response.status == 200 and isinstance(body, dict) and body.get("status") == "ready":
                        print("Deployment readiness confirmed over HTTPS.")
                        return 0
            except (OSError, ValueError):
                pass
            time.sleep(2)
    except (OSError, subprocess.TimeoutExpired):
        print("Deployment failed: required runner tooling or runtime readiness is unavailable.", file=sys.stderr)
        return 1
    print("Deployment is not verified: public HTTPS readiness check failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
