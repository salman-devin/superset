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
"""Minimal client for the Devin API (https://docs.devin.ai/api-reference).

Only the endpoints needed by the self-healing automation are implemented.
Uses the standard library so the workflow needs no pip install step.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.devin.ai"
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class DevinAPIError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Devin API error {status}: {body[:500]}")
        self.status = status
        self.body = body


class DevinClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 60,
        max_retries: int = 4,
    ) -> None:
        if not api_key:
            raise ValueError("A Devin API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("Authorization", f"Bearer {self.api_key}")
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")
                if exc.code not in RETRYABLE_STATUSES:
                    raise DevinAPIError(exc.code, body) from exc
                last_error = DevinAPIError(exc.code, body)
            except urllib.error.URLError as exc:
                last_error = exc
            backoff = 2**attempt
            logger.warning(
                "%s %s failed (%s), retrying in %ss", method, path, last_error, backoff
            )
            time.sleep(backoff)

        raise DevinAPIError(0, f"exhausted retries: {last_error}")

    def create_session(
        self,
        prompt: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        idempotent: bool = True,
        max_acu_limit: int | None = None,
        structured_output_schema: dict[str, Any] | None = None,
        playbook_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"prompt": prompt, "idempotent": idempotent}
        if title:
            payload["title"] = title[:255]
        if tags:
            payload["tags"] = tags
        if max_acu_limit:
            payload["max_acu_limit"] = max_acu_limit
        if structured_output_schema:
            payload["structured_output_schema"] = structured_output_schema
        if playbook_id:
            payload["playbook_id"] = playbook_id
        return self._request("POST", "/v1/sessions", payload)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/session/{session_id}")

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/session/{session_id}/message", {"message": message}
        )

    def wait_for_session(
        self,
        session_id: str,
        *,
        timeout_seconds: int,
        poll_seconds: int = 30,
        on_poll: Any = None,
    ) -> dict[str, Any]:
        """Poll a session until it finishes, blocks on a human, or times out."""
        deadline = time.monotonic() + timeout_seconds
        session: dict[str, Any] = {}
        while time.monotonic() < deadline:
            session = self.get_session(session_id)
            status = session.get("status_enum") or session.get("status")
            detail = session.get("status_detail")
            if on_poll:
                on_poll(status, detail, session)
            if status in {"blocked", "expired", "exit", "error", "finished"}:
                return session
            if status in {"suspended"} or detail in {"finished", "waiting_for_user"}:
                return session
            time.sleep(poll_seconds)
        session["timed_out"] = True
        return session
