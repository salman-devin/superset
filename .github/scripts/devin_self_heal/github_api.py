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
"""Minimal GitHub REST client for the self-healing maintenance automation.

Covers the issue read/write surface the automation needs so the container can
file and update issues without gh-aw safe-outputs or the ``gh`` CLI. Standard
library only, so the runner image needs no extra dependency.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.github.com"
RETRYABLE_STATUSES = {403, 429, 500, 502, 503, 504}


class GitHubAPIError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"GitHub API error {status}: {body[:500]}")
        self.status = status
        self.body = body


class GitHubClient:
    """Issue-scoped GitHub REST client.

    The token needs the classic ``repo`` scope (``public_repo`` suffices for a
    public repository), or a fine-grained token with Issues: read and write.
    """

    def __init__(
        self,
        token: str,
        repo: str,
        api_url: str = DEFAULT_API_URL,
        timeout: int = 30,
        max_retries: int = 4,
    ) -> None:
        if not token:
            raise ValueError("A GitHub token is required")
        if "/" not in repo:
            raise ValueError(f"Expected repo as 'owner/name', got {repo!r}")
        self.token = token
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = json.dumps(payload).encode() if payload is not None else None
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("Authorization", f"Bearer {self.token}")
            request.add_header("Accept", "application/vnd.github+json")
            request.add_header("X-GitHub-Api-Version", "2022-11-28")
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                # A 403 is both "forbidden" and "secondary rate limit"; only the
                # latter is worth retrying and it always names itself.
                retryable = exc.code in RETRYABLE_STATUSES and (
                    exc.code != 403 or "rate limit" in body.lower()
                )
                if not retryable:
                    raise GitHubAPIError(exc.code, body) from exc
                last_error = GitHubAPIError(exc.code, body)
            except urllib.error.URLError as exc:
                last_error = exc
            backoff = 2**attempt
            logger.warning(
                "%s %s failed (%s), retrying in %ss", method, path, last_error, backoff
            )
            time.sleep(backoff)

        raise GitHubAPIError(0, f"exhausted retries: {last_error}")

    def list_issues(
        self,
        *,
        labels: list[str] | None = None,
        state: str = "all",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List issues, newest first, excluding pull requests."""
        issues: list[dict[str, Any]] = []
        per_page = min(100, limit)
        page = 1
        while len(issues) < limit:
            query: dict[str, Any] = {
                "state": state,
                "per_page": per_page,
                "page": page,
            }
            if labels:
                query["labels"] = ",".join(labels)
            batch = self._request("GET", f"/repos/{self.repo}/issues", query=query)
            if not isinstance(batch, list) or not batch:
                break
            issues += [issue for issue in batch if "pull_request" not in issue]
            if len(batch) < per_page:
                break
            page += 1
        return issues[:limit]

    def get_issue(self, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.repo}/issues/{number}")

    def create_issue(
        self, *, title: str, body: str, labels: list[str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self._request("POST", f"/repos/{self.repo}/issues", payload)

    def create_comment(self, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body}
        )

    def remove_label(self, number: int, label: str) -> None:
        """Drop a label, tolerating its absence so reruns stay idempotent."""
        encoded = urllib.parse.quote(label, safe="")
        try:
            self._request(
                "DELETE", f"/repos/{self.repo}/issues/{number}/labels/{encoded}"
            )
        except GitHubAPIError as exc:
            if exc.status != 404:
                raise

    def ensure_labels(self, labels: dict[str, str]) -> None:
        """Create any missing repository labels, mapping name to description."""
        existing = {
            label["name"]
            for label in self._request(
                "GET", f"/repos/{self.repo}/labels", query={"per_page": 100}
            )
        }
        for name, description in labels.items():
            if name in existing:
                continue
            logger.info("Creating label %r", name)
            self._request(
                "POST",
                f"/repos/{self.repo}/labels",
                {"name": name, "description": description, "color": "ededed"},
            )
