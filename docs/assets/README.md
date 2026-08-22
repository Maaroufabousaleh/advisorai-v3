# Documentation assets

The diagrams in the documentation are Mermaid source embedded in Markdown so GitHub can render and maintain them without a binary generation pipeline.

The PNG assets in this directory are repository-owned visual material:

- `branding/advisorai-logo.png` is a transparent-background derivative of the supplied AdvisorAI mark, retained as a secondary branding asset.
- `branding/advisorai-v3-header.png` is the supplied AdvisorAI V3 banner used as the root README header image.
- `screenshots/dashboard-*-synthetic.png` were captured from the current React dashboard in local development mode with the explicit synthetic fixture. Values in those images are illustrative UI state, not market data, performance, or evidence.

Refresh screenshots after meaningful dashboard layout changes. Keep the `synthetic` label in filenames and surrounding documentation unless a screenshot is captured from a verified ledger-backed projection.
