"""Migrate first, then replace the process with Uvicorn (no access/query logs)."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    try:
        from alembic import command
        from alembic.config import Config
        from app.config import Settings
        Settings()  # Validate before attempting any migration.
        command.upgrade(Config(str(ROOT / "backend" / "alembic.ini")), "head")
    except Exception:
        # Never print configuration values, SQL arguments or exception bodies.
        print("Startup failed: check application configuration, database availability and migrations.", file=sys.stderr)
        return 1
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
