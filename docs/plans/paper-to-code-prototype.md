# Paper-to-code prototype in Hermes Skill Foundry

**Review date:** 2026-11-17. This is an optional Phase 8/9 experiment proposal,
with implementation and admission pending.

## Decision

Use [Paper2Code's PaperCoder](https://github.com/going-doer/Paper2Code/tree/ba9169978043d5799c8d4f4a0963e6b66a24c2e1)
as a modifiable upstream base for an AdvisorAI-owned component. The reviewed
revision describes a three-stage workflow (planning, implementation analysis,
code generation) with LaTeX and PDF-derived JSON inputs. The
[paper](https://proceedings.iclr.cc/paper_files/paper/2026/hash/41b6674c28a9b93ec8d22a53ca25bc3b-Abstract-Conference.html)
evaluates ML code generation. Quantitative point-in-time correctness needs a
separate AdvisorAI benchmark.

Adapt only the parts needed to build one factor, estimator, or deterministic
research component. Retain the useful dependency-aware planning and source
analysis. Reuse the approved paper parser and ModelGateway where possible.
Leave the upstream full-repository workflow, direct model configuration and
automatic evaluation outside the integration. Hermes remains the Builder
entry point and the Alpha Team remains the owner of the experiment.

At import, pin the exact adapted files, retain the upstream copyright and
[Apache 2.0 license](https://github.com/going-doer/Paper2Code/blob/ba9169978043d5799c8d4f4a0963e6b66a24c2e1/LICENSE),
preserve required notices if present, mark modified files, and update
[third-party notices](../../THIRD_PARTY_NOTICES.md) and the
[compliance inventory](../../configs/compliance/third-party-licenses.yaml).
Review paper, supplementary code, model and data rights separately. Record
which files were adapted and how to remove the component.

## Proposed workflow

1. **Find the original implementation.** Check the paper, supplements,
   author-linked repositories and release artifacts. Record candidate commits,
   reproducibility and license. Prefer usable author code with appropriate
   rights. If the implementation is missing, incomplete or unsuitable, record
   why and scope the missing method before starting a Builder job.
2. **Plan from the paper.** Read a permitted, versioned input. Emit an equation
   and section map, interface, units, target definition, publication lags,
   corporate-action semantics, initialization/lookbacks, decision and
   rebalancing schedule, and unresolved assumptions. Ask the research reviewer
   to resolve material ambiguities before coding. Treat paper text and
   retrieved material as untrusted input.
3. **Generate one candidate.** Target existing AdvisorAI ports and a small
   fixture. Pin prompts, model/route, dependencies, code, inputs and environment.
   Enforce the job's token, CPU/RAM, wall-time, and cost budgets. Model
   calls go through the approved route. No credentials are passed into the
   generated-code environment.
4. **Separate generation and execution.** Export a quarantined candidate
   through the existing capability boundary. Inspect code and dependencies
   before execution. Run generated native code only behind the backend
   qualified for its trust class by [ADR 0012](../decisions/0012-agent-permission-and-sandbox-boundary.md).
   Provide read-only fixture inputs, deny outbound network in validation,
   keep a bounded export directory, and exclude broker credentials and
   production writes.
5. **Verify and register.** Compare against hand-calculated vectors or a
   reviewed implementation written independently. Test late observations,
   split timing and forward-label leakage where applicable. Record paper
   version/hash, provenance per material choice, candidate/config/snapshot
   hashes, tests, failures, and reviewer verdict in the existing capability
   and `ExperimentArtifact` trail. Replay qualifying strategy candidates
   through the existing canonical validation process.

## First experiment and decision rule

Select one equity-factor paper with a reproducible published implementation.
Keep the reference code and outputs out of both generators' view. Give a
normal Hermes Builder attempt and the adapted Paper2Code workflow identical
paper material, fixtures, and budgets. Compare each against the reference
on fixed vectors and at least one adversarial as-of case. Predeclare numeric
tolerances, mandatory leakage and sandbox checks, and what counts as a
meaningful improvement in review time or correctness.

Measure accepted output correctness, unsupported assumptions, reviewer
minutes, failed runs, tokens/cost, and time to reproducible output. Continue
only if the adaptation passes all mandatory checks and provides a measured
advantage over Hermes alone. A result on one paper informs whether to expand
the experiment. Promotion still requires the existing lifecycle and role
separation. This is outside the current paper-release acceptance criteria.
