# Local rollback and Bronze rebuild evidence

This runbook records the Phase-1 local operational drill. It exercises the
same content-addressed configuration bundles and manifest-managed Bronze lake
used by the application, without network access, credentials, venue access, or
live-capital authority.

## Run the drill

From the repository root:

```bash
uv run python scripts/run_phase1_local_rebuild.py \
  --config-root . \
  --output artifacts/phase1/local-rebuild
```

The command creates a new immutable run directory for every invocation. It
loads the V3-Core configuration files, creates two immutable bundles, activates
both, rolls back to the first bundle with an auditable activation event, and
rebuilds a raw Bronze artifact into a clean lake root. The report requires
matching configuration hashes, manifests, Parquet bytes, and decoded rows. The
parent `latest.json` is only a pointer; prior run directories are preserved.

The report is local Phase-1 evidence, not a paper-venue or 60-day soak gate.
Provider-specific deployment rollback, real testnet operation, archive restore,
and human Phase-10 approval remain separate requirements.

## Evidence interpretation

`passed: true` proves the local rollback and Bronze rebuild drill completed with
zero network calls. A failure must leave the run available for diagnosis and
must not be converted into a passing status by editing the report. Any change to
configuration source files or the rebuild implementation requires a fresh run.
