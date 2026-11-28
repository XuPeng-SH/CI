# Coverage runner rollout

Set MatrixOne's `MO_COVERAGE_NATIVE` variable to the number of one PR
targeting `main` or `4.2-dev` to run a canary. The other PRs use the pinned
standby workflow. Preflight records the request for failed-jobs reruns.
The canary uses `coverage-ut-native.yaml` and fails if the selected head is
ineligible. Clearing the variable sends fresh runs through standby. Keep the
standby workflows, scripts and checks maintained through the production cutover
and until their outstanding runs have drained.

The canary reads `go.mod` at the authorized PR commit once. It selects the
greater of the `go` and preferred `toolchain` versions and resolves a language
version such as `go 1.26` to its first patch release (`go1.26.0`). Both Coverage
groups and the 4.2 OSS/COS job use that exact toolchain for package listing,
builds and tests. A failed-job rerun uses the original resolved version.

## Runner setup

Register `matrixone-coverage-x86` on CS and Shanghai in distinct ARC runner
groups. Limit runner access to MatrixOne and the reviewed reusable workflow.
Set `MO_COVERAGE_RUNNER_SITE` in the trusted runner container template to
`mty`, `jed`, `ruh`, `mnl2`, or `shanghai`. Helm derives runner names from
the shared scale-set name. CS templates keep their site and expected PVC node
binding, and supply the actual node through the Kubernetes Downward API.
Shanghai workers must satisfy the restricted UID, disk, sudo and socket checks.

- CS consumes the existing verified read-only toolchain and compile cache on the
  node PVC. Each job gets private incremental writes. Shanghai builds with
  job-local caches and a site-local Go module proxy.
- Each site supplies one HTTP(S) `MO_COVERAGE_GOPROXY`. The proxy owns upstream
  fetch concurrency, cache lifetime and Go checksum-service forwarding. Go
  toolchain misses use the same proxy with built-in verification. Use a
  single site-local `MO_COVERAGE_PIP_INDEX_URL` for jobs with Python UDFs.
- Provide a Go bootstrap with toolchain selection on all three execution paths.
  Preload the Go versions from both branches in PATH or in the runner tool
  cache with its installation marker. Populate the Python 3.12 tool cache,
  CGo build tools and verified ONNX runtime. Publish images through the site's
  registry and measure cold image pulls separately from job downloads.
- Mount only the site's prefix-scoped S3 test fixture at
  `/etc/matrixone-ci/coverage-s3` in the worker template. Provide the narrow
  `COVERAGE_PR_READ_TOKEN` credential with PR and organization membership read
  access. The separate Shanghai release job receives its OSS and COS fixtures.

## Canary evidence

Compare the existing route and the native route with identical PR heads and
package coverage on both branches. Confirm that both native groups ran and
record their assigned sites. Exercise warm and cold capacity, a site
unavailable before assignment, a listener that acknowledges a job whose Pod
cannot start, and a failed-group rerun. Capture queue time and end-to-end
P50/P95, cache hit rates, artifact sizes, proxy hit/miss traffic, per-site
WAN egress and image pulls.

Expansion requires a recovery bound for acknowledged jobs that cannot start,
in addition to matching the existing reliability, latency and bandwidth
budgets. Include the separate OSS and COS profile in the `4.2-dev` results
because those tests require Shanghai.
