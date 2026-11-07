"""Validate Compose/Actions structure without contacting Docker Engine."""
import json
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def check():
    checks = []

    def require(condition, name):
        if not condition:
            raise ValueError(name)
        checks.append(name)

    def read(name):
        # BaseLoader follows GitHub's meaning of the `on` key rather than
        # treating that YAML 1.2 key as a YAML 1.1 boolean.
        return yaml.load((ROOT / name).read_text(encoding="utf-8-sig"), Loader=yaml.BaseLoader)

    compose = read("compose.yml")
    require(set(compose["services"]) == {"db", "app"}, "compose contains only EduProject db/app")
    db, app = compose["services"]["db"], compose["services"]["app"]
    require(db["image"] == "postgres:17-alpine", "PostgreSQL 17 image")
    require("ports" not in db, "database has no host port")
    require(app["ports"] == ["127.0.0.1:8000:8000"], "application binds only loopback")
    require(app["depends_on"]["db"]["condition"] == "service_healthy", "application waits for healthy database")
    require(bool(db.get("healthcheck", {}).get("test")) and bool(app.get("healthcheck", {}).get("test")), "both services have healthchecks")
    require("eduproject_pgdata" in compose["volumes"], "project-specific database volume")
    require(app["read_only"] == "true" and "ALL" in app["cap_drop"], "runtime filesystem/capabilities restricted")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    require("FROM python:3.12-slim" in dockerfile and "USER 10001:10001" in dockerfile, "Python 3.12 non-root image")
    require("requirements-lock.txt" in dockerfile and "COPY . " not in dockerfile, "locked runtime with explicit COPY inputs")
    require("scripts/container_entrypoint.py" in dockerfile and "/health/ready" in dockerfile, "migration entrypoint and readiness")
    require("pytest==" not in (ROOT / "backend/requirements-lock.txt").read_text(encoding="utf-8-sig").lower(), "test runner is absent from runtime lock")
    for path in (".github/workflows/ci.yml", ".github/workflows/deploy.yml"):
        workflow = read(path)
        require(workflow["permissions"] == {"contents": "read"}, path + " has read-only repository token")
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    require(bool(re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", step["uses"])), path + " pins " + step["uses"].split("@")[0])
    ci, deploy = read(".github/workflows/ci.yml"), read(".github/workflows/deploy.yml")
    require(set(ci["on"]) == {"pull_request", "workflow_dispatch"}, "CI runs only for PR/manual triggers")
    require(ci["jobs"]["verify"]["services"]["postgres"]["image"] == "postgres:17-alpine", "CI includes PostgreSQL 17 service")
    require(set(deploy["on"]) == {"workflow_dispatch"}, "deployment is manual only")
    require("self-hosted" in deploy["jobs"]["deploy"]["runs-on"] and "eduproject-deploy" in deploy["jobs"]["deploy"]["runs-on"], "deployment requires a dedicated runner label")
    require(deploy["jobs"]["deploy"]["environment"] == "eduproject-staging", "deployment uses a configured GitHub environment")
    for name in (".gitignore", ".dockerignore"):
        ignored = (ROOT / name).read_text(encoding="utf-8")
        require(".env" in ignored and ".venv" in ignored and "qa" in ignored, name + " excludes local secrets/runtimes/QA")
    return checks


if __name__ == "__main__":
    try:
        completed = check()
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps({"passed": False, "check": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from None
    print(json.dumps({"passed": True, "checks": len(completed), "details": completed}, ensure_ascii=False, indent=2))
