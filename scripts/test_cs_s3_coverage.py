"""Coverage runner, storage and release contracts using local fixtures."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest


DIRECTORY = Path(__file__).parents[1] / ".github/workflows"
ENTRY = (DIRECTORY / "coverage-ut.yaml").read_text()
WORKFLOW = (DIRECTORY / "coverage-worker.yaml").read_text()
RELEASE = (DIRECTORY / "coverage-release-storage.yaml").read_text()


def step_script(step, source=None):
    if source is None:
        source = WORKFLOW
        if step == "eligibility":
            source = (DIRECTORY / "coverage-ut-pre-cutover.yaml").read_text()
    marker = re.search(rf"^( +)- id: {step}$", source, re.M)
    block = re.split(rf"\n(?:{marker[1]}- |  [\w-]+:)", source[marker.end():], maxsplit=1)[0]
    return textwrap.dedent(re.split(r"\n +run: \|\n", block, maxsplit=1)[1])


class CSS3CoverageTest(unittest.TestCase):
    def test_normal_pr_trust_gate_preserved(self):
        for member, label, ready, allowed in (("0", "0", "true", False), ("1", "0", "true", True),
                                             ("0", "1", "true", True), ("1", "0", "false", False)):
            with self.subTest(member=member, label=label, ready=ready), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                result = subprocess.run(["bash", "-c", step_script("eligibility")], capture_output=True,
                                        env=dict(PATH=os.environ["PATH"], GITHUB_OUTPUT=str(output),
                                                 IN_ORG=member, SAFE_LABEL=label, SECRETS_READY=ready))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(output.read_text(), f"allowed={str(allowed).lower()}\n")

    def test_native_jobs_run_in_the_parent_with_an_authorized_source(self):
        group = ENTRY.split("\n  ut-coverage-linux-x86:\n")[1].split("\n  release-storage-coverage:\n")[0]
        self.assertIn("group: [0, 1]", group)
        self.assertIn("fail-fast: false", group)
        self.assertIn("uses: ./.github/workflows/coverage-worker.yaml", group)
        self.assertIn("source: ${{ needs.check_coverage_eligibility.outputs.head_repository }}", group)
        self.assertIn("head: ${{ needs.check_coverage_eligibility.outputs.head_sha }}", group)
        self.assertIn("COVERAGE_EXPECTED_SOURCE: ${{ inputs.source }}", WORKFLOW)
        self.assertIn("COVERAGE_EXPECTED_HEAD: ${{ inputs.head }}", WORKFLOW)
        self.assertIn("repository: ${{ inputs.source }}", WORKFLOW)
        self.assertIn("ref: ${{ inputs.head }}", WORKFLOW)
        self.assertLess(WORKFLOW.index("- id: reader"), WORKFLOW.index("- id: verify_pr"))
        self.assertLess(WORKFLOW.index("- id: verify_pr"), WORKFLOW.index("name: Checkout MatrixOne source"))
        self.assertNotRegex(WORKFLOW + ENTRY, r"workflow_dispatch|actions: write|runs/(?=\$|\{)")
        self.assertIn("COVERAGE_UT_PACKAGE_PARALLELISM: '7'", WORKFLOW)
        self.assertIn("runs-on: matrixone-coverage-x86", WORKFLOW)
        self.assertNotIn("environment: ci", WORKFLOW)
        for secret in ("TOKEN_ACTION", "CS_S3_", "S3API", "S3BUCKET"):
            self.assertNotIn(secret, WORKFLOW + group)
        self.assertIn("COVERAGE_PR_READ_TOKEN", group)

    def test_runner_assignment_checks_both_pools_before_checkout(self):
        shared_runner = "matrixone-coverage-x86-abcde-runner-fghij"
        cases = (
            ("jed", {}, "cs"),
            ("mty", {}, "cs"),
            ("mnl2", {}, "cs"),
            ("ruh", {}, "cs"),
            ("shanghai", {}, "idc"),
            ("jed", {"CS_CACHE_ENV": "ruh"}, None),
            ("jed", {"CS_CACHE_EXPECTED_NODE": "mo-standard-1"}, None),
            ("jed", dict(CS_CACHE_NODE="mo-standard-3", CS_CACHE_EXPECTED_NODE="mo-standard-3"), None),
            ("mty", dict(CS_CACHE_NODE="mo-standard-2", CS_CACHE_EXPECTED_NODE="mo-standard-2"), None),
            ("jed", {"TEST_ARCH": "aarch64"}, None),
            ("shanghai", {"TEST_UID": "0"}, None),
            ("shanghai", {"CS_CACHE_ENV": "jed"}, None),
            ("shanghai", {"TEST_DISK": "62914559"}, None),
            ("shanghai", {"TEST_SUDO_STATUS": "0"}, None),
            ("shanghai", {"EXPOSED_FILE": "token"}, None),
            ("shanghai", {"EXPOSED_FILE": "docker.sock"}, None),
            ("shanghai", {"EXPOSED_FILE": "containerd.sock"}, None),
            ("shanghai", {"MO_COVERAGE_GOPROXY": ""}, None),
            ("shanghai", {"MO_COVERAGE_GOPROXY": "https://cache.internal|direct"}, None),
            ("jed", {"MO_COVERAGE_GOPROXY": "https://cache.internal,https://elsewhere"}, None),
            ("jed", {"MO_COVERAGE_GOPROXY": "https://cache.internal\nINJECTED=yes"}, None),
            ("jed", {"MISSING_FIXTURE": "yes"}, None),
            ("", {"ACTUAL_RUNNER": "cs-jed-nc-n0-pr-v1-abc-runner-def"}, None),
            ("unknown", {}, None),
        )
        script = """
uname() { echo "${TEST_ARCH:-x86_64}"; }
id() { echo "${TEST_UID:-1001}"; }
sudo() { return "${TEST_SUDO_STATUS:-1}"; }
df() { printf 'dev 99999999 0 %s 0%% /\\n' "${TEST_DISK:-62914560}"; }
""" + step_script("reader")
        host_paths = ("/var/run/secrets/kubernetes.io/serviceaccount/token",
                      "/var/run/docker.sock", "/run/containerd/containerd.sock")
        for path in host_paths:
            self.assertIn(path, script)
        for site, extra, pool in cases:
            with self.subTest(site=site, extra=extra), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output = root / "output"
                git_env = root / "env"
                fixture_file = root / "coverage-s3"
                if "MISSING_FIXTURE" not in extra:
                    fixture_file.touch()
                isolated = script.replace("/etc/matrixone-ci/coverage-s3", shlex.quote(str(fixture_file)))
                for path in host_paths:
                    fixture = root / Path(path).name
                    isolated = isolated.replace(path, shlex.quote(str(fixture)))
                    if fixture.name == extra.get("EXPOSED_FILE"):
                        if fixture.suffix == ".sock":
                            fixture.symlink_to(root / "missing")
                        else:
                            fixture.touch()
                env = dict(PATH=os.environ["PATH"], ACTUAL_RUNNER=shared_runner, MO_COVERAGE_RUNNER_SITE=site,
                           GITHUB_ENV=str(git_env), MO_COVERAGE_GOPROXY="http://cache.internal:8080",
                           GITHUB_OUTPUT=str(output), GITHUB_WORKSPACE=tmp, GITHUB_STEP_SUMMARY=str(root / "summary"))
                if site in ("jed", "mty", "mnl2", "ruh"):
                    env.update(CS_CACHE_ENV=site, CS_CACHE_NODE="mo-standard-0", CS_CACHE_EXPECTED_NODE="mo-standard-0")
                result = subprocess.run(["bash", "-c", isolated], env={**env, **extra}, capture_output=True)
                self.assertEqual(result.returncode == 0, pool is not None, result.stdout + result.stderr)
                self.assertEqual(output.read_text() if output.exists() else "",
                                 f"pool={pool}\nsite={site}\n" if pool else "")

                self.assertEqual(git_env.read_text() if git_env.exists() else "",
                                 "GOPROXY=http://cache.internal:8080\nGOTOOLCHAIN=local\n" if pool else "")

    @unittest.skipUnless(shutil.which("go") and shutil.which("jq"), "requires Go and jq")
    def test_go_accepts_language_version_and_preferred_toolchain(self):
        script = step_script("go_toolchain")
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(PATH=os.environ["PATH"], GOTOOLCHAIN="local", GOPROXY="off", GOWORK="off")
            installed, goroot = subprocess.check_output(["go", "env", "GOVERSION", "GOROOT"],
                                                        env=env, cwd=tmp, text=True).splitlines()
            language = installed.removeprefix("go").rsplit(".", 1)[0]
        for directive in (f"go {language}", f"go {language}.0\ntoolchain {installed}"):
            with self.subTest(directive=directive), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "go.mod").write_text(f"module example\n{directive}\n")
                output = root / "github-env"
                result = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True,
                                        env=dict(env, GITHUB_ENV=str(output), GITHUB_PATH=str(root / "path"),
                                                 RUNNER_TEMP=tmp))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), f"GOROOT={goroot}\n")

    def test_go_toolchain_prefers_local_copies_and_uses_the_site_proxy_on_a_miss(self):
        script = step_script("go_toolchain")
        for ready, mirror_available in ((True, False), (False, True), (False, False)):
            with self.subTest(ready=ready, mirror_available=mirror_available), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "go.mod").write_text("module example\ngo 1.27.1\n")
                bootstrap = root / "bin"
                bootstrap.mkdir()
                toolcache = root / "installed"
                cached = toolcache / "go/1.27.1/x64"
                downloaded = root / "resolved"
                for tree in (cached, downloaded):
                    (tree / "bin").mkdir(parents=True)
                    go = tree / "bin/go"
                    go.write_text(f'#!/bin/bash\necho {shlex.quote(str(tree))}\n')
                    go.chmod(0o755)
                if ready:
                    cached.with_suffix(".complete").touch()
                (bootstrap / "go").write_text(r"""#!/bin/bash
if [[ "$*" == 'mod edit -json' && "$GOTOOLCHAIN" == local ]]; then
  echo '{"Go":"1.27.1"}'
elif [[ "$*" == 'env GOROOT' && "$GOTOOLCHAIN" == go1.27.1+auto ]]; then
  if command -v go1.27.1 >/dev/null; then
    exec go1.27.1 "$@"
  fi
  if [[ "$GOPROXY" == http://cache.internal &&
        "$GOMODCACHE" == "$RUNNER_TEMP/coverage-go-toolchain" && "$MIRROR_AVAILABLE" == yes ]]; then
    echo "$RUNNER_TEMP/resolved"
    exit 0
  fi
  exit 42
else
  exit 43
fi
""")
                (bootstrap / "go").chmod(0o755)
                output = root / "github-env"
                env = dict(PATH=f'{bootstrap}:{os.environ["PATH"]}', GITHUB_ENV=str(output),
                           GITHUB_PATH=str(root / "github-path"), RUNNER_TEMP=tmp,
                           RUNNER_TOOL_CACHE=str(toolcache), GOTOOLCHAIN="local", GOPROXY="http://cache.internal",
                           MIRROR_AVAILABLE="yes" if mirror_available else "no")
                result = subprocess.run(["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True)
                valid = ready or mirror_available
                selected = cached if ready else downloaded
                self.assertEqual(result.returncode, 0 if valid else 42, result.stderr)
                self.assertEqual(output.read_text() if output.exists() else "",
                                 f"GOROOT={selected}\n" if valid else "")

    def test_storage_fixture_requires_one_line_and_uses_attempt_prefix(self):
        script = step_script("coverage_ut").split('coverage_profile="', 1)[0]
        cases = (
            ("cs", "jed", "name=cs,key-secret=fixture", "1", True),
            ("idc", "shanghai", "name=cs,key-secret=fixture\n", "2", True),
            ("cs", "jed", "", "2", False),
            ("idc", "shanghai", "name=cs\nkey-secret=fixture", "2", False),
        )
        for pool, site, data, attempt, valid in cases:
            with self.subTest(pool=pool, attempt=attempt, valid=valid), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                summary = root / "summary"
                fixture = root / "fixture"
                fixture.write_text(data)
                stub = "echo() { if [[ \"$1\" == ::add-mask::* ]]; then return 0; fi; builtin echo \"$@\"; }\n"
                isolated = script.replace("/etc/matrixone-ci/coverage-s3", shlex.quote(str(fixture)))
                env = dict(PATH=os.environ["PATH"], RUNNER_TEMP=tmp, GITHUB_WORKSPACE=tmp, GITHUB_STEP_SUMMARY=str(summary),
                           GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT=attempt, RUNNER_POOL=pool,
                           RUNNER_SITE=site, COVERAGE_GROUP="0", endpoint="legacy", TEST_S3FS_ALIYUN="legacy",
                           TEST_S3FS_QCLOUD="legacy", CS_NODE_CACHE_STATE="test")
                result = subprocess.run(
                    ["bash", "-c", stub + isolated + '\ntest -z "${endpoint+x}${TEST_S3FS_ALIYUN+x}${TEST_S3FS_QCLOUD+x}"'],
                    env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                self.assertNotIn("key-secret", result.stdout + result.stderr)
                if valid:
                    self.assertIn(f"ci-ut/123-{attempt}/group-0", summary.read_text())

    def test_release_storage_uses_shanghai_and_the_authorized_head(self):
        job = ENTRY.split("\n  release-storage-coverage:\n")[1].split("\n  merge-coverage-groups:\n")[0]
        self.assertIn("uses: ./.github/workflows/coverage-release-storage.yaml", job)
        self.assertIn("runs-on: amd64-mo-shanghai-ut-safe", RELEASE)
        self.assertNotIn("TOKEN_ACTION", RELEASE)
        self.assertNotIn("environment: ci", RELEASE)
        self.assertIn("github.base_ref == '4.2-dev' && inputs.enable_4_2_dev", job)
        self.assertIn("needs.check_coverage_eligibility.outputs.allowed == 'true'", job)
        self.assertIn("head: ${{ needs.check_coverage_eligibility.outputs.head_sha }}", job)
        self.assertIn("ref: ${{ inputs.head }}", RELEASE)
        job = RELEASE
        self.assertLess(job.index("Verify Shanghai runner isolation"), job.index("Checkout authorized MatrixOne head"))
        self.assertLess(job.index("Reauthorize the exact release PR"), job.index("Checkout authorized MatrixOne head"))
        retry = job.split("name: Reauthorize the exact release PR")[1].split("Checkout authorized MatrixOne head")[0]
        self.assertIn('run: PYTHONPATH="$GITHUB_WORKSPACE/reviewed/scripts" python3 coverage-tools/scripts/coverage_pr_entry.py', retry)
        self.assertNotIn("$GITHUB_OUTPUT", retry)
        self.assertIn("profile: ${{ steps.profile.outputs.artifact-id }}", job)
        self.assertIn("artifact-ids: ${{ needs.release-storage-coverage.outputs.profile }}", ENTRY)

    def test_release_provider_coverage_requires_actual_provider_passes(self):
        script = step_script("test_storage_providers", RELEASE)
        package = "github.com/matrixorigin/matrixone/pkg/fileservice"
        passed = [dict(Action="pass", Package=package, Test=name) for name in (
            "TestObjectStorages/oss_eu/storage/aliyun/basic",
            "TestObjectStorages/TencentCOS/qcloud/basic",
            "TestS3FSFromSpecs/oss_eu/storage/streaming_write",
            "TestS3FSFromSpecs/TencentCOS/streaming_write",
        )]
        oss_spec = "name=oss eu/storage,endpoint=https://oss.aliyuncs.invalid,bucket=fixture\r\n"
        cos_spec = "name=TencentCOS,endpoint=https://cos.myqcloud.invalid,bucket=fixture"
        disk_only = [dict(Action="pass", Package=package, Test="TestObjectStorages/test/disk/basic")]
        cos_skipped = passed[:-1] + [dict(passed[-1], Action="skip")]
        wrong_fixture = passed[:-1] + [dict(passed[-1], Test="TestS3FSFromSpecs/test/streaming_write")]
        for name, missing, test_exit, events, expected in (
                ("both providers passed", None, 0, passed, 0),
                ("missing OSS config", "TEST_S3FS_ALIYUN", 0, passed, 1),
                ("missing COS config", "TEST_S3FS_QCLOUD", 0, passed, 1),
                ("Go test failed", None, 7, passed, 7),
                ("disk only", None, 0, disk_only, 1),
                ("only COS passed", None, 0, passed[1::2], 1),
                ("unrelated streaming fixture passed", None, 0, wrong_fixture, 1),
                ("COS streaming skipped", None, 0, cos_skipped, 1)):
            with self.subTest(name=name), \
                 tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "events").write_text("\n".join(json.dumps(event) for event in events))
                go = root / "go"
                go.write_text("#!/bin/bash\n"
                              '[[ "$TEST_S3FS_ALIYUN" == "$OSS_SPEC" && "$TEST_S3FS_QCLOUD" == "$COS_SPEC" ]] || exit 8\n'
                              '[[ -z "${TEST_S3FS_CS+x}${endpoint+x}" ]] || exit 9\n'
                              'printf "%s\\n" "$@" > "$RUNNER_TEMP/args"\n'
                              'cat "$RUNNER_TEMP/events"\n'
                              'exit "$TEST_EXIT"\n')
                go.chmod(0o755)
                env = dict(PATH=f"{root}:{os.environ['PATH']}", RUNNER_TEMP=tmp,
                           TEST_S3FS_ALIYUN=oss_spec, TEST_S3FS_QCLOUD=cos_spec,
                           OSS_SPEC=oss_spec, COS_SPEC=cos_spec, TEST_EXIT=str(test_exit),
                           TEST_S3FS_CS="unexpected", endpoint="unexpected")
                if missing:
                    env[missing] = ""
                result = subprocess.run(["bash", "-c", script], cwd=tmp, env=env, capture_output=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if not missing:
                    args = (root / "args").read_text().splitlines()
                    self.assertEqual(args[-1], "./pkg/fileservice")
                    self.assertIn("-covermode=set", args)
                else:
                    self.assertFalse((root / "args").exists())

    def test_release_merge_requires_and_includes_provider_coverage(self):
        require = step_script("validate_release_storage", ENTRY)
        for status, profile, expected in (
                ("success", "123", True), ("success", "", False),
                ("skipped", "123", False), ("failure", "123", False)):
            with self.subTest(status=status, profile=profile):
                result = subprocess.run(["bash", "-c", require], capture_output=True,
                                        env=dict(PATH=os.environ["PATH"], STORAGE_RESULT=status, STORAGE_PROFILE=profile))
                self.assertEqual(result.returncode == 0, expected)

        download = ENTRY.split("    - name: Download the selected release provider profile\n", 1)[1]
        download = download.split("    - id: merge_profiles\n", 1)[0]
        # The pinned action nests downloads by artifact ID unless flattening is enabled.
        self.assertRegex(download, r"(?m)^        merge-multiple: true$")
        storage_dir = re.search(r"(?m)^        path: (.+)$", download)[1]
        merge = step_script("merge_profiles", ENTRY)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("coverage_group_collect.py", "coverage_two_groups_merge.py"):
                (scripts / name).touch()
            go = root / "go"
            go.write_text("#!/bin/bash\nexit 0\n")
            go.chmod(0o755)
            for folder, name in (("selected/profiles/0", "group0"), ("selected/profiles/1", "group1"),
                                 (storage_dir, "oss-cos")):
                profile = root / folder / "ut-coverage.out"
                profile.parent.mkdir(parents=True)
                profile.write_text(f"mode: set\n{name}:1.1,1.2 1 1\n")
            env = dict(PATH=f"{root}:{os.environ['PATH']}", RUNNER_TEMP=tmp, EXPECTED_HEAD_SHA="a"*40,
                       GROUP_COUNT="2", GITHUB_RUN_ID="42")
            for required in ("false", "true"):
                with self.subTest(required=required):
                    result = subprocess.run(["bash", "-c", merge], cwd=tmp, capture_output=True,
                                            env={**env, "RELEASE_STORAGE_REQUIRED": required})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    contents = (root / "ut-coverage.out").read_text()
                    self.assertEqual(contents.count("mode: set"), 1)
                    self.assertIn("group0:", contents)
                    self.assertIn("group1:", contents)
                    self.assertEqual("oss-cos:" in contents, required == "true")
            (root / storage_dir / "ut-coverage.out").unlink()
            result = subprocess.run(["bash", "-c", merge], cwd=tmp, capture_output=True,
                                    env={**env, "RELEASE_STORAGE_REQUIRED": "true"})
            self.assertNotEqual(result.returncode, 0)

    def test_required_s3_tests_cannot_be_missing_skipped_or_failed(self):
        script = step_script("coverage_ut")
        script = 'if [[ "${COVERAGE_GROUP}" == \'0\' ]]; then' + script.split('if [[ "${COVERAGE_GROUP}" == \'0\' ]]; then', 1)[1].split("phase_start compact_profile", 1)[0]
        names = ("TestObjectStorages/cs-s3/minio/basic", "TestS3FSFromSpecs/cs-s3/streaming_write")
        for action in ("pass", "skip", "fail", "missing"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "report.json"
                events = [dict(Action=action if i else "pass", Test=name,
                               Package="github.com/matrixorigin/matrixone/pkg/fileservice")
                          for i, name in enumerate(names) if not (i and action == "missing")]
                events.append(dict(Action="pass", Package="another/package"))
                report.write_text("\n".join(map(json.dumps, events)))
                result = subprocess.run(["bash", "-c", script], capture_output=True,
                                        env=dict(PATH=os.environ["PATH"], coverage_report=str(report), COVERAGE_GROUP="0"))
                self.assertEqual(result.returncode == 0, action == "pass")

    def test_skip_manifest_uploaded_for_success_and_failure(self):
        block = WORKFLOW.split("- id: skip_manifest\n", 1)[1].split("- id: upload_coverage_report\n", 1)[0]
        self.assertIn("always() && !cancelled()", block)
        self.assertIn("steps.coverage_ut.outcome == 'success' || steps.coverage_ut.outcome == 'failure'", block)
        self.assertIn("steps.skip_manifest.outputs.available == 'true'", block)
        self.assertIn("coverage_skip_manifest.py", block)
        self.assertIn("ut-coverage-skip-manifest.json", block)


if __name__ == "__main__":
    unittest.main()
