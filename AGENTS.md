# Instructions for AI agents

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
