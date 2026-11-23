# Instructions for AI agents

## Workstation delivery

- Use [I1–I9](docs/plans/native-first-integration-roadmap.md) for implementation order and acceptance. Start with a runnable NautilusTrader replay, then build real PydanticAI agent execution on the existing Evidence Council boundary. Use [PAPER-V1](AdvisorAI_Paper_Readiness_Tracker.md) for first-release crypto admission. The full workstation also includes U.S. stocks, ETFs and listed options.
- For a selected upstream tool, inspect the locked version and existing integration, then invoke the upstream engine or library through its supported interface. Reuse AdvisorAI's data, account, RiskKernel, OMS and reconciliation owners. Use test doubles for fault coverage and the simulator for labeled rehearsals. A new stand-in engine, a successful import, or a fixture alone cannot satisfy a native milestone.
- Ship a runnable operator path with each milestone. Record the exact command, upstream invocation, input and revision pins, expected and actual results, and remaining blockers. If a native route is missing or blocked, record the failing step and keep working on the runnable portion. Replacing a selected engine or changing scope requires a reviewed roadmap decision.
- Keep strategy, venue, data and account approval separate from integration. Research and dashboard code have no direct venue write authority. Approved paper execution must route through the deterministic controls and preserve the evidence-to-fill trace. Require a real fake-funds venue fill, exact reconciliation and restart without duplicate orders before claiming paper qualification.

## Required security review before any push

Before running `git push`, review the exact commits and files that will reach
the destination branch. A clean working tree or a passing test suite does not
replace this review.

1. Identify the push destination and outgoing commit range. Review the complete
   commit diffs, staged files, and any untracked files intended for the push;
   confirm the range contains only the requested work.
2. Run an available secret scanner against the outgoing history and files. Scan
   for API keys, provider tokens, passwords, private keys, authenticated URLs,
   cookies, connection strings, `.env` contents, and other credentials. Also
   check that local secret files and runtime state are not tracked or staged.
   If no scanner is available, perform and record an explicit manual review;
   never silently skip the check.
3. Do not print or copy secret values into tool output, logs, commits, or
   reports. When documenting a finding, give its path and line or commit, plus
   the credential type, with the value redacted.
4. Review security-sensitive code and configuration changes, including
   authentication and authorization, input validation, file and process
   boundaries, network access, permissions, logging, and dependency changes.
   Run the applicable security checks and focused tests for those changes.
5. Fix confirmed vulnerabilities before pushing. If a credential appears in
   any outgoing commit, remove it from the entire outgoing history, not only
   the latest file state, and arrange to revoke or rotate it. Do not push while
   a suspected credential leak remains unresolved. Removing a credential from
   Git does not invalidate it. Do not rewrite already-published history without
   explicit authorization from the repository owner.
6. Recheck the final outgoing diff and repository status after fixes. Push only
   the reviewed branch and commit range; do not force-push as a shortcut.

Avoid broad staging commands when local secrets, generated files, or runtime
evidence may be present. Stage reviewed paths explicitly.
