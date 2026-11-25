# Platform expansion implementation report

Status: local implementation on `codex/platform-expansion`; not deployed.
Baseline: `1e65147`. Stripe configuration and production rows are unchanged.

## Phase 1 — tested foundation

- Replaced the homepage API response hero with an explicitly illustrative product
  workspace preview. Preserved the cream, ink and lime visual identity. Added the
  requested heading/description, Free/tools CTAs and a consistent tool card grid.
- Added grouped native-disclosure navigation, a working All Tools directory,
  five solution workflow pages and an authenticated workspace overview. Only
  implemented tools are listed; inventory, assets and custom label design are not
  advertised as complete.
- Added account-based product lookup and equivalent-GTIN conversion pages using
  existing services. Internal CSV labels and enrichment now use session wrappers
  and no longer ask authenticated users to paste API keys.
- Added server entitlement snapshots, feature enforcement, expired premium
  read-only permissions and versioned customer policies. Existing accounts retain
  legacy Creator storage; new Creator accounts receive 500 records. Existing
  integration allowances and Stripe billing logic are unchanged.
- Added a public verified-statistics snapshot endpoint and explicit operator
  refresh command. There is no count on the visitor request path. Until a verified
  snapshot is populated, the UI shows an unavailable count rather than inventing
  one. The audit's production COUNT timed out and was not repeated.
- Added configurable frontend API origin for isolated local integration testing.

Validation: 397 API tests passed; Astro reported zero errors/warnings/hints;
production-format static build and SEO checks passed for 193 pages. Mocked local
browser checks passed at 390/768/1280px for homepage, grouped navigation, Escape
dismissal, directory, workspace, solutions and lookup/conversion pages. The
homepage preview was visually inspected. A Windows sandbox EPERM interrupted one
build; rebuilding locally outside the sandbox succeeded.

## Migration

`20261125_0024_workspace_foundation` adds `users.workspace_policy` and
`platform_statistics`. Existing users are backfilled as legacy; the default for
future users is platform_v1. Upgrade/downgrade tests verified that existing user
rows survive. No production migration has been run.

## Next phases

Phase 2 (SKU manager, catalogue extensions, Label Studio), Phase 3 (inventory,
bulk studio, auditing), Phase 4 (assets, landing pages, privacy-safe funnels) and
Phase 5 final acceptance/security/performance review remain in progress/not yet
implemented at this checkpoint. This report will be updated after each phase.

## Deployment boundary and cost

Do not push to main or deploy automatically: Render auto-deploys that branch.
After explicit release approval, back up the database, review migrations, apply
them in staging, deploy the API, populate the verified count via
`python -m app.tools.refresh_statistics --timeout-seconds 120`, then build and
deploy the static website. The refresh uses the selected environment's database;
do not run it against production without approval. No new paid service is needed.
Snapshot reads add negligible query work; the exact refresh is an explicitly
bounded operator operation rather than repeated visitor work.

Modified files can be reviewed with `git diff 1e65147 --stat` and the local
phase commits. Audit details and schema dependencies are recorded in
`PLATFORM_EXPANSION_AUDIT.md`.
