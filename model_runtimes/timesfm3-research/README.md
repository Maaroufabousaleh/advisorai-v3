# TimesFM 3 research runtime

This is an optional environment for the isolated `google/timesfm-3.0-pytorch`
challenger. It is not part of AdvisorAI's core dependency graph or `uv.lock`.

Install it only for an explicitly approved research qualification job. The
adapter requires a local checkpoint path by default; it does not download model
weights during AdvisorAI startup, import, or unit tests. Any checkpoint
acquisition must be an explicit operator action with `allow_model_download` set
in the research-only runtime config.

The runtime is scoped to one GPU family at a time and must be closed after the
job. Its outputs are evidence artifacts only: they cannot submit orders, alter
risk, access credentials, enter the V3-Core roster, or self-promote.
