# Real rclone-crypt archive qualification

This runbook qualifies the existing AdvisorAI `ArchiveBackend` boundary. It is
not a second backup system and it does not make a cloud remote authoritative;
local immutable data and ledgers remain the source of truth.

## Required scoped configuration

The operator configures these names in the reviewed local secrets file. The
values must not be pasted into chat, printed, or sourced by a shell:

- `RCLONE_CONFIG` — absolute path to the password-encrypted rclone config;
- `RCLONE_CONFIG_PASS` — the rclone config password;
- `RCLONE_REMOTE_A` / `RCLONE_CRYPT_REMOTE_A` — the raw and crypt aliases for
  provider A;
- `RCLONE_REMOTE_B` / `RCLONE_CRYPT_REMOTE_B` — the raw and crypt aliases for
  provider B.

The current operator aliases are `advisor_raw_a:`, `advisor_archive_a:` and
`advisor_raw_b:`, `advisor_archive_b:`. The aliases are configuration identity,
not credentials. The historical singular `RCLONE_REMOTE` and
`RCLONE_CRYPT_REMOTE` names remain supported for one-provider fixtures, but
cannot close the two-provider gate.

The process receives only the `ARCHIVE_RCLONE` credential scope. The runner
does not call `rclone config show` or `rclone config dump`, does not emit
provider command output, and does not persist the encrypted config or any
password/token.

## Explicit real qualification

After the operator has configured the names above locally, run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./.venv/bin/python \
  scripts/qualify_rclone_archive.py \
  --real \
  --secrets /home/maaro/.config/advisorai-v3/secrets.env \
  --evidence-dir artifacts/phase0/rclone-crypt-qualification
```

The runner generates a fresh harmless payload and exercises, independently for
both providers:

1. raw-layer listing before and after upload;
2. encrypted upload through the crypt alias;
3. crypt restore and SHA-256 comparison;
4. the canonical `ArchiveAutomation` two-provider verification;
5. provider outage, missing-object, wrong-path, interrupted-copy/retry,
   checksum-mismatch, and corrupted-local-restore drills.

Provider command output is classified in memory and discarded. The immutable
run directory contains aliases, operation classifications, counts, source and
restored SHA-256 values, and evidence hashes only. A pass requires:

- both independent uploads and restores pass;
- each raw layer shows a new object without exposing the plaintext filename or
  plaintext key;
- `source SHA == provider A restored SHA == provider B restored SHA`;
- every required recovery drill passes or is explicitly classified as an
  injected/local drill.

The manual operator copy/restore statement is useful context but is not
admission evidence until this controlled path produces a passing immutable run.
An absent or malformed scoped configuration produces
`PENDING_OPERATOR_ACTION` with zero network calls.
