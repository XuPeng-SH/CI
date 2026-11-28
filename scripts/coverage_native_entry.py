"""Coverage canary admission and toolchain selection for an immutable PR head."""
import os
from pathlib import Path
import subprocess
from urllib.request import Request, build_opener

from coverage_pr_entry import NoRedirect, RELEASE_BASE, eligible_base, has_s3_fixture, live_identity, read_live_pr, require, route


def resolve_go_toolchain(source, sha, token):
    request = Request(f"https://api.github.com/repos/{source}/contents/go.mod?ref={sha}",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"})
    limit = 512 * 1024
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        raw = response.read(limit + 1)
        length = response.getheader("Content-Length")
    require(len(raw) <= limit and (length is None or len(raw) == int(length)), "Incomplete or oversized go.mod")
    helper = Path(__file__).with_name("coverage_go_toolchain.go")
    env = dict(os.environ, GO111MODULE="off", GOTOOLCHAIN="local", GOWORK="off", GOPROXY="off")
    return subprocess.check_output(["go", "run", str(helper)], input=raw, env=env, timeout=60).decode().strip()


def main():
    import node_cache_real_pr_gate as approved  # immutable reviewed helper checkout
    ctx, event, pr, token = read_live_pr()
    expected = (os.environ.get("COVERAGE_EXPECTED_SOURCE"), os.environ.get("COVERAGE_EXPECTED_HEAD"))
    verify_snapshot = expected != (None, None)
    enable_release = os.environ.get("COVERAGE_ENABLE_4_2_DEV") == "true"
    if verify_snapshot:
        base, source, sha = live_identity(ctx, event, pr)
        require((source, sha) == expected and eligible_base(base, enable_release)
                and not pr["head"]["repo"]["archived"], "PR no longer matches authorization")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        require(attempt.isascii() and attempt.isdecimal() and int(attempt) > 0, "invalid run attempt")
        if attempt == "1":
            # needs provided an authorized snapshot in this attempt. Recheck
            # the live PR after queueing without repeating membership and fixture reads.
            return

    status, _ = approved.membership(pr["user"]["login"], os.environ["CS_CACHE_READ_TOKEN"], token)
    base = pr["base"]["ref"]

    def verify_fixture(source, sha):
        if verify_snapshot and base == "main":
            return True  # Reuse the verified fixture for this immutable head.
        if base == RELEASE_BASE:
            return has_s3_fixture(source, sha, token, base=base)
        return has_s3_fixture(source, sha, token)

    result = route(ctx, event, pr, status, verify_fixture, enable_4_2_dev=enable_release)
    require(result["allowed"] == "true", "PR is not eligible for native Coverage")
    if verify_snapshot:
        return
    result.pop("route")
    result["go_toolchain"] = resolve_go_toolchain(result["head_repository"], result["head_sha"], token)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as f:
        for key, value in result.items():
            print(f"{key}={value}", file=f)


if __name__ == "__main__":
    main()
