# AdvisorAI V3 operator console

This is the React/TypeScript operator surface for AdvisorAI V3. It is designed
for a private local/LAN deployment and defaults to a synthetic paper snapshot
when the API is unavailable. Synthetic values are labelled in the interface;
they are not trading evidence.

## Local development

From the repository root, launch both services with one command:

```bash
./scripts/launch_dashboard.sh
```

The launcher installs frontend dependencies on first run, starts the typed API,
waits for its health endpoint, starts Vite, and stops both processes together
when you press Ctrl-C. Open <http://localhost:5173>.

To require configured password + TOTP MFA:

```bash
./scripts/launch_dashboard.sh --protected
```

Vite proxies `/api` to `127.0.0.1:8787`.

## Protected mode

Generate a password hash and TOTP secret without writing them to disk:

```bash
uv run --extra dashboard python scripts/bootstrap_dashboard_auth.py
```

Provide the printed values through a protected service environment, then start
the API without `ADVISORAI_DASHBOARD_DEV_MODE`. Production/LAN deployments must
use TLS, set `ADVISORAI_DASHBOARD_COOKIE_SECURE=1`, and explicitly configure
`ADVISORAI_DASHBOARD_ALLOWED_ORIGINS`.

Set `ADVISORAI_DASHBOARD_LEDGER_PATH` to a reviewed SQLite WAL path to persist
dashboard command receipts into the existing incident ledger; omit it only for
ephemeral UI development.

The API rejects unauthenticated reads in protected mode and binds command writes
to the short-lived session, CSRF token, idempotency key, and step-up boundary.
The command contract contains no live activation or live order operation.
