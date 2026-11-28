# Coverage runner rollout

MatrixOne's `MO_COVERAGE_NATIVE` variable selects the route for a full CI run on
`main` or `4.2-dev`. With the variable unset, the existing pinned standby workflow
runs. Set it to one PR number for a canary or to `shared` for both branches.
Preflight records the decision. A failed-jobs rerun reuses that decision.
Keep the standby worker and completion cleanup until old runs have drained.

## Runner setup

Register the same `matrixone-coverage-x86` scale-set name on the CS and Shanghai
ARC installations in distinct runner groups. Limit runner access to MatrixOne and
the reviewed reusable workflow. Keep the approved per-site runner names. CS
workers must retain their verified node binding. Shanghai workers must satisfy
the restricted UID, disk, sudo and socket checks. ARC assignment behavior and
capacity must be verified at both sites before enabling the shared route.

- CS consumes the existing verified read-only toolchain and compile cache on the
  node PVC. Each job gets private incremental writes. Shanghai builds with
  job-local caches and a site-local Go module proxy.
- Each site supplies one HTTP(S) `MO_COVERAGE_GOPROXY`. The proxy owns upstream
  fetch concurrency, cache lifetime and Go checksum-service forwarding. Use a
  single site-local `MO_COVERAGE_PIP_INDEX_URL` for jobs with Python UDFs.
- Preload the Go versions from both branches and the matching Python 3.12 tool
  cache, CGo build tools and verified ONNX runtime. Publish images through the
  site's registry and measure cold image pulls separately from job downloads.
- Mount only the site's prefix-scoped S3 test fixture at
  `/etc/matrixone-ci/coverage-s3` in the worker template. Provide the narrow
  `COVERAGE_PR_READ_TOKEN` credential with PR and organization membership read
  access. The separate Shanghai release job receives its OSS and COS fixtures.

## Canary evidence

Compare the existing route and the native route with identical PR heads and
package coverage on both branches. Exercise warm and cold capacity, one site
unavailable before assignment, one site unable to start an assigned job, and a
failed-group rerun. Capture queue time and end-to-end P50/P95, cache hit rates,
profile and diagnostic artifact bytes, proxy hit/miss traffic, per-site WAN
egress and runner image pulls. Validate S3 scope and the release provider tests.
Expand after the native route preserves coverage and reliability within the
existing latency and bandwidth budgets.
