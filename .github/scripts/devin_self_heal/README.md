<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Devin self-healing maintenance

Two [GitHub Agentic Workflows](https://github.com/github/gh-aw) that turn dependency
scan results into merged-ready fixes without a human in the loop for the routine
cases.

```
schedule (weekdays)                    label: devin-remediate
        │                                        │
        ▼                                        ▼
devin-maintenance-scan.md               devin-remediate.md
 ├─ steps: pip-audit + npm audit         ├─ agent reads the issue
 │   → scan.py → findings.json           ├─ decides auto-remediable or noop
 ├─ agent dedups by `Fingerprint:`       └─ calls start_devin_remediation
 └─ safe-output: create-issue                     │
             (labels: devin-remediate)            ▼
                                          safe-output job
                                           ├─ POST /v3/organizations/{org}/sessions
                                           ├─ poll GET  …/sessions/{id}
                                           └─ gh issue comment: session URL,
                                              status, outcome, PR links
```

## Files

| Path | Role |
|---|---|
| `.github/workflows/devin-maintenance-scan.md` | Scheduled scanner + issue filer |
| `.github/workflows/devin-remediate.md` | Label-triggered remediation dispatcher |
| `scan.py` | Normalises `pip-audit` / `npm audit` into fingerprinted findings |
| `devin_api.py` | Stdlib-only Devin API client (create / get / message / poll) |
| `run_remediation.py` | Starts a session per issue, polls it, renders the status comment |

## Triggers

- **Scheduled**: `schedule: daily on weekdays` (plus `workflow_dispatch`) runs the
  scan and files at most 5 new issues per run.
- **Event**: adding the `devin-remediate` label to an issue starts a Devin session
  for it. gh-aw removes the label on activation, so a run cannot loop.
- **Manual**: `workflow_dispatch` with an `issue_number` input replays a single issue.

## Observable outputs

- GitHub issues, one per finding, carrying a stable `Fingerprint:` dedup key.
- An issue comment per remediation run with the Devin session URL, session status,
  the session's structured `outcome`, ACUs consumed, and any pull request links.
- The same report in the Actions job summary.
- Pull requests opened by the Devin session itself, each closing its issue.

## Setup

1. Repository secret `DEVIN_API_KEY` — a Devin service-user API key (`cog_…`) with
   permission to create sessions, and repository variable `DEVIN_ORG_ID`
   (`org-…`). The client uses the v3 organization-scoped endpoints; v1 keys
   without an org scope return `403 Unauthorized`.
2. Repository labels `maintenance`, `automated-scan`, `devin-remediate`.
3. An engine credential for the gh-aw agent job (Copilot by default; see
   [Quick Start](https://github.github.com/gh-aw/setup/quick-start/)).
4. Recompile after editing frontmatter: `gh aw compile devin-remediate`.

## Guardrails

- The agent jobs are read-only. Every GitHub write goes through `safe-outputs:`.
- `DEVIN_API_KEY` is only exposed to the `start-devin-remediation` safe-output job,
  never to the agent sandbox.
- Sessions are capped with `max_acu_limit` (`DEVIN_MAX_ACU`, default 20) and polled
  for at most `DEVIN_SESSION_TIMEOUT` seconds.
- The remediation agent must `noop` for findings marked `Auto-remediable: no`, for
  advisories with no released fix, and for issues that already have an open PR.
- Devin returns a validated structured output (`fixed`, `partially_fixed`,
  `not_fixed`, `needs_human`), so the status comment is deterministic.

## Local development

```bash
python .github/scripts/devin_self_heal/scan.py --repo-root .

GH_AW_AGENT_OUTPUT=/tmp/agent_out.json GITHUB_REPOSITORY=owner/repo \
  python .github/scripts/devin_self_heal/run_remediation.py --dry-run
```

`--dry-run` prints the prompt each Devin session would receive without calling the
API.
