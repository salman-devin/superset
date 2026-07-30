---
emoji: 🛡️
name: Devin maintenance scan
description: Scan dependencies daily, file deduplicated maintenance issues, and queue them for Devin remediation.
on:
  schedule: daily on weekdays
  workflow_dispatch:
permissions:
  contents: read
  issues: read
  pull-requests: read
strict: true
timeout-minutes: 20
network:
  allowed: [defaults, github]
tools:
  github:
    mode: gh-proxy
    toolsets: [default]
steps:
  - name: Set up Python
    uses: actions/setup-python@v7
    with:
      python-version: "3.11"
  - name: Install scanners
    run: pip install pip-audit==2.10.1
  - name: Run maintenance scan
    run: python .github/scripts/devin_self_heal/scan.py --repo-root .
safe-outputs:
  create-issue:
    max: 5
    labels: [maintenance, automated-scan, devin-remediate]
    # A label applied with the default GITHUB_TOKEN cannot trigger another
    # workflow, so `devin-remediate` would never reach `devin-remediate.md`.
    # SELF_HEAL_PAT (fine-grained, issues: write) makes the chain work; without
    # it the label is still applied, it just has to be re-applied by a human.
    github-token: ${{ secrets.SELF_HEAL_PAT || secrets.GITHUB_TOKEN }}
---

# Devin maintenance scan

A deterministic scan has already run. Its normalised findings are at
`/tmp/gh-aw/maintenance-scan/findings.md` (human readable) and
`/tmp/gh-aw/maintenance-scan/findings.json` (one object per finding, each with a
stable `fingerprint`).

## Task

1. Read `findings.json`.
2. List the maintenance issues already tracked so you never file a duplicate:
   `gh issue list --state all --limit 300 --json number,title,body,state`.
   Regardless of the existing issue's state, a finding is a duplicate when **any**
   of these already appears in an issue title or body:
   - its `fingerprint`;
   - its advisory identifier (`PYSEC-…`, `GHSA-…`, or any of its `aliases`);
   - the affected package name together with the same upgrade
     (for example an open issue titled "Upgrade Flask 2.3.3 → 3.1.3" already
     covers `py:flask:PYSEC-2026-2151`).
   Issues filed by hand predate the fingerprint convention, so never rely on the
   fingerprint alone.
3. Rank the remaining findings by severity, then by whether a fix version exists.
   File at most the top 5 with `create-issue`, one issue per finding.
4. Each issue body must contain, in this order:
   - a `Fingerprint: <fingerprint>` line (this is the deduplication key — never omit it)
   - what the finding is and why it matters for this repository
   - the affected package, installed version, and fix version (or "no fix released")
   - the concrete remediation command, for example
     `uv pip compile pyproject.toml requirements/base.in -o requirements/base.txt --upgrade-package <name>`
     for Python, or an `overrides` entry in `superset-frontend/package.json` for npm
   - a verification step (`pip-audit -r requirements/base.txt`, `npm audit`)
   - `Auto-remediable: yes|no` — `no` when no upstream fix exists, when the bump is
     semver-major across a build-critical dependency, or when the change needs a
     product decision
5. Title format: `[security] <package> <installed> → <fix>` for fixable findings,
   `[security] <package> — <advisory> (no fix released)` otherwise.

Findings labelled `devin-remediate` are picked up by the `devin-remediate`
workflow, which hands them to a Devin session. Only apply that label (it is on by
default for `create-issue`) when `Auto-remediable: yes`; otherwise state clearly in
the body that a human must triage it.

## Safe outputs

- Use `create-issue` for new findings only.
- Never pass a `labels` field to `create-issue`. Labels come from the workflow
  configuration; supplying your own creates malformed literal labels.
- Call `noop` with a one-line explanation when every finding is already tracked or
  the scan produced nothing.
