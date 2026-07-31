<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# eslint-plugin-i18n-strings (internal)

This is an **internal, in-repo ESLint plugin**, not the `eslint-plugin-i18n-strings`
package published on the npm registry. It is consumed only through the local
`file:eslint-rules/eslint-plugin-i18n-strings` reference in
`superset-frontend/package.json` and is never downloaded from the registry.

It provides two Superset-specific rules used by `eslint.config.minimal.js`:

- `i18n-strings/no-template-vars` — disallow variables in `t()`/`tn()` template
  strings, since Flask-Babel is a static translation service.
- `i18n-strings/no-eager-t-in-config` — disallow eager `t()`/`tn()` calls for
  `label`/`description` in module-level config objects.

## Security advisory note

GitHub advisory [GHSA-55h3-fm53-wq99](https://github.com/advisories/GHSA-55h3-fm53-wq99)
flags the **registry** package named `eslint-plugin-i18n-strings` as malware.
That advisory does not apply to this directory: the code here is maintained in
this repository, has no dependencies, no install/postinstall scripts, and no
network, filesystem, or process access. Because the name matches, security
scanners may keep reporting this local dependency; such reports can be treated
as false positives after re-verifying the source in this directory.
