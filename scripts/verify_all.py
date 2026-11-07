"""Run backend, frontend and infrastructure checks, without source rewrites."""
import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true", help="Try the isolated Docker PostgreSQL test helper")
    parser.add_argument("--require-postgres", action="store_true")
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    out = ROOT / "qa" / run_id
    out.mkdir(parents=True)
    temp = out / "tmp"
    temp.mkdir()
    env = dict(os.environ, PYTHONUTF8="1", TMP=str(temp), TEMP=str(temp))
    started = datetime.now(UTC)
    checks = []

    def run(name, command, cwd=ROOT, timeout=240):
        try:
            result = subprocess.run(command, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            code, log = result.returncode, result.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, log = 124, type(exc).__name__
        (out / (name + ".log")).write_text(log, encoding="utf-8")
        checks.append({"name": name, "exit_code": code})
        return code

    backend_output = ROOT / "backend" / "qa" / ("combined-" + run_id)
    backend_command = [sys.executable, "-u", "scripts/verify.py", "--output", str(backend_output)]
    if args.postgres:
        backend_command.append("--postgres")
    if args.require_postgres:
        backend_command.append("--require-postgres")
    run("backend", backend_command, ROOT / "backend", timeout=300)
    run("infrastructure-tests", [sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-v"])
    run("yaml-structure", [sys.executable, "scripts/check_structure.py"])
    # --quiet validates interpolation without exposing the generated .env password.
    run("compose-config", ["docker", "compose", "config", "--quiet"], timeout=30)
    for asset in ("app", "api"):
        run("syntax-" + asset, [args.node, "--check", "frontend/assets/" + asset + ".js"])
    files = sorted(str(path.relative_to(ROOT / "frontend")) for path in (ROOT / "frontend/tests").glob("*.test.js"))
    run("frontend-tests", [args.node, "--test", *files], ROOT / "frontend")
    # Import via unittest rather than __main__: no frontend evidence is overwritten.
    run("frontend-http", [sys.executable, "-m", "unittest", "discover", "-s", "frontend/tests", "-p", "http_contract.py", "-v"])
    backend_result = json.loads((backend_output / "result.json").read_text(encoding="utf-8")) if (backend_output / "result.json").exists() else None
    failure = any(check["exit_code"] != 0 and not (check["name"] == "backend" and check["exit_code"] == 2 and backend_result and backend_result["pytest_exit_code"] == 0) for check in checks)
    summary = {
        "started_at_utc": started.isoformat(), "finished_at_utc": datetime.now(UTC).isoformat(),
        "checks": checks, "backend": backend_result,
        "all_commands_succeeded": all(check["exit_code"] == 0 for check in checks),
        "verification_status": "failed" if failure else ("incomplete_external_dependencies" if backend_result and backend_result["counts"]["skipped"] else "passed"),
        "real_telegram_e2e": "pending", "docker_image_build": "not_run_by_combined_helper",
        "deployment": "not_run", "visual_browser_check": "not_run",
        "backend_evidence_directory": str(backend_output.relative_to(ROOT)),
    }
    (out / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_directory": str(out.relative_to(ROOT)), **summary}, ensure_ascii=False, indent=2))
    return 0 if summary["all_commands_succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
