# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Turn scan findings into deduplicated GitHub issues.

Deterministic replacement for the triage agent of the `devin-maintenance-scan`
agentic workflow: it renders the issue body, the remediation plan and the
`Fingerprint:` dedup key in code, so the whole scan-to-issue path runs from the
runner image with no agent engine and no gh-aw safe-outputs.

A finding is a duplicate when its fingerprint already appears in the body of any
issue, open or closed, so a closed issue is never refiled.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from github_api import GitHubClient  # noqa: E402

logger = logging.getLogger("devin-self-heal.file-issues")

FINGERPRINT_PREFIX = "Fingerprint:"

LABELS = {
    "maintenance": "Routine upkeep tracked by the self-healing automation",
    "automated-scan": "Filed by the scheduled dependency scan",
    "devin-remediate": "Queued for automated remediation by Devin",
}

PYTHON_PLAN = """1. Regenerate the requirements lockfiles with
   `uv pip compile pyproject.toml requirements/base.in -o requirements/base.txt
   --upgrade-package {package}` so `{package}` is >= {fix_version}, and repeat for
   `requirements/development.txt` if it pins the same package.
2. Do not hand-edit the lockfiles.
3. If `requirements/base.in` carries an explicit pin or upper bound that blocks the
   upgrade, read the comment above it: the pin may exist for a reason that still
   holds. In that case stop and report `needs_human`.
4. Run `pre-commit run --all-files` and fix anything it reports.
5. Verify with `pip-audit -r requirements/base.txt` that {advisory} is gone.
6. Open a PR against the default branch closing this issue."""

NPM_PLAN = """1. In `superset-frontend`, upgrade `{package}` out of the vulnerable range
   ({vulnerable_range}), preferring the smallest change that clears the advisory:
   bump the direct dependency in `package.json` when `{package}` is one, otherwise
   update the transitive pin (e.g. `npm update {package}` or an `overrides` entry).
2. Commit the regenerated `package-lock.json`; do not hand-edit it.
3. Run `npm ci` and the test suites touching the affected packages
   ({dependents}).
4. Verify with `npm audit --json` that the advisory no longer appears.
5. Run `pre-commit run --all-files` and fix anything it reports.
6. Open a PR against the default branch closing this issue."""

NO_FIX_PLAN = """No fixed version has shipped for {advisory}. Do not force an upgrade:
confirm whether a fix exists upstream, and if it does not, report `needs_human`
with what you checked."""


def python_issue(finding: dict[str, Any]) -> tuple[str, str]:
    package = finding["package"]
    advisory = finding["advisory"]
    fix_versions = finding.get("fix_versions") or []
    fix_version = fix_versions[0] if fix_versions else ""
    aliases = ", ".join(f"`{alias}`" for alias in finding.get("aliases", [])) or "none"

    version = finding["version"]
    title = (
        f"[Security] Upgrade {package} {version} → {fix_version} ({advisory})"
        if fix_version
        else f"[Security] {package} {version} is affected by {advisory}"
    )

    plan = (
        PYTHON_PLAN.format(package=package, fix_version=fix_version, advisory=advisory)
        if fix_version
        else NO_FIX_PLAN.format(advisory=advisory)
    )

    body = f"""## Summary

`{package}=={finding["version"]}` in `requirements/base.txt` is affected by
**{advisory}**.

## Details

| Field | Value |
| --- | --- |
| Package | `{package}` |
| Installed | `{finding["version"]}` |
| Fixed in | {f"`{fix_version}`" if fix_version else "_no fixed version released_"} |
| Aliases | {aliases} |
| Severity | `{finding.get("severity", "moderate")}` |

{finding.get("description") or "_No advisory description provided._"}

## Remediation plan

{plan}

## Verification

```
pip-audit -r requirements/base.txt
```
"""
    return title, body


def npm_issue(finding: dict[str, Any]) -> tuple[str, str]:
    package = finding["package"]
    dependents = ", ".join(f"`{name}`" for name in finding.get("dependents", []))
    title = (
        f"[Security] {package} is vulnerable in superset-frontend: "
        f"{finding.get('title') or finding['fingerprint']}"
    )
    body = f"""## Summary

`{package}` in `superset-frontend/package-lock.json` is affected by
{finding.get("title") or "an npm advisory"}.

## Details

| Field | Value |
| --- | --- |
| Package | `{package}` |
| Vulnerable range | `{finding.get("vulnerable_range") or "unknown"}` |
| Severity | `{finding.get("severity", "moderate")}` |
| Advisory | {finding.get("advisory") or "n/a"} |
| Reached through | {dependents or "direct dependency"} |

## Remediation plan

{
        NPM_PLAN.format(
            package=package,
            vulnerable_range=finding.get("vulnerable_range") or "unknown",
            dependents=dependents or "the packages importing it",
        )
    }

## Verification

```
cd superset-frontend && npm audit --json
```
"""
    return title, body


def render_issue(finding: dict[str, Any]) -> tuple[str, str]:
    """Render the issue title and body, including the dedup and triage keys."""
    if finding["kind"] == "python-dependency":
        title, body = python_issue(finding)
    else:
        title, body = npm_issue(finding)

    auto_remediable = "yes" if finding.get("fixable", True) else "no"
    footer = (
        "---\n\n"
        f"{FINGERPRINT_PREFIX} `{finding['fingerprint']}`\n"
        f"Auto-remediable: {auto_remediable}\n\n"
        "<sub>Filed automatically by the self-healing maintenance scan.</sub>\n"
    )
    return title, f"{body}\n{footer}"


def known_fingerprints(issues: list[dict[str, Any]]) -> set[str]:
    """Collect the fingerprints already tracked by an issue, open or closed."""
    seen: set[str] = set()
    for issue in issues:
        body = issue.get("body") or ""
        for line in body.splitlines():
            if line.strip().startswith(FINGERPRINT_PREFIX):
                seen.add(line.split(FINGERPRINT_PREFIX, 1)[1].strip().strip("`"))
    return seen


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--findings", default="/tmp/gh-aw/maintenance-scan/findings.json"
    )
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--max-issues",
        type=int,
        default=int(os.environ.get("SELF_HEAL_MAX_ISSUES", "5")),
        help="Cap on issues filed per run, matching the scan workflow.",
    )
    parser.add_argument(
        "--queue-label",
        default=os.environ.get("SELF_HEAL_QUEUE_LABEL", "devin-remediate"),
        help="Label that queues an issue for remediation; empty to skip queueing.",
    )
    parser.add_argument(
        "--out",
        default="/tmp/gh-aw/maintenance-scan/filed.json",
        help="Where to write the filed issue numbers for the remediate step.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    if not findings:
        logger.info("No findings; nothing to file.")
        Path(args.out).write_text("[]", encoding="utf-8")
        return 0

    client = None
    seen: set[str] = set()
    if not args.dry_run:
        client = GitHubClient(os.environ["GITHUB_TOKEN"], args.repo)
        client.ensure_labels(LABELS)
        seen = known_fingerprints(client.list_issues(labels=["automated-scan"]))
        logger.info("%d fingerprint(s) already tracked", len(seen))

    labels = ["maintenance", "automated-scan"]
    if args.queue_label:
        labels.append(args.queue_label)

    filed: list[dict[str, Any]] = []
    for finding in findings:
        if len(filed) >= args.max_issues:
            logger.info("Reached the per-run cap of %d issues", args.max_issues)
            break
        fingerprint = finding["fingerprint"]
        if fingerprint in seen:
            logger.info("Skipping %s: already tracked", fingerprint)
            continue

        title, body = render_issue(finding)
        if args.dry_run or client is None:
            print(f"--- would file: {title}\n{body}")
            filed.append({"fingerprint": fingerprint, "title": title})
            continue

        issue = client.create_issue(title=title, body=body, labels=labels)
        logger.info("Filed #%s for %s", issue["number"], fingerprint)
        filed.append(
            {
                "number": issue["number"],
                "fingerprint": fingerprint,
                "title": title,
                "url": issue.get("html_url", ""),
            }
        )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(filed, indent=2), encoding="utf-8")

    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n## Filed {len(filed)} maintenance issue(s)\n\n")
            for entry in filed:
                handle.write(f"- {entry.get('url') or entry['title']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
