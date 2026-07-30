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
#
# Runner image for the self-healing maintenance automation. It packages the
# scanner (pip-audit / npm audit), the issue filer and the Devin session driver,
# so the full scan -> issue -> remediation loop runs from any scheduler with no
# gh-aw and no agent engine.
#
#   docker build -t devin-self-heal .github/scripts/devin_self_heal
#   docker run --rm -e DEVIN_API_KEY -e DEVIN_ORG_ID -e GITHUB_TOKEN \
#     -e GITHUB_REPOSITORY=owner/name -v "$PWD:/repo" devin-self-heal all
#
# Credentials are only ever passed in at runtime; nothing is baked in.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pip-audit==2.10.1

WORKDIR /repo
COPY . /opt/devin_self_heal/

ENTRYPOINT ["/opt/devin_self_heal/entrypoint.sh"]
CMD ["all"]
