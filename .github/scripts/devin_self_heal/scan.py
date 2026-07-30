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
"""Deterministic maintenance scan feeding the `devin-maintenance-scan` workflow.

Normalises `pip-audit` and `npm audit` output into a compact findings file so the
agent triages a small, structured document instead of raw scanner noise. Each
finding carries a stable ``fingerprint`` used for issue deduplication.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "info": 4}

# npm workspaces (relative to the repo root) that ship their own lockfile.
NPM_WORKSPACES = (
    "superset-frontend",
    "superset-websocket",
    "superset-embedded-sdk",
    "superset-frontend/cypress-base",
)


def npm_fingerprint(workspace: str, name: str, source: Any) -> str:
    """Build the npm dedup fingerprint for a finding.

    ``superset-frontend`` keeps the historical ``npm:{package}:{source}`` form so
    issues already filed against it are not refiled under a new key. Every other
    workspace carries its path so the same package vulnerable in two workspaces
    stays two distinct findings instead of collapsing into one.
    """
    if workspace == "superset-frontend":
        return f"npm:{name}:{source}"
    return f"npm:{workspace}:{name}:{source}"


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Run a scanner and return its exit code and stdout.

    Scanner stderr is surfaced on the job log so a failed scan is diagnosable
    instead of silently parsing as "no findings".
    """
    process = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if process.stderr.strip():
        print(f"[{cmd[0]}] {process.stderr.strip()}", file=sys.stderr)
    return process.returncode, process.stdout


def scan_python(requirements: Path) -> list[dict[str, Any]]:
    """Run pip-audit against a resolved requirements file."""
    if not requirements.exists():
        return []
    # Editable local packages cannot be resolved by pip-audit in a clean runner.
    resolved = Path("/tmp/gh-aw/pip-audit-input.txt")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        "\n".join(
            line
            for line in requirements.read_text(encoding="utf-8").splitlines()
            if not line.startswith("-e")
        ),
        encoding="utf-8",
    )
    code, stdout = _run(
        [
            "pip-audit",
            "-r",
            str(resolved),
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]
    )
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        print(f"pip-audit produced no parsable JSON (exit {code})", file=sys.stderr)
        return []

    findings = []
    for dependency in report.get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            fixes = vuln.get("fix_versions") or []
            findings.append(
                {
                    "kind": "python-dependency",
                    "fingerprint": f"py:{dependency['name']}:{vuln['id']}",
                    "package": dependency["name"],
                    "version": dependency["version"],
                    "advisory": vuln["id"],
                    "aliases": vuln.get("aliases", []),
                    "fix_versions": fixes,
                    "fixable": bool(fixes),
                    "severity": "high" if fixes else "moderate",
                    "description": (vuln.get("description") or "").strip()[:800],
                }
            )
    return findings


def scan_npm(
    root: Path, workspaces: Iterable[str] = NPM_WORKSPACES
) -> list[dict[str, Any]]:
    """Run npm audit across every workspace that ships a lockfile."""
    findings: list[dict[str, Any]] = []
    for workspace in workspaces:
        findings.extend(scan_npm_workspace(root, workspace))
    return findings


def scan_npm_workspace(root: Path, workspace: str) -> list[dict[str, Any]]:
    """Run npm audit in one workspace and collapse transitive chains.

    Each transitive advisory is folded onto its root package so a single issue
    is filed per vulnerable root instead of one per dependent.
    """
    directory = root / workspace
    if not (directory / "package-lock.json").exists():
        return []
    code, stdout = _run(["npm", "audit", "--json"], cwd=directory)
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        print(f"npm audit produced no parsable JSON (exit {code})", file=sys.stderr)
        return []

    roots: dict[str, dict[str, Any]] = {}
    dependents: dict[str, set[str]] = defaultdict(set)

    for name, entry in report.get("vulnerabilities", {}).items():
        for via in entry.get("via", []):
            if isinstance(via, str):
                dependents[via].add(name)
                continue
            package = via.get("name", name)
            root_finding = roots.setdefault(
                package,
                {
                    "kind": "npm-dependency",
                    "fingerprint": npm_fingerprint(
                        workspace, package, via.get("source")
                    ),
                    "workspace": workspace,
                    "package": package,
                    "advisory": via.get("url", ""),
                    "title": via.get("title", ""),
                    "severity": via.get("severity", entry.get("severity", "moderate")),
                    "vulnerable_range": via.get("range", ""),
                    "dependents": set(),
                },
            )
            root_finding["dependents"].add(name)

    findings = []
    for root_finding in roots.values():
        root_finding["dependents"] = sorted(
            root_finding["dependents"] | dependents.get(root_finding["package"], set())
        )[:20]
        findings.append(root_finding)
    return findings


def sort_key(finding: dict[str, Any]) -> tuple[int, str]:
    return (
        SEVERITY_ORDER.get(finding.get("severity", "moderate"), 9),
        finding["fingerprint"],
    )


def to_markdown(findings: Iterable[dict[str, Any]]) -> str:
    lines = ["# Maintenance scan findings", ""]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        grouped[finding["kind"]].append(finding)
    if not grouped:
        lines.append("No findings.")
    for kind, group in grouped.items():
        lines += [f"## {kind} ({len(group)})", ""]
        for finding in sorted(group, key=sort_key):
            lines.append(
                f"- `{finding['fingerprint']}` — **{finding['package']}** "
                f"({finding.get('version') or finding.get('vulnerable_range', '')}) "
                f"severity=`{finding.get('severity')}` "
                f"fix=`{','.join(finding.get('fix_versions', [])) or 'none'}` "
                f"{finding.get('title') or finding.get('advisory', '')}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out-dir", default="/tmp/gh-aw/maintenance-scan")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    findings = scan_python(root / "requirements" / "base.txt") + scan_npm(root)
    deduplicated: dict[str, dict[str, Any]] = {}
    for finding in findings:
        deduplicated.setdefault(finding["fingerprint"], finding)
    findings = sorted(deduplicated.values(), key=sort_key)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, default=list), encoding="utf-8"
    )
    markdown = to_markdown(findings)
    (out_dir / "findings.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
