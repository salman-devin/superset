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

case "${1:-scan}" in
  scan)
    # Deterministic discovery: writes findings.json / findings.md for the
    # triage agent to turn into deduplicated issues.
    shift || true
    exec python "$SCRIPTS/scan.py" "$@"
    ;;
  remediate)
    # Session driver. Requires DEVIN_API_KEY, DEVIN_ORG_ID, GITHUB_REPOSITORY
    # and GH_AW_AGENT_OUTPUT pointing at the triage agent's output.
    shift || true
    : "${DEVIN_API_KEY:?DEVIN_API_KEY must be provided at runtime}"
    : "${DEVIN_ORG_ID:?DEVIN_ORG_ID must be provided at runtime}"
    exec python "$SCRIPTS/run_remediation.py" "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
