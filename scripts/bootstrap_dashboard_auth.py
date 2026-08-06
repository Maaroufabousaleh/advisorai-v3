"""Print production dashboard authentication material without writing secrets.

Usage:
    uv run --extra dashboard python scripts/bootstrap_dashboard_auth.py

Copy the two printed values into a protected service environment.  The script
never persists credentials to the repository or a shared filesystem.
"""

from __future__ import annotations

from getpass import getpass

from advisorai.api.security import PasswordService, TotpService


def main() -> None:
    password = getpass("New dashboard password (12+ characters): ")
    confirm = getpass("Repeat dashboard password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    password_hash = PasswordService().hash(password)
    totp_secret = TotpService.new_secret()
    print("\nExport these values through your protected service manager:\n")
    print(f"ADVISORAI_DASHBOARD_PASSWORD_HASH={password_hash}")
    print(f"ADVISORAI_DASHBOARD_TOTP_SECRET={totp_secret}")
    print("\nAdd the TOTP secret to an authenticator before closing this terminal.")


if __name__ == "__main__":
    main()
