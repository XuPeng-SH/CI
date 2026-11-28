import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import coverage_pr_entry

with mock.patch.dict(sys.modules, coverage_pr_entry=coverage_pr_entry):
    from scripts import coverage_native_entry


class CoverageCanaryAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.pr = {
            "number": 1,
            "state": "open",
            "base": {"ref": "main", "repo": {"full_name": coverage_pr_entry.PROJECT}},
            "head": {"sha": self.sha, "repo": {"full_name": "example/matrixone", "archived": False}},
            "user": {"login": "owner"},
        }
        self.event = {"action": "synchronize", "number": 1, "pull_request": copy.deepcopy(self.pr)}

    def invoke(self, *, attempt="1", worker=False, member=204, fixture=True):
        ctx = (coverage_pr_entry.PROJECT, "pull_request_target", coverage_pr_entry.ENTRY)
        client = SimpleNamespace(membership=mock.Mock(return_value=(member, [])))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = dict(GITHUB_RUN_ATTEMPT=attempt, CS_CACHE_READ_TOKEN="fixture-only", GITHUB_OUTPUT=str(output),
                       COVERAGE_ENABLE_4_2_DEV="true")
            if worker:
                env.update(COVERAGE_EXPECTED_SOURCE="example/matrixone", COVERAGE_EXPECTED_HEAD=self.sha)
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.dict(sys.modules, node_cache_real_pr_gate=client), \
                 mock.patch.object(coverage_native_entry, "read_live_pr", return_value=(ctx, self.event, self.pr, "token")), \
                 mock.patch.object(coverage_native_entry, "has_s3_fixture", return_value=fixture), \
                 mock.patch.object(coverage_native_entry, "resolve_go_toolchain", return_value="go1.24.0") as resolve:
                try:
                    coverage_native_entry.main()
                except ValueError:
                    resolve.assert_not_called()
                    self.assertFalse(output.exists())
                    raise
            return output.read_text() if output.exists() else "", resolve, client.membership

    def test_canary_resolves_the_authorized_head_once(self):
        output, resolve, membership = self.invoke()
        self.assertIn(f"head_sha={self.sha}\n", output)
        self.assertIn("go_toolchain=go1.24.0\n", output)
        resolve.assert_called_once_with("example/matrixone", self.sha, "token")
        membership.assert_called_once()

    def test_unavailable_fixture_fails_the_canary_before_toolchain_lookup(self):
        with self.assertRaisesRegex(ValueError, "not eligible for native Coverage"):
            self.invoke(fixture=False)

    def test_worker_and_failed_only_rerun_keep_the_resolved_toolchain(self):
        for attempt in ("1", "2"):
            with self.subTest(attempt=attempt):
                output, resolve, membership = self.invoke(attempt=attempt, worker=True)
                self.assertEqual(output, "")
                resolve.assert_not_called()
                self.assertEqual(membership.call_count, int(attempt == "2"))
        with self.assertRaisesRegex(ValueError, "not eligible for native Coverage"):
            self.invoke(attempt="2", worker=True, member=404)
        self.pr["head"]["sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "stale or invalid PR identity"):
            self.invoke(worker=True)
