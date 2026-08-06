---
name: AdvisorAI V3 Control Room
description: A deterministic quant operating console where every paper decision is visible and auditable.
colors:
  graphite-bg: "#080a0e"
  graphite-surface: "#0e1218"
  graphite-raised: "#121820"
  graphite-line: "#252d36"
  instrument-white: "#edf2f4"
  telemetry-cyan: "#6ed9e8"
  approval-green: "#72d3a0"
  warning-amber: "#f2bd67"
  critical-red: "#ff7378"
  muted-blue: "#8aaef4"
typography:
  display:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(2.625rem, 5.1vw, 4.75rem)"
    fontWeight: 630
    lineHeight: 0.94
    letterSpacing: "-0.055em"
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.7
  label:
    fontFamily: "IBM Plex Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "9px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0.12em"
rounded:
  sm: "4px"
  md: "0px"
spacing:
  xs: "5px"
  sm: "9px"
  md: "15px"
  lg: "22px"
  xl: "35px"
components:
  button-primary:
    backgroundColor: "{colors.telemetry-cyan}"
    textColor: "#071014"
    rounded: "{rounded.md}"
    padding: "11px 14px"
  button-secondary:
    backgroundColor: "{colors.graphite-raised}"
    textColor: "{colors.instrument-white}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
  panel:
    backgroundColor: "{colors.graphite-surface}"
    rounded: "{rounded.md}"
    padding: "21px"

# Design System: AdvisorAI V3 Control Room

## Overview

**Creative North Star: “The Split-Flap Concourse.”**

The console behaves like a professional instrument board: fixed columns, deliberate rows, live state changes, and clear cancellation or lock states. The split-flap reference is carried as functional grammar—not nostalgia: mission rows, policy rows, service rows, and audit events keep their columns while values update inside them.

The scene is a single owner-operator at a dim workstation, scanning a dense but calm paper-trading state. Graphite surfaces and precise rules make room for white measurement type; cyan marks telemetry and navigation, green proves a passing control, amber calls for review, and red reserves critical intervention. There is no decorative neon, trading hype, or opaque “AI magic.”

**Key Characteristics:**

- Matte graphite field with hard, quiet row geometry.
- Fixed-column data boards with accessible table semantics underneath.
- Sparse color used as a state language, never decoration.
- Evidence, expiry, lineage, and policy ownership stay adjacent to decisions.

## Colors

The palette is a cool graphite instrument surface with one bright telemetry voice and three explicit safety states.

### Primary

- **Telemetry Cyan** (#6ed9e8): active navigation, data lines, links, focus, and live read-state.

### Secondary

- **Approval Green** (#72d3a0): validated data, clean reconciliation, approved risk, and completed recovery steps.
- **Warning Amber** (#f2bd67): review, stale or incomplete evidence, paper/testnet boundary, and sealed live readiness.
- **Critical Red** (#ff7378): kill switch, loss, rejection, and emergency intervention.

### Neutral

- **Graphite Background** (#080a0e): application field and page gutters.
- **Graphite Surface** (#0e1218): primary panels and tables.
- **Graphite Raised** (#121820): controls and focused surfaces.
- **Graphite Line** (#252d36): panel boundaries and row separators.
- **Instrument White** (#edf2f4): primary data and headings.
- **Telemetry Muted** (#8a959f): secondary copy and labels.

### Named Rules

**The State Has a Color Rule.** Every saturated color must indicate a state, authority, or interaction; do not use accent color to decorate empty space.

## Typography

**Display Font:** Inter (with system UI fallbacks)
**Body Font:** Inter (with system UI fallbacks)
**Label/Mono Font:** IBM Plex Mono (with SFMono-Regular and Consolas fallbacks)

The UI face stays quiet and highly legible. The mono face is reserved for measurements, IDs, timestamps, policy names, statuses, and navigation labels; it is instrumentation, not costume.

### Hierarchy

- **Display** (630, `clamp(2.625rem, 5.1vw, 4.75rem)`, `.94`): the overview thesis only.
- **Headline** (600, `36px`, `1.1`): workspace titles.
- **Title** (550, `17–23px`, `1.2`): panel and modal titles.
- **Body** (400, `12–14px`, `1.5–1.7`): explanations and recovery copy, kept to a readable measure.
- **Label** (500, `9–10px`, `1.2`, tracked uppercase): state rails, table heads, policy IDs, and controls.

### Named Rules

**The Measurement Rule.** Use tabular mono for values that must align or be compared; use the UI face for human explanation.

## Layout

The desktop shell uses a 256px fixed navigation rail, a 57px top state bar, and a 39px environment strip. The workspace is a 36px padded, max-1680px operating field. Panels use a 22px gap; dense rows use 1px separators and 59–61px rhythm.

The first viewport is a two-column thesis: the paper-control narrative and actions on the left, the sealed Phase 10 gate on the right. Metrics then form one ruled strip, followed by equity/risk, the mission board, and three supporting registers. At 980px the thesis and equity/risk sections stack; at 760px the rail becomes an off-canvas menu and data boards retain horizontal overflow rather than collapsing their columns; at 470px metrics and risk summaries become one column.

## Elevation & Depth

Depth is mostly tonal and structural: graphite surfaces, rules, and a soft ambient shadow distinguish operating layers. Shadows are never used as a colored halo or as a substitute for hierarchy.

### Shadow Vocabulary

- **Panel ambient:** `0 18px 40px rgba(0,0,0,.18)` for primary panels.
- **Floating control:** `0 9px 22px rgba(0,0,0,.30)` for quick actions.
- **Modal depth:** `0 24px 80px rgba(0,0,0,.55)` for protected focus.

### Named Rules

**The Flat-by-Default Rule.** Keep resting surfaces flat; reserve shadow for a panel that floats above the operating field or requires protected focus.

## Shapes

The language is squared and instrument-like: 0px panel and control corners, 4px navigation/status corners, 1px borders, rectangular row cells, and small circular signal lamps. Pills are reserved for compact status labels, not used as the page’s primary container shape.

## Components

### Buttons

- **Shape:** square instrument controls (0px), with 1px borders on secondary actions.
- **Primary:** telemetry cyan background, dark ink text, `11px 14px` padding, tracked mono label.
- **Hover / Focus:** lighten the cyan or lift by 1px; keyboard focus uses a 2px cyan outline with 3px offset.
- **Secondary / Ghost:** raised graphite or transparent background with a brightened border on hover.
- **Danger:** red is reserved for halt/rejection and must be confirmed in a protected dialog.

### Chips

- **Style:** compact rectangular status labels with a 1px state-colored border and a 5px signal lamp.
- **State:** the text names the state; color reinforces it but never carries meaning alone.

### Cards / Containers

- **Corner Style:** 0px panel geometry, 1px graphite border.
- **Background:** graphite surface or raised graphite for controls.
- **Shadow Strategy:** reference Elevation & Depth; no colored glows.
- **Internal Padding:** 18–22px for panels; 21px is the standard heading/table inset.

### Inputs / Fields

- **Style:** near-black field, 1px bright graphite border, square corners, mono values.
- **Focus:** cyan border and visible 2px focus ring.
- **Error / Disabled:** red error block with explicit recovery copy; disabled controls reduce opacity and remain non-interactive.

### Navigation

- **Style:** fixed rail with mono labels, small secondary hints, and a 2px cyan active edge.
- **States:** muted at rest, white on hover, cyan icon and tinted field when active.
- **Mobile:** off-canvas rail opened by a labeled menu button; no hidden control is required to monitor or halt paper state.

### Split-Flap Mission Board

The signature component is a semantically accessible table whose visual grammar is fixed rows and columns. Mission state, evidence count, confidence, expiry, and dissent remain visible in the same row; updates should change cell content without reshuffling the board.

## Do's and Don'ts

### Do:

- **Do** place environment, kill state, reconciliation, freshness, and headroom in the persistent state rails.
- **Do** show the evidence, expiry, policy version, and owning service beside decisions.
- **Do** keep paper/testnet and live-locked states explicit in copy and color.
- **Do** preserve table semantics, keyboard focus, reduced motion, and non-color status labels.
- **Do** use the split-flap grammar for dense operational lists.

### Don't:

- **Don't** add a live activation or live-order endpoint to the client command contract.
- **Don't** use saturated color without a state meaning.
- **Don't** let an AI or browser client write ledgers, loosen limits, or bypass RiskKernel/OMS.
- **Don't** use gradients, glow-heavy chrome, or generic metric cards to obscure provenance.
- **Don't** hide stale data, dissent, rejection reasons, or recovery blockers behind a second screen.
