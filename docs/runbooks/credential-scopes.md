# Scoped credential delivery

`secrets.env` is a master operator inventory, not a process environment. Load
it with `load_env_file` or `CredentialResolver.from_env_file`; never `source`
it. The resolver parses assignments without executing shell syntax and returns
only one explicitly requested scope.

```python
from pathlib import Path

from advisorai.config import CredentialResolver, CredentialScope

resolver = CredentialResolver.from_env_file(Path("/mnt/c/projects/advisorai-v3/secrets.env"))
llm_env = resolver.resolve_for_process(CredentialScope.DIRECT_LLM)
```

The available scopes are `DIRECT_LLM`, `LITELLM`, `OMNIROUTE`, `PUBLIC_DATA`,
`MODEL_REGISTRY`, the connector-specific `PAPER_VENUE*` scopes,
`DERIBIT_PUBLIC`, the connector-specific `ARCHIVE_*` scopes, `EVENT_BUS`,
and `INTERNAL_APP`. Each scope has a reviewed allowlist in
`advisorai.config.secrets.CREDENTIAL_SCOPES`; an unscoped or `all` request is
rejected. Empty assignments are ignored.

The direct OpenRouter adapter continues to use
`ADVISORAI_LLM_API_KEY`. A future LiteLLM process may receive a process-local
alias without duplicating the value in the file:

```python
from advisorai.config import CredentialAlias

litellm_env = resolver.resolve_for_process(
    CredentialScope.LITELLM,
    aliases=(
        CredentialAlias(
            target="OPENROUTER_API_KEY",
            source="ADVISORAI_LLM_API_KEY",
        ),
    ),
)
```

Aliases never modify the source mapping, `os.environ`, or `secrets.env`.
Conflicting target/source values, unknown names, and targets outside the
requested scope fail closed. Diagnostics may use `available_names()`; values
must not be logged.
