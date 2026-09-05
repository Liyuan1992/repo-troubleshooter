# Changelog

## Unreleased

- Added `rt prepare PROFILE` for the repeatable first-use flow: initialise the
  local evidence database and run the same bounded, resumable sync as `rt sync`.
- A diagnosis now classifies whether its input is an observed failure, question,
  idea, or unknown before incident retrieval. Unknown reports stop for a
  concrete symptom or an explicit `--report-kind failure` declaration.
- Added repeatable `--anchor KIND:VALUE` CLI constraints and MCP `anchors`.
  Exact error, structural, package, path, and module anchors can reject an
  incompatible candidate. They cannot accept a candidate or authorise advice.
- Clarified confirmation: a digest confirms the displayed interpretation,
  candidate incident, evidence, and proposal. It only permits a recommendation;
  the tool never executes an upgrade or configuration change.
