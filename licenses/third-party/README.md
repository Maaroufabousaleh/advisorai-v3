# Third-party license texts

AdvisorAI does not vendor or bundle the package-managed dependencies in this
source tree, so their upstream license files are intentionally not copied here
as a second, potentially stale package mirror. The source distribution points
to the exact pinned package/model revisions in
`configs/compliance/third-party-licenses.yaml` and preserves the required
notice obligations in `THIRD_PARTY_NOTICES.md`.

When building a wheel, container, executable, or other bundle, the release
process must collect the license texts and notices for every actually included
component, including transitive runtime components. A release may add exact
upstream texts under this directory after verifying the corresponding pinned
revision; it must not substitute a generic license or rewrite upstream
copyright notices.
