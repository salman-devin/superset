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
"""Drive Devin sessions that remediate maintenance issues.

Invoked from the ``start-devin-remediation`` safe-output job of the
``devin-remediate`` agentic workflow. It reads the agent's structured output
(``$GH_AW_AGENT_OUTPUT``), starts one Devin session per requested issue, polls
the session to completion and writes an observable status report:

* a markdown report on stdout / ``$GITHUB_STEP_SUMMARY``
* ``devin-status.md`` files that the calling job posts as issue comments
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

from devin_api import DevinClient  # noqa: E402

logger = logging.getLogger("devin-self-heal")

TOOL_NAME = "start_devin_remediation"

# Devin returns this so the workflow can render a deterministic status table
# instead of parsing free-form session text.
STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["fixed", "partially_fixed", "not_fixed", "needs_human"],
        },
        "summary": {"type": "string"},
        "pull_request_url": {"type": "string"},
        "verification": {"type": "string"},
        "blockers": {"type": "string"},
    },
    "required": ["outcome", "summary"],
}

PROMPT_TEMPLATE = """You are remediating a maintenance issue in the GitHub \
repository {repo}.

Issue #{issue_number}: {issue_title}
Issue URL: {issue_url}

--- Issue body ---
{issue_body}
--- End issue body ---

Remediation plan produced by the triage agent:
{plan}

Requirements:
1. Work on a new branch off the repository default branch.
2. Make the smallest change that resolves the issue. Do not bundle unrelated
   refactors, and do not modify tests to make them pass.
3. Follow the repository conventions in AGENTS.md. Run `pre-commit run --all-files`
   and fix everything it reports before pushing.
4. Open a pull request that says "Closes #{issue_number}" in the body and explains
   what changed, why, and how you verified it.
5. If the issue cannot be safely fixed automatically (for example an upstream fix
   has not shipped yet, or the change needs a product decision), do NOT force a
   change: stop and report `needs_human` with the reason.
6. Report your result through the structured output schema before finishing.
"""


def read_agent_items(path: str) -> list[dict[str, Any]]:
    """Extract remediation requests from the gh-aw agent output file."""
    if not path or not os.path.exists(path):
        logger.warning("No agent output file at %r", path)
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return [item for item in items if item.get("type") == TOOL_NAME]


def build_prompt(item: dict[str, Any], repo: str) -> str:
    issue_number = item["issue_number"]
    return PROMPT_TEMPLATE.format(
        repo=repo,
        issue_number=issue_number,
        issue_title=item.get("issue_title", ""),
        issue_url=f"https://github.com/{repo}/issues/{issue_number}",
        issue_body=(item.get("issue_body") or "(not provided)")[:8000],
        plan=item.get("plan") or "(none provided — derive one from the issue)",
    )


def render_comment(
    item: dict[str, Any], session: dict[str, Any], session_url: str
) -> str:
    status = session.get("status_enum") or session.get("status") or "unknown"
    structured = session.get("structured_output") or {}
    outcome = structured.get("outcome", "unknown")
    pull_requests = session.get("pull_requests") or []
    pr_links = [
        f"- {pr.get('pr_url')} ({pr.get('pr_state') or 'open'})" for pr in pull_requests
    ]
    if structured.get("pull_request_url") and not pr_links:
        pr_links = [f"- {structured['pull_request_url']}"]

    lines = [
        "### 🤖 Devin self-healing remediation",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Session | {session_url} |",
        f"| Session status | `{status}` |",
        f"| Outcome | `{outcome}` |",
        f"| ACUs consumed | {session.get('acus_consumed', 'n/a')} |",
        "",
    ]
    if summary := structured.get("summary"):
        lines += ["**Summary**", "", summary, ""]
    if verification := structured.get("verification"):
        lines += ["**Verification**", "", verification, ""]
    if blockers := structured.get("blockers"):
        lines += ["**Blockers**", "", blockers, ""]
    if pr_links:
        lines += ["**Pull requests**", "", *pr_links, ""]
    else:
        lines += ["_No pull request was opened by this session._", ""]
    if session.get("timed_out"):
        lines += [
            "> ⏱️ The workflow stopped polling before the session finished. "
            "Follow the session link for live progress.",
            "",
        ]
    lines.append(
        "<sub>Filed automatically by the `devin-remediate` agentic workflow.</sub>"
    )
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-output", default=os.environ.get("GH_AW_AGENT_OUTPUT"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--out-dir", default="devin-remediation")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("DEVIN_SESSION_TIMEOUT", "3600")),
    )
    parser.add_argument(
        "--poll-seconds", type=int, default=int(os.environ.get("DEVIN_POLL", "30"))
    )
    parser.add_argument(
        "--max-acu-limit",
        type=int,
        default=int(os.environ.get("DEVIN_MAX_ACU", "20")),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    items = read_agent_items(args.agent_output)
    if not items:
        logger.info("No %s requests in agent output; nothing to do.", TOOL_NAME)
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = None
    if not args.dry_run:
        client = DevinClient(os.environ["DEVIN_API_KEY"], os.environ["DEVIN_ORG_ID"])

    for item in items:
        issue_number = item["issue_number"]
        prompt = build_prompt(item, args.repo)
        logger.info("Starting Devin session for issue #%s", issue_number)

        if args.dry_run or client is None:
            print(f"--- prompt for issue #{issue_number} ---\n{prompt}")
            continue

        title = f"[self-heal] {args.repo}#{issue_number} {item.get('issue_title', '')}"
        created = client.create_session(
            prompt=prompt,
            title=title,
            tags=["self-heal", f"{args.repo}#{issue_number}"],
            idempotent=True,
            max_acu_limit=args.max_acu_limit,
            structured_output_schema=STRUCTURED_OUTPUT_SCHEMA,
        )
        session_id = created["session_id"]
        session_url = (
            created.get("url") or f"https://app.devin.ai/sessions/{session_id}"
        )
        logger.info("Issue #%s -> %s", issue_number, session_url)

        (out_dir / f"{issue_number}.session").write_text(
            f"{session_id}\n{session_url}\n", encoding="utf-8"
        )

        session = client.wait_for_session(
            session_id,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            on_poll=lambda status, detail, _s, sid=session_id: logger.info(
                "  session %s: %s/%s", sid, status, detail
            ),
        )
        comment = render_comment(item, session, session_url)
        (out_dir / f"{issue_number}.md").write_text(comment, encoding="utf-8")

        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(f"\n## Issue #{issue_number}\n\n{comment}\n")
        print(comment)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
