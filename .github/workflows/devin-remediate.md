---
emoji: 🤖
name: Devin remediate
description: Hand a labelled maintenance issue to a Devin session that fixes it and opens a pull request.
on:
  label_command:
    name: devin-remediate
    events: [issues]
  workflow_dispatch:
    inputs:
      issue_number:
        description: Issue number to remediate
        required: false
        type: string
permissions:
  contents: read
  issues: read
  pull-requests: read
strict: true
timeout-minutes: 15
network:
  allowed: [defaults, github]
tools:
  github:
    mode: gh-proxy
    toolsets: [default]
safe-outputs:
  add-comment:
    max: 1
  jobs:
    start-devin-remediation:
      description: >-
        Start a Devin session that fixes the issue and opens a pull request, wait
        for it to finish, and post the outcome back on the issue.
      runs-on: ubuntu-latest
      permissions:
        contents: read
        issues: write
      inputs:
        issue_number:
          description: The issue number being remediated
          required: true
          type: number
        issue_title:
          description: The issue title
          required: true
          type: string
        issue_body:
          description: The full issue body, so Devin has the remediation details
          required: true
          type: string
        plan:
          description: Concrete remediation plan for Devin to follow
          required: true
          type: string
      env:
        DEVIN_API_KEY: ${{ secrets.DEVIN_API_KEY }}
        DEVIN_MAX_ACU: "20"
        DEVIN_SESSION_TIMEOUT: "4200"
      output: "Devin remediation session finished; the outcome was posted on the issue."
      steps:
        - uses: actions/checkout@v5
        - uses: actions/setup-python@v7
          with:
            python-version: "3.11"
        - name: Start and follow the Devin session
          run: |
            python .github/scripts/devin_self_heal/run_remediation.py \
              --out-dir "$RUNNER_TEMP/devin-remediation"
        - name: Post the outcome on the issue
          env:
            GH_TOKEN: ${{ github.token }}
          run: |
            shopt -s nullglob
            for report in "$RUNNER_TEMP"/devin-remediation/*.md; do
              issue="$(basename "$report" .md)"
              gh issue comment "$issue" --body-file "$report"
            done
---

# Devin remediate

An issue was labelled `devin-remediate` (or this workflow was dispatched with an
`issue_number`). Hand that issue to a Devin session so it is fixed autonomously.

## Task

1. Read the issue:
   `gh issue view ${{ github.event.issue.number || inputs.issue_number }} --json number,title,body,labels,state`.
2. Decide whether it is safely auto-remediable. Call `noop` with a short reason,
   and do **not** start a session, when any of these hold:
   - the body says `Auto-remediable: no`
   - no upstream fix exists yet (the fix is only an unreleased commit)
   - the issue is closed, or a linked pull request already addresses it
     (`gh pr list --search "<issue number>" --state open --json number,title,url`)
   - the change is open-ended enough to need a human product decision
3. Otherwise call `start_devin_remediation` exactly once with:
   - `issue_number`, `issue_title`, `issue_body` copied verbatim from the issue
   - `plan`: a short, concrete, ordered plan derived from the issue — which files to
     change, which command regenerates them, and how to verify the fix. Include the
     repository conventions that matter (`pre-commit run --all-files` must pass;
     lockfiles are regenerated with `uv pip compile`, never hand-edited).
4. The job posts the session URL, status, structured outcome, and any pull request
   links back on the issue, so do not duplicate that in your own comment.
5. Use `add-comment` only when you have something the job cannot report — for
   example why you narrowed the scope of the plan.

## Safe outputs

- `start_devin_remediation` — start and follow the remediation session.
- `add-comment` — optional context for reviewers.
- `noop` — required whenever you decide not to start a session; say why in one line.
