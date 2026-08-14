# Dashboard API reference

The optional FastAPI application is a narrow projection and control API for the operator console. It is created by `advisorai.api.dashboard.create_dashboard_app`; OpenAPI docs are disabled in the application factory.

## Endpoint summary

| Method | Path | Auth behavior | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | Unauthenticated | Return `status` and configured environment |
| `GET` | `/api/v1/auth/status` | Unauthenticated | Report whether auth is required/configured/authenticated |
| `POST` | `/api/v1/auth/login` | Unauthenticated | Verify password + TOTP and create a session |
| `POST` | `/api/v1/auth/step-up` | Session + CSRF | Issue a short-lived step-up token |
| `POST` | `/api/v1/auth/logout` | Session if present | Revoke the session cookie |
| `GET` | `/api/v1/dashboard/overview` | Protected when auth is enabled | Return the dashboard overview projection |
| `GET` | `/api/v1/dashboard/services` | Protected when auth is enabled | Return service ownership/state projection |
| `GET` | `/api/v1/dashboard/audit` | Protected when auth is enabled | Return audit projection |
| `GET` | `/api/v1/dashboard/paper-cycles` | Protected when auth is enabled | Return recorded paper runtime cycles |
| `GET` | `/api/v1/dashboard/source-health` | Protected when auth is enabled | Return sanitized source-health projection |
| `POST` | `/api/v1/control/command` | Session + CSRF; step-up for sensitive commands | Record and execute a bounded operator command |

The source of truth for route definitions is [`src/advisorai/api/dashboard.py`](../../src/advisorai/api/dashboard.py). This is not a general trading API: there is no live activation endpoint, arbitrary order endpoint, or raw provider proxy.

## Health example

```bash
curl http://127.0.0.1:8787/api/v1/health
```

```json
{"status":"ok","environment":"paper_testnet"}
```

## Authentication flow

1. `GET /api/v1/auth/status` tells the UI whether protected mode is enabled and whether credentials are configured.
2. `POST /api/v1/auth/login` accepts a password and six-to-eight-character TOTP code. On success it sets an `HttpOnly` `advisorai_session` cookie and returns a CSRF token.
3. The browser sends the CSRF token in `X-CSRF-Token` for protected write requests.
4. Sensitive commands obtain a short-lived step-up token through `POST /api/v1/auth/step-up`; the API consumes it once when the command is submitted.

The implementation uses Argon2id password verification, TOTP, strict session cookies, a small in-process login limiter, CSRF comparison, and security headers. Deployment TLS, secret storage, process isolation, and network policy remain operator responsibilities.

## Control command contract

`POST /api/v1/control/command` accepts a typed request with a `command`, `reason`, `confirmed` flag, unique `idempotency_key`, and optional command-specific fields. The `CommandKind` values are:

```text
halt_paper
resume_paper
set_mode
propose_config
rollback_config
refresh_data
```

`set_mode` requires a requested mode. `propose_config` requires a non-empty string-valued patch. `rollback_config` requires a bundle hash and reason. Sensitive commands require a step-up token in protected mode. Reusing an idempotency key returns the prior receipt; a conflicting request is rejected by the projection/ledger boundary.

Example local-development request shape:

```bash
curl -X POST http://127.0.0.1:8787/api/v1/control/command \
  -H 'Content-Type: application/json' \
  -H 'X-CSRF-Token: local-development-csrf' \
  -d '{
    "command": "refresh_data",
    "reason": "Refresh the local projection",
    "confirmed": true,
    "idempotency_key": "docs-refresh-001"
  }'
```

The exact CSRF value in local development is held by the UI session; the example illustrates the request shape only. Do not reuse this example as a production credential or idempotency key.

## Projection sources

Without `ADVISORAI_DASHBOARD_LEDGER_PATH`, the API can use the explicitly synthetic overview fixture. With a configured ledger path, the projection reads the local event authority and optional configuration/source-health projection paths. Either way, clients must inspect the `synthetic` field before treating values as authoritative.
