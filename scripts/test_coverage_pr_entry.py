"""Coverage PR authorization and the existing fallback contract."""
import copy
import hashlib
from http.client import IncompleteRead, RemoteDisconnected
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from unittest import mock
from scripts.coverage_pr_entry import (
    ENTRY, PROJECT, RELEASE_BASE, RELEASE_ENTRY, RELEASE_S3_FIXTURE_BLOBS,
    S3_FIXTURE_BLOBS, has_s3_fixture, main, route,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_HELPER_SHA = "96874e708fa46db2af5a6f833b64bbd448b2d4d8"
CALLER = (PROJECT, "pull_request_target", ENTRY)
RELEASE_CALLER = (PROJECT, "pull_request_target", RELEASE_ENTRY)


class EntryTest(unittest.TestCase):
    def setUp(self):
        self.verify_fixture = mock.Mock(return_value=True)
        self.pr = {"number": 32871, "state": "open", "draft": False, "labels": [{"name": "size/XL"}],
                   "base": {"repo": {"full_name": PROJECT}, "ref": "main"},
                   "head": {"repo": {"full_name": "example/matrixone", "archived": False}, "sha": "a"*40},
                   "user": {"login": "example"}}
        self.event = {"number": 32871, "action": "synchronize", "pull_request": copy.deepcopy(self.pr)}

    def test_fork_and_source_are_bound_to_the_event(self):
        out = route(CALLER, self.event, self.pr, 204, self.verify_fixture)
        self.assertEqual((out["head_sha"], out["head_repository"], out["route"]),
                         ("a"*40, "example/matrixone", "native"))
        for target, key, value in [(self.pr["head"], "sha", "b"*40),
                                   (self.pr["head"]["repo"], "full_name", "other/matrixone"),
                                   (self.pr, "state", "closed"), (self.pr, "number", 1)]:
            old = target[key]; target[key] = value
            with self.assertRaises(ValueError):
                route(CALLER, self.event, self.pr, 204, self.verify_fixture)
            target[key] = old

    def test_unverified_requests_cannot_get_cache_runners(self):
        for status in (403, 401, 500):
            with self.assertRaises(ValueError):
                route(CALLER, self.event, self.pr, status, self.verify_fixture)
        self.assertEqual(route(CALLER, self.event, self.pr, 404, self.verify_fixture)["route"], "legacy")
        self.assertEqual(route(CALLER, self.event, self.pr, 302, self.verify_fixture)["route"], "legacy")
        for label in ("safe-to-test", "kind/test-ci"):
            self.pr["labels"] = [{"name": label}]
            self.assertEqual(route(CALLER, self.event, self.pr, 404, self.verify_fixture)["route"], "legacy")
        for ctx in [(PROJECT, "workflow_dispatch", ENTRY), (PROJECT, "pull_request", ENTRY),
                    ("example/matrixone", CALLER[1], ENTRY), (*CALLER[:2], ENTRY+"x")]:
            with self.assertRaises(ValueError):
                route(ctx, self.event, self.pr, 204, self.verify_fixture)

    def test_only_main_is_enabled_without_rollout_opt_in(self):
        for base, expected in (("main", "native"), ("4.2-dev", "legacy"),
                               ("4.1-dev", "legacy"), ("4.2-dev-other", "legacy"),
                               ("v4.2.8-hotfix", "legacy"), ("3.0-dev", "legacy")):
            with self.subTest(base=base):
                self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = base
                self.assertEqual(route(CALLER, self.event, self.pr, 204, self.verify_fixture)["route"], expected)
        self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = "4.2-dev"
        self.assertEqual(route(CALLER, self.event, self.pr, 404, self.verify_fixture)["route"], "legacy")
        self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = "main"
        for labels in ([], [{"name": "kind/refactor"}]):
            self.pr["labels"] = labels
            self.assertEqual(route(CALLER, self.event, self.pr, 204, self.verify_fixture)["route"], "native")
        self.pr["head"]["repo"]["archived"] = True
        self.assertEqual(route(CALLER, self.event, self.pr, 204, self.verify_fixture)["route"], "legacy")
        self.pr["head"]["repo"]["archived"] = False
        self.pr["base"]["ref"] = "4.2-dev"
        with self.assertRaisesRegex(ValueError, "stale or invalid PR identity"):
            route(CALLER, self.event, self.pr, 204, self.verify_fixture)

    def test_release_requires_opt_in_membership_and_exact_fixture(self):
        self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = RELEASE_BASE
        self.pr["draft"] = self.event["pull_request"]["draft"] = True
        self.pr["labels"] = self.event["pull_request"]["labels"] = []
        for caller in (RELEASE_CALLER, CALLER):
            for enabled, member, valid, expected in (
                    (False, 204, True, "legacy"), (True, 204, True, "native"),
                    (True, 204, False, "legacy"), (True, 302, True, "legacy"),
                    (True, 404, True, "legacy")):
                with self.subTest(caller=caller, enabled=enabled, member=member, valid=valid):
                    fixture = mock.Mock(return_value=valid)
                    result = route(caller, self.event, self.pr, member, fixture, enable_4_2_dev=enabled)
                    self.assertEqual(result["route"], expected)
                    if enabled and member == 204:
                        fixture.assert_called_once_with("example/matrixone", "a"*40)
                    else:
                        fixture.assert_not_called()
        self.pr["head"]["sha"] = "b"*40
        with self.assertRaisesRegex(ValueError, "stale or invalid PR identity"):
            route(RELEASE_CALLER, self.event, self.pr, 204, self.verify_fixture, enable_4_2_dev=True)
        self.pr["head"]["sha"] = "a"*40
        self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = "4.1-dev"
        with self.assertRaisesRegex(ValueError, "unexpected base"):
            route(RELEASE_CALLER, self.event, self.pr, 204, self.verify_fixture, enable_4_2_dev=True)
        self.assertEqual(route(CALLER, self.event, self.pr, 204, self.verify_fixture,
                               enable_4_2_dev=True)["route"], "legacy")

    def test_release_fixture_blobs_are_not_approved_for_main(self):
        # Snapshot from the pinned MatrixOne tree, independently checked by CI.
        snapshot = json.loads((ROOT / "scripts/testdata/coverage_4_2_fixture.json").read_text())
        release = {name: {sha} for name, sha in snapshot["files"].items()}
        self.assertEqual(RELEASE_S3_FIXTURE_BLOBS, release)
        cases = [(release, RELEASE_BASE, True), (release, "main", False),
                 (S3_FIXTURE_BLOBS, RELEASE_BASE, False)]
        for name in release:
            missing = release.copy()
            del missing[name]
            cases.append((missing, RELEASE_BASE, False))
            cases.append(({**release, name: {"b"*40}}, RELEASE_BASE, False))
        for blobs, base, expected in cases:
            with self.subTest(base=base, expected=expected), \
                 mock.patch("scripts.coverage_pr_entry.build_opener") as opener:
                files = [{"type": "file", "name": name, "sha": sorted(shas)[0]} for name, shas in blobs.items()]
                response = opener.return_value.open.return_value.__enter__.return_value
                response.getheader.return_value = None
                response.read.return_value = json.dumps(files).encode()
                self.assertEqual(has_s3_fixture(PROJECT, snapshot["commit"], "fixture-only", base=base), expected)
                request = opener.return_value.open.call_args.args[0]
                self.assertTrue(request.full_url.endswith("?ref=" + snapshot["commit"]))

    def test_drafts_use_the_same_routing_and_identity_checks(self):
        self.pr["draft"] = self.event["pull_request"]["draft"] = True
        for base, member, expected in (("main", 204, "native"), ("4.2-dev", 204, "legacy"),
                                       ("main", 404, "legacy"), ("4.2-dev", 302, "legacy"),
                                       ("4.1-dev", 204, "legacy")):
            for action in ("opened", "synchronize"):
                for labels in ([], [{"name": "kind/test-ci"}]):
                    with self.subTest(base=base, member=member, action=action, labels=labels):
                        self.event["action"] = action
                        self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = base
                        self.pr["labels"] = labels
                        self.assertEqual(route(CALLER, self.event, self.pr, member, self.verify_fixture)["route"], expected)
        self.pr["base"]["ref"] = self.event["pull_request"]["base"]["ref"] = "main"
        self.pr["head"]["repo"]["archived"] = True
        self.assertEqual(route(CALLER, self.event, self.pr, 204, self.verify_fixture)["route"], "legacy")
        self.pr["head"]["repo"]["archived"] = False
        self.pr["head"]["sha"] = "b"*40
        with self.assertRaisesRegex(ValueError, "stale or invalid PR identity"):
            route(CALLER, self.event, self.pr, 204, self.verify_fixture)

    def test_unlabelled_main_head_requires_reviewed_s3_fixture(self):
        self.pr["labels"] = self.event["pull_request"]["labels"] = []
        files = [{"type": "file", "name": name, "sha": sorted(shas)[0]}
                 for name, shas in S3_FIXTURE_BLOBS.items()]
        verify = lambda source, sha: has_s3_fixture(source, sha, "fixture-only")
        for listing, expected in ((files[1:], "legacy"), (files, "native"),
                                  ([dict(files[0], sha="b"*40), *files[1:]], "legacy")):
            with self.subTest(expected=expected), mock.patch("scripts.coverage_pr_entry.build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.getheader.return_value = None
                response.read.return_value = json.dumps(listing).encode()
                self.assertEqual(route(CALLER, self.event, self.pr, 204, verify)["route"], expected)
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url,
                                 "https://api.github.com/repos/example/matrixone/contents/pkg/fileservice?ref=" + "a"*40)
        with mock.patch("scripts.coverage_pr_entry.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError("https://api.github.com", 404, "missing", {}, None)
            self.assertEqual(route(CALLER, self.event, self.pr, 204, verify)["route"], "legacy")

    def test_fixture_transport_errors_keep_legacy_route(self):
        verify = lambda source, sha: has_s3_fixture(source, sha, "fixture-only")
        for error in (RemoteDisconnected("peer closed the connection"), ConnectionResetError(),
                      IncompleteRead(b"[", 10)):
            with self.subTest(error=type(error).__name__), \
                 mock.patch("scripts.coverage_pr_entry.build_opener") as opener:
                open_request = opener.return_value.open
                if isinstance(error, IncompleteRead):
                    response = open_request.return_value.__enter__.return_value
                    response.getheader.return_value = None
                    response.read.side_effect = error
                else:
                    open_request.side_effect = error
                self.assertEqual(route(CALLER, self.event, self.pr, 204, verify),
                                 {"route": "legacy", "allowed": "false"})

    def test_fixture_response_shorter_than_content_length_keeps_legacy_route(self):
        files = [{"type": "file", "name": name, "sha": sorted(shas)[0]}
                 for name, shas in S3_FIXTURE_BLOBS.items()]
        raw = json.dumps(files).encode()
        with mock.patch("scripts.coverage_pr_entry.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = raw
            response.getheader.return_value = str(len(raw) + 10)
            verify = lambda source, sha: has_s3_fixture(source, sha, "fixture-only")
            self.assertEqual(route(CALLER, self.event, self.pr, 204, verify),
                             {"route": "legacy", "allowed": "false"})

    def test_pr_number_does_not_pin_either_group(self):
        for i in range(1, 9):
            self.pr["number"] = self.event["number"] = self.event["pull_request"]["number"] = i
            self.assertEqual(route(CALLER, self.event, self.pr, 204, self.verify_fixture), {
                "route":"native", "allowed":"true", "head_sha":"a"*40, "head_repository":"example/matrixone"})

    def invoke(self, *, live=None, member=204, fixture=True, attempt="1", source=None, head=None,
               caller=CALLER, enable_release=False):
        client = SimpleNamespace(read=mock.Mock(return_value=(200, live or self.pr)),
                                 membership=mock.Mock(return_value=(member, [])))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "event").write_text(json.dumps(self.event))
            env = dict(zip(("GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW_REF"), caller))
            env.update(GITHUB_EVENT_PATH=str(root / "event"), GITHUB_OUTPUT=str(root / "outputs"),
                       GITHUB_RUN_ATTEMPT=attempt, CS_PR_METADATA_TOKEN="fixture-only",
                       CS_CACHE_READ_TOKEN="fixture-only", COVERAGE_ENABLE_4_2_DEV=str(enable_release).lower())
            if source is not None:
                env["COVERAGE_EXPECTED_SOURCE"] = source
            if head is not None:
                env["COVERAGE_EXPECTED_HEAD"] = head
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.dict(sys.modules, node_cache_real_pr_gate=client), \
                 mock.patch("scripts.coverage_pr_entry.has_s3_fixture", return_value=fixture) as lookup:
                main()
            client.read.assert_called_once_with(
                f"repos/matrixorigin/matrixone/pulls/{self.event['number']}", "fixture-only")
            output = root / "outputs"
            values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return values, client.membership, lookup

    def test_eligibility_exports_the_verified_source_for_native_jobs(self):
        values, membership, fixture = self.invoke()
        self.assertEqual(values, {"route": "native", "allowed": "true", "head_sha": "a"*40,
                                  "head_repository": "example/matrixone"})
        membership.assert_called_once()
        fixture.assert_called_once_with("example/matrixone", "a"*40, "fixture-only")

    def test_first_group_attempt_rejects_a_pr_changed_while_queued(self):
        bind = dict(source="example/matrixone", head="a"*40)
        _, membership, fixture = self.invoke(**bind)
        membership.assert_not_called()
        fixture.assert_not_called()
        for state in ("closed", "retargeted", "superseded", "archived"):
            with self.subTest(state=state):
                live = copy.deepcopy(self.pr)
                if state == "closed":
                    live["state"] = "closed"
                elif state == "retargeted":
                    live["base"]["ref"] = RELEASE_BASE
                elif state == "superseded":
                    live["head"]["sha"] = "b"*40
                else:
                    live["head"]["repo"]["archived"] = True
                with self.assertRaises(ValueError):
                    self.invoke(live=live, **bind)
        with self.assertRaisesRegex(ValueError, "PR no longer matches authorization"):
            self.invoke(source="example/another", head="a"*40)

    def test_failed_group_retry_checks_membership_for_the_same_immutable_head(self):
        bind = dict(source="example/matrixone", head="a"*40, attempt="2")
        _, membership, fixture = self.invoke(**bind)
        membership.assert_called_once()
        fixture.assert_not_called()
        with self.assertRaisesRegex(ValueError, "PR no longer authorized"):
            self.invoke(member=404, **bind)
        with self.assertRaisesRegex(ValueError, "PR no longer matches authorization"):
            self.invoke(**{**bind, "head": "b"*40})

    def test_release_retry_rechecks_the_release_fixture(self):
        self.event["pull_request"]["base"]["ref"] = self.pr["base"]["ref"] = RELEASE_BASE
        bind = dict(source="example/matrixone", head="a"*40, attempt="2", caller=RELEASE_CALLER)
        _, membership, fixture = self.invoke(enable_release=True, **bind)
        membership.assert_called_once()
        fixture.assert_called_once_with("example/matrixone", "a"*40, "fixture-only", base=RELEASE_BASE)
        with self.assertRaisesRegex(ValueError, "PR no longer authorized"):
            self.invoke(enable_release=True, fixture=False, **bind)
        with self.assertRaisesRegex(ValueError, "PR no longer matches authorization"):
            self.invoke(**bind)

    def test_fallback_keeps_the_legacy_body_with_updated_runner_placement(self):
        s = (ROOT/".github/workflows/coverage-ut-pre-cutover.yaml").read_text()
        checkout = "repository: matrixorigin/CI\n          ref: "+LEGACY_HELPER_SHA
        self.assertEqual(s.count(checkout), 1)
        runner = ("    # Keep both routes for main and 4.2-dev on the shared CS and Shanghai pools.\n"
                  "    runs-on: ${{ (github.base_ref == 'main' || github.base_ref == '4.2-dev') && "
                  "'amd64-mo-shanghai-ut-safe' || 'amd64-mo-guangzhou-2xlarge16' }}")
        original_runner = ("    # Preserve the Guangzhou network path and capacity used by main's\n"
                           "    # external Aliyun/QCloud coverage UT. This is intentionally larger than\n"
                           "    # the UT-only minimum while we validate the external-storage timeout fix.\n"
                           "    runs-on: amd64-mo-guangzhou-2xlarge16")
        self.assertEqual(s.count(runner), 1)
        b = s.replace(checkout, "repository: ${{ job.workflow_repository }}\n          ref: ${{ job.workflow_sha }}").replace(
            runner, original_runner).encode()
        self.assertEqual(hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest(),
                         "52b9213fa50e4de5184b9089ef15f13f82cf8a54")
