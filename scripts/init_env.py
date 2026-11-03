"""Create a development .env once. Never overwrites a file or prints secrets."""
import os
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]


def initialize(directory: Path = ROOT) -> bool:
    target = directory / ".env"
    if target.exists() or target.is_symlink():
        return False
    template = (directory / ".env.example").read_text(encoding="utf-8-sig")
    if not re.search(r"(?m)^APP_ENV=development\s*$", template):
        raise ValueError("Initializer supports only the development template")
    if len(re.findall(r"(?m)^POSTGRES_PASSWORD=.*$", template)) != 1:
        raise ValueError("Development template requires exactly one POSTGRES_PASSWORD")
    if not re.search(r"(?m)^TELEGRAM_CLIENT_ID=\s*$", template) or not re.search(r"(?m)^TELEGRAM_CLIENT_SECRET=\s*$", template):
        raise ValueError("Telegram credentials must remain empty in the template")
    content = re.sub(r"(?m)^POSTGRES_PASSWORD=.*$", "POSTGRES_PASSWORD=" + secrets.token_hex(32), template)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return True


if __name__ == "__main__":
    try:
        created = initialize()
    except (OSError, ValueError):
        raise SystemExit("Development environment could not be initialized; check .env.example and directory permissions.") from None
    print("Development .env created; credentials were not printed." if created else "Existing .env preserved without changes.")
