#!/usr/bin/env bash
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
set -euo pipefail

SCRIPTS=/opt/devin_self_heal
STATE=${SELF_HEAL_STATE_DIR:-/tmp/gh-aw/maintenance-scan}

require() {
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "$name must be provided at runtime" >&2
      exit 2
    fi
  done
}

case "${1:-all}" in
  scan)
    # Deterministic discovery: writes findings.json / findings.md.
    shift || true
    exec python "$SCRIPTS/scan.py" --out-dir "$STATE" "$@"
    ;;
  file-issues)
    # Dedups findings against existing issues and files the new ones,
    # labelled for remediation. Needs GITHUB_TOKEN and GITHUB_REPOSITORY.
    shift || true
    require GITHUB_TOKEN GITHUB_REPOSITORY
    exec python "$SCRIPTS/file_issues.py" \
      --findings "$STATE/findings.json" --out "$STATE/filed.json" "$@"
    ;;
  remediate)
    # Session driver. Reads issues from the GitHub API (--issue/--from-label)
    # or from a gh-aw agent output file ($GH_AW_AGENT_OUTPUT).
    shift || true
    require DEVIN_API_KEY DEVIN_ORG_ID
    exec python "$SCRIPTS/run_remediation.py" "$@"
    ;;
  all)
    # The whole loop, gh-aw free: scan, file issues, remediate the queue.
    shift || true
    require GITHUB_TOKEN GITHUB_REPOSITORY DEVIN_API_KEY DEVIN_ORG_ID
    python "$SCRIPTS/scan.py" --out-dir "$STATE"
    python "$SCRIPTS/file_issues.py" \
      --findings "$STATE/findings.json" --out "$STATE/filed.json"
    exec python "$SCRIPTS/run_remediation.py" \
      --from-label "${SELF_HEAL_QUEUE_LABEL:-devin-remediate}" "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
