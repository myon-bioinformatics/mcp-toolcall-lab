# Runtime trust receipts

Updating FastMCP or an application's `certifi` alone does not establish that a
container rejects the October 2026 eMudhra chains. Rebuild the image, restart the
deployment, and run the check with **that container's application Python**.
Do not substitute a successful check on the developer's laptop or CI host.

```sh
# Offline inventory. Exit 0 means inventory completed, NOT that TLS trust passed.
python scripts/check_runtime_trust.py --metadata-only --output /tmp/inventory.json

# Explicit read-only live check, inside a rebuilt MCP image.
python scripts/check_runtime_trust.py --live --require-fastmcp --output /tmp/runtime-trust.json

# Stdlib-only images: optional absent packages are recorded, not installed.
python scripts/check_runtime_trust.py --live --output /tmp/runtime-trust.json

# The same tests need no pytest, network, or optional client packages.
python -m unittest tests.test_runtime_trust
```

The script can be mounted read-only into an image and executed with its own
Python. Only the optional receipt destination is written. Preserve the JSON
alongside the image ID/digest, source commit, build logs, and deployment identity;
the script cannot independently establish which registry image was deployed.

## Coverage

The receipt inventories Python, OpenSSL, available OS CA/OpenSSL package versions,
pip, uv, certifi, FastMCP, MCP, HTTPX/HTTPX2 and truststore. It records certificate
bundle hashes and TLS/proxy override **names**, never their values or raw error
messages. A container marker is a hint, not an image-provenance attestation.

Live checks cover:

- Python's actual default `ssl` context, including applicable environment overrides.
- Installed certifi and pip-vendored certifi bundles in explicit SSL contexts.
- Installed HTTPX and HTTPX2 default asynchronous clients.
- pip's actual default truststore factory plus `PipSession`, which includes its
  bundled roots as well as system trust. This is not a pip install/download.
- The installed FastMCP 4 Streamable HTTP transport's default MCP HTTP-client
  factory. The returned client and actual selected transport context are inspected;
  HTTPX is not silently substituted for HTTPX2. A changed/uninspectable API fails
  inconclusively rather than inventing a trust-store result.

For each available backend, PyPI and python.org must return verified 2xx HTTPS
responses and the incident site's same-server liveness control must return its
expected marker. Only then are all fourteen published direct/cross-signed and
no-CT test hosts checked. Redirects are not followed. No arbitrary target URLs,
account credentials, downloads to execute, or TLS-verification bypasses are used.

There are at most two backend subprocesses and four concurrent requests in each.
`--timeout` defaults to 10 seconds per I/O operation. Each backend has a hard
subprocess deadline of `5 * timeout + 20` seconds, including stalled DNS or body
reads. A failed positive control leaves each incident target explicitly not run.
A killed or broken backend produces an inconclusive receipt for every target.

## Results and limits

| Status | Exit | Meaning |
| --- | ---: | --- |
| `pass` | 0 | Every applicable live backend passed all controls and rejected every incident chain. |
| `not_checked` | 0 | Metadata only; no live trust claim. |
| `exposed` | 1 | At least one incident endpoint was accepted with verified HTTPS and its expected response marker. |
| `outdated` | 1 | An installed certifi, pip, or uv is below the incident's documented minimum, even if this Python supplies another defense. |
| `inconclusive` | 2 | A timeout, DNS/network problem, failed control, unsupported client, untested setting, or other coverage gap. |

A negative probe passes only with an actual certificate-verification exception
(`CERTIFICATE_VERIFY_FAILED`), including typed causes preserved by the client.
A timeout, generic SSL error, HTTP error, or an exception that merely contains
those words does not count. Expired/not-yet-valid certificates and hostname
mismatches are also inconclusive, not proof of CA distrust.

Important boundaries:

- uv's version is checked, but its own live transport is not exercised. The
  minimums are certifi 2026.10.19, pip 26.2.2 and uv 0.13.4; these are security
  floors, not claims about the latest release.
- A patched Python verifier may block an intermediate even when a bundle still
  contains an affected root. Passing a bundle probe proves rejection by that
  runtime, not physical removal of the root. Review OS package versions too.
- Custom application factories/SSL contexts, pip configuration files and other
  interpreters/virtual environments require their own checks. Relevant untested
  pip/verification overrides prevent an overall pass. `--require-fastmcp` makes
  an absent or unusable FastMCP default client a failure; omit it for stdlib images.
- If a default HTTP client selects an environment proxy, the script reports
  `proxy_route_not_tested` and does not contact that proxy or send proxy
  credentials. A proxy's TLS rejection does not attest to the origin chain.
  The direct SSL backends are explicitly separate, not replacement evidence for
  an untested application's proxy route. No proxy settings are changed.
- `SSLKEYLOGFILE` blocks live clients before context creation, avoiding TLS-secret
  log writes. Do not turn off certificate verification to make a check pass.
- Ordinary upstream availability is required. A restricted runner or incident
  endpoint outage is a red/inconclusive check, never a green security receipt.

Sources, checked 2026-10-24:

- [FastMCP security guidance](https://github.com/PrefectHQ/fastmcp/blob/main/SECURITY.md#certificate-authority-incident-october-2026)
- [Incident response and distribution status](https://emudhra.we-have-a-problem.com/)
- [Published test-host list and liveness marker](https://github.com/galadran/we-have-a-problem.com/blob/main/checker.js)
- [Python security team's package guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)
