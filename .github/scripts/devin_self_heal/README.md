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

Dependency scan results turned into merge-ready fixes without a human in the loop
for the routine cases. Two interchangeable drivers run the same code:

| Driver | Where the triage lives | Needs |
|---|---|---|
| [GitHub Agentic Workflows](https://github.com/github/gh-aw) (`devin-*.md`) | an agent, prompted | gh-aw + an engine credential |
| Container (`Dockerfile`, `devin-self-heal-docker.yml`) | `file_issues.py`, deterministic | Docker only |

Pick either; both file the same fingerprinted issues and start the same Devin
sessions, so they can also be swapped without losing dedup history.

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
| `.github/workflows/devin-maintenance-scan.md` | Scheduled scanner + issue filer (gh-aw) |
| `.github/workflows/devin-remediate.md` | Label-triggered remediation dispatcher (gh-aw) |
| `.github/workflows/devin-self-heal-docker.yml` | Same loop, running the container (no gh-aw) |
| `scan.py` | Normalises `pip-audit` / `npm audit` into fingerprinted findings |
| `file_issues.py` | Renders issue bodies and plans, dedups by fingerprint, files them |
| `github_api.py` | Stdlib-only GitHub client (issues, comments, labels) |
| `devin_api.py` | Stdlib-only Devin API client (create / get / message / poll) |
| `run_remediation.py` | Starts a session per issue, polls it, renders the status comment |
| `Dockerfile`, `entrypoint.sh`, `docker-compose.yml` | Runner image and its stages |

## Triggers

- **Scheduled**: `schedule: daily on weekdays` (plus `workflow_dispatch`) runs the
  scan and files at most 5 new issues per run.
- **Event**: adding the `devin-remediate` label to an issue starts a Devin session
  for it. gh-aw removes the label on activation, so a run cannot loop.
- **Manual**: `workflow_dispatch` with an `issue_number` input replays a single issue.
- **Container**: `devin-self-heal-docker.yml` mirrors all three, with a `stage` input
  to run one stage at a time.

## Container usage

The image bundles the scanners and both clients, so any scheduler (cron, a
Kubernetes CronJob, the `devin-self-heal-docker.yml` workflow, your laptop) can run
the loop. Stages are entrypoint commands:

| Command | Does | Requires |
|---|---|---|
| `scan` | writes `findings.json` / `findings.md` | nothing |
| `file-issues` | dedups and files issues, labelled for remediation | `GITHUB_TOKEN` |
| `remediate` | starts and polls a Devin session per issue, comments, unlabels | `GITHUB_TOKEN`, `DEVIN_API_KEY`, `DEVIN_ORG_ID` |
| `all` (default) | `scan` → `file-issues` → `remediate --from-label` | all of the above |

```bash
docker build -t devin-self-heal .github/scripts/devin_self_heal

# whole loop
docker run --rm -v "$PWD:/repo" \
  -e GITHUB_REPOSITORY=owner/name -e GITHUB_TOKEN -e DEVIN_API_KEY -e DEVIN_ORG_ID \
  devin-self-heal all

# one issue
docker run --rm -v "$PWD:/repo" \
  -e GITHUB_REPOSITORY=owner/name -e GITHUB_TOKEN -e DEVIN_API_KEY -e DEVIN_ORG_ID \
  devin-self-heal remediate --issue 2

# or through compose, one service per stage
docker compose -f .github/scripts/devin_self_heal/docker-compose.yml run --rm scan
```

`GITHUB_TOKEN` here is a PAT (classic `repo`, or fine-grained with Issues: read and
write). The default `GITHUB_TOKEN` of a workflow also works, except that labels it
applies cannot trigger other workflows — which is why the container path does the
remediation itself in the same run instead of relying on the label event.

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
2. Repository labels `maintenance`, `automated-scan`, `devin-remediate`; the
   container creates any that are missing.
3. gh-aw driver only: an engine credential for the agent job (Copilot by default;
   see [Quick Start](https://github.github.com/gh-aw/setup/quick-start/)), and
   `gh aw compile devin-remediate` after editing frontmatter.
4. Container driver only: Docker, plus `GH_AW_GITHUB_TOKEN` (or any PAT) if you want
   the filed issues to be able to trigger other workflows.

## Guardrails

- The agent jobs are read-only. Every GitHub write goes through `safe-outputs:`.
- The container never writes to the repository: it only files issues, comments and
  removes its own queue label. Code changes are made by the Devin session, in its
  own pull request.
- At most `SELF_HEAL_MAX_ISSUES` (default 5) issues are filed per run, and a
  fingerprint already present in any issue — open or closed — is never refiled.
- Sessions are created with `idempotent: true`, so a re-run of the same issue
  attaches to the existing session instead of starting a second one.
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

# what would be filed, without touching GitHub
python .github/scripts/devin_self_heal/file_issues.py --dry-run

GH_AW_AGENT_OUTPUT=/tmp/agent_out.json GITHUB_REPOSITORY=owner/repo \
  python .github/scripts/devin_self_heal/run_remediation.py --dry-run
```

`--dry-run` prints the issues that would be filed and the prompt each Devin session
would receive, without calling either API.
