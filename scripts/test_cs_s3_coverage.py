"""Coverage runner, storage and release contracts using local fixtures."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


DIRECTORY = Path(__file__).parents[1] / ".github/workflows"
ENTRY = (DIRECTORY / "coverage-ut.yaml").read_text()
WORKFLOW = (DIRECTORY / "coverage-worker.yaml").read_text()
STACKS = ("mty", "mnl2", "jed", "ruh")


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

    def test_runner_assignment_checks_both_pools_before_checkout(self):
        cs = "cs-jed-nc-n0-pr-v1-abc-runner-def"
        idc = "amd64-mo-shanghai-ut-safe-abc-runner-def"
        cases = (
            (cs, "cs", {}, "cs", "jed"), (cs, "shared", {}, "cs", "jed"),
            (cs.replace("jed", "mty"), "shared", {"CS_CACHE_ENV": "mty"}, "cs", "mty"),
            (cs.replace("jed", "mnl2"), "shared", {"CS_CACHE_ENV": "mnl2"}, "cs", "mnl2"),
            (cs.replace("jed", "ruh"), "shared", {"CS_CACHE_ENV": "ruh"}, "cs", "ruh"),
            (idc, "idc", {}, "idc", "shanghai"), (idc, "shared", {}, "idc", "shanghai"),
            (cs, "idc", {}, None, None), (idc, "cs", {}, None, None),
            (cs, "shared", {"CS_CACHE_ENV": "ruh"}, None, None),
            (cs, "shared", {"CS_CACHE_EXPECTED_NODE": "mo-standard-1"}, None, None),
            (cs.replace("jed-nc-n0", "mty-nc-n2"), "shared",
             dict(CS_CACHE_ENV="mty", CS_CACHE_NODE="mo-standard-2", CS_CACHE_EXPECTED_NODE="mo-standard-2"), None, None),
            (idc, "shared", {"TEST_UID": "0"}, None, None),
            (idc, "shared", {"TEST_DISK": "62914559"}, None, None),
            (idc, "shared", {"TEST_SUDO_STATUS": "0"}, None, None),
            ("unknown", "shared", {}, None, None),
        )
        script = """
uname() { echo x86_64; }
id() { echo "${TEST_UID:-1001}"; }
sudo() { return "${TEST_SUDO_STATUS:-1}"; }
df() { printf 'dev 99999999 0 %s 0%% /\\n' "${TEST_DISK:-62914560}"; }
""" + step_script("reader")
        for runner, requested, extra, pool, site in cases:
            with self.subTest(runner=runner, requested=requested, extra=extra), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                env = dict(PATH=os.environ["PATH"], ACTUAL_RUNNER=runner, REQUESTED_POOL=requested,
                           CS_CACHE_ENV="jed", CS_CACHE_NODE="mo-standard-0", CS_CACHE_EXPECTED_NODE="mo-standard-0",
                           GITHUB_OUTPUT=str(output), GITHUB_WORKSPACE=tmp, GITHUB_STEP_SUMMARY=str(Path(tmp) / "summary"))
                result = subprocess.run(["bash", "-c", script], env={**env, **extra}, capture_output=True)
                self.assertEqual(result.returncode == 0, pool is not None, result.stderr)
                self.assertEqual(output.read_text() if output.exists() else "",
                                 f"pool={pool}\nsite={site}\n" if pool else "")

    def test_cs_credentials_are_selected_only_for_the_assigned_site(self):
        value = re.search(r"TEST_S3FS_CS: >-\n\s*\$\{\{(.*?)\}\}", WORKFLOW, re.S)[1]
        for site in (*STACKS, "shanghai"):
            for missing in (False, True):
                with self.subTest(site=site, missing=missing):
                    expr = " ".join(value.split()).replace("steps.reader.outputs.site", repr(site))
                    for stack in STACKS:
                        expr = expr.replace(f"secrets.CS_S3_{stack.upper()}_CONFIG", repr("" if missing and stack == site else stack))
                    self.assertEqual(eval(expr.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}),
                                     site if site in STACKS and not missing else "")
        for field in ("IDC_ENDPOINT", "IDC_REGION", "IDC_BUCKET", "IDC_KEY", "IDC_SECRET"):
            self.assertRegex(WORKFLOW, rf"{field}: \$\{{\{{ steps.reader.outputs.pool == 'idc' && secrets\.")

    def test_storage_fixture_rejects_missing_credentials_before_ut_and_uses_attempt_prefix(self):
        script = step_script("coverage_ut").split('coverage_profile="', 1)[0]
        for pool, site, missing, attempt in (("cs", "jed", False, "1"), ("cs", "jed", True, "2"),
                                              ("idc", "shanghai", False, "2"), ("idc", "shanghai", True, "1")):
            with self.subTest(pool=pool, missing=missing, attempt=attempt), tempfile.TemporaryDirectory() as tmp:
                summary = Path(tmp) / "summary"
                env = dict(PATH=os.environ["PATH"], RUNNER_TEMP=tmp, GITHUB_WORKSPACE=tmp, GITHUB_STEP_SUMMARY=str(summary),
                           GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT=attempt, CS_CACHE_ENV=site, RUNNER_POOL=pool,
                           RUNNER_SITE=site, TEST_S3FS_CS="opaque-test-value" if pool == "cs" and not missing else "",
                           IDC_ENDPOINT="https://fixture.invalid", IDC_REGION="region", IDC_BUCKET="bucket",
                           IDC_KEY="fixture-key", IDC_SECRET="" if missing else "opaque-test-value", COVERAGE_GROUP="0",
                           endpoint="legacy", TEST_S3FS_ALIYUN="legacy", TEST_S3FS_QCLOUD="legacy")
                result = subprocess.run(["bash", "-c", script + '\ntest -z "${endpoint+x}${TEST_S3FS_ALIYUN+x}${TEST_S3FS_QCLOUD+x}"'],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, not missing, result.stderr)
                self.assertNotIn("opaque-test-value", result.stdout + result.stderr)
                if not missing:
                    details = summary.read_text()
                    self.assertIn(f"ci-ut/123-{attempt}/group-0", details)
                    self.assertNotIn("opaque-test-value", details)

    def test_release_storage_uses_shanghai_and_the_authorized_head(self):
        job = ENTRY.split("\n  release-storage-coverage:\n")[1].split("\n  merge-coverage-groups:\n")[0]
        self.assertIn("runs-on: amd64-mo-shanghai-ut-safe", job)
        self.assertIn("github.base_ref == '4.2-dev' && inputs.enable_4_2_dev", job)
        self.assertIn("needs.check_coverage_eligibility.outputs.allowed == 'true'", job)
        self.assertIn("ref: ${{ needs.check_coverage_eligibility.outputs.head_sha }}", job)
        self.assertLess(job.index("Verify Shanghai runner isolation"), job.index("Checkout authorized MatrixOne head"))
        self.assertLess(job.index("Reauthorize the exact release PR"), job.index("Checkout authorized MatrixOne head"))
        retry = job.split("name: Reauthorize the exact release PR")[1].split("Checkout authorized MatrixOne head")[0]
        self.assertIn('run: PYTHONPATH="$GITHUB_WORKSPACE/reviewed/scripts" python3 coverage-tools/scripts/coverage_pr_entry.py', retry)
        self.assertNotIn("$GITHUB_OUTPUT", retry)
        self.assertIn("profile: ${{ steps.profile.outputs.artifact-id }}", job)
        self.assertIn("artifact-ids: ${{ needs.release-storage-coverage.outputs.profile }}", ENTRY)

    def test_release_provider_coverage_requires_actual_provider_passes(self):
        script = step_script("test_storage_providers", ENTRY)
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
