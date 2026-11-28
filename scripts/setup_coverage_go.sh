#!/usr/bin/env bash
set -euo pipefail

toolchain=$1
if ! [[ "$toolchain" =~ ^go[0-9][0-9a-z.]*$ ]]; then
  echo '::error::Coverage requires the resolved Go toolchain'
  exit 1
fi

installed=$(env -u GOROOT GOTOOLCHAIN=local go env GOVERSION 2>/dev/null || true)
cached="${RUNNER_TOOL_CACHE:-}/go/${toolchain#go}/x64"
if [[ "$installed" == "$toolchain" ]]; then
  selected=$(env -u GOROOT GOTOOLCHAIN=local go env GOROOT)
elif [[ -n "${RUNNER_TOOL_CACHE:-}" && -x "$cached/bin/go" && -f "$cached.complete" ]]; then
  selected=$(env -u GOROOT GOTOOLCHAIN=local "$cached/bin/go" env GOROOT)
else
  # Keep verified toolchain modules separate from job-local compilation caches.
  selected=$(env -u GOROOT GOTOOLCHAIN="$toolchain" GOWORK=off \
    GOMODCACHE="$RUNNER_TEMP/coverage-go-toolchain" go env GOROOT)
fi
if ! [[ -x "$selected/bin/go" && "$(env -u GOROOT GOTOOLCHAIN=local "$selected/bin/go" env GOVERSION)" == "$toolchain" ]]; then
  echo "::error::Coverage could not select $toolchain"
  exit 1
fi
printf 'GOROOT=%s\nGOTOOLCHAIN=local\n' "$selected" >> "$GITHUB_ENV"
printf '%s/bin\n' "$selected" >> "$GITHUB_PATH"
echo "Coverage Go toolchain $toolchain"
