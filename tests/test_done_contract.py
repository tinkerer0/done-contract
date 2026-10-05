import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from done_contract import core, hooks  # noqa: E402
from done_contract.cli import merge_hooks, hook_snippet  # noqa: E402

BIN = ROOT / "bin" / "done-contract"


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self._env_backup = dict(os.environ)
        os.environ["DONE_CONTRACT_HOME"] = str(self.home)
        for k in ("DONE_CONTRACT_APPROVE_NO_TTY", "DONE_CONTRACT_MAX_BLOCKS", "DONE_CONTRACT_STOP_BUDGET"):
            os.environ.pop(k, None)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "README.md").write_text("hello\n")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.repo = self.repo.resolve()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        self.tmp.cleanup()

    def make_contract(self, items, task="task-1", **kw):
        c = core.init_contract(self.repo, task, "do the thing", **kw)
        c["items"] = items
        core.write_json(core.task_dir(self.repo, task) / "contract.json", c)
        return c

    def approve(self, task="task-1", **kw):
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        try:
            return core.approve_contract(self.repo, task, stdout=io.StringIO(), **kw)
        finally:
            os.environ.pop("DONE_CONTRACT_APPROVE_NO_TTY", None)


class TestLintAndGlobs(Base):
    def test_lint_rejects_missing_trivial_and_unknown(self):
        c = self.make_contract([
            {"id": "Q1", "text": "a", "check": "true"},
            {"id": "Q2", "text": "b", "check": ""},
            {"id": "Q3", "text": "c"},
            {"id": "Q4", "text": "d", "check": "echo done"},
            {"id": "Q4", "text": "dup", "check": "test -f README.md"},
            {"id": "Q5", "text": "typo", "check": "test -f README.md", "expcet": "x"},
        ])
        joined = "\n".join(core.lint_contract(c))
        self.assertIn("items[0].check 'true' can never fail", joined)
        self.assertIn("items[1].check is required", joined)
        self.assertIn("items[2].check is required", joined)
        self.assertIn("items[3].check 'echo done' can never fail", joined)
        self.assertIn("duplicates Q4", joined)
        self.assertIn("unknown keys: expcet", joined)
        c["extra"] = 1
        self.assertIn("unknown contract keys: extra", "\n".join(core.lint_contract(c)))

    def test_lint_accepts_real_checks_and_rejects_empty_items(self):
        c = self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.assertEqual(core.lint_contract(c), [])
        c["items"] = []
        self.assertIn("items must be a non-empty list", "\n".join(core.lint_contract(c)))

    def test_strength_is_info_only_and_cache_is_opt_in(self):
        self.assertEqual(core.strength_of("pytest -q tests"), "test")
        self.assertEqual(core.strength_of("pytest -q && curl -f http://x"), "http")  # compound: weakest wins
        self.assertEqual(core.strength_of("test -f docs/x.md"), "existence")
        self.assertEqual(core.strength_of("grep -q RATE docs/x.md"), "content")
        self.assertEqual(core.strength_of("./scripts/smoke.sh"), "other")
        self.assertFalse(core.item_cacheable({"check": "pytest -q"}))
        self.assertFalse(core.item_cacheable({"check": "pytest -q", "cache": False}))
        self.assertTrue(core.item_cacheable({"check": "pytest -q", "cache": True}))

    def test_globs(self):
        self.assertTrue(core.matches_any("tests/test_x.py", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("tests/a/b/test_x.py", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("src/foo_test.go", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("web/app.spec.ts", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("pkg/__tests__/x.js", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("./tests/x.py", core.DEFAULT_PROTECTED))
        self.assertFalse(core.matches_any("src/app.py", core.DEFAULT_PROTECTED))
        self.assertFalse(core.matches_any("docs/testing.md", core.DEFAULT_PROTECTED))

    def test_repo_relative_paths(self):
        lex, res = core.repo_relative(self.repo, "fixture.txt", self.repo / "tests")
        self.assertEqual(lex, "tests/fixture.txt")
        lex, res = core.repo_relative(self.repo, str(self.repo / "tests" / "x.py"))
        self.assertEqual((lex, res), ("tests/x.py", "tests/x.py"))
        lex, res = core.repo_relative(self.repo, "/etc/hosts")
        self.assertEqual((lex, res), (None, None))
        outside = Path(self.tmp.name) / "outside.py"
        outside.write_text("x")
        (self.repo / "tests" / "test_link.py").symlink_to(outside)
        lex, res = core.repo_relative(self.repo, "tests/test_link.py")
        self.assertEqual(lex, "tests/test_link.py")
        self.assertIsNone(res)


class TestTreeHash(Base):
    def test_tree_hash_tracks_untracked_and_changes(self):
        t0 = core.working_tree_hash(self.repo)
        self.assertEqual(t0, core.working_tree_hash(self.repo))
        (self.repo / "new.txt").write_text("x\n")
        t1 = core.working_tree_hash(self.repo)
        self.assertNotEqual(t0, t1)
        self.assertEqual(core.changed_paths(self.repo, t0, t1), ["new.txt"])
        (self.repo / "README.md").write_text("changed\n")
        t2 = core.working_tree_hash(self.repo)
        self.assertEqual(sorted(core.changed_paths(self.repo, t0, t2)), ["README.md", "new.txt"])
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertIn("?? new.txt", status)  # the user's real index is untouched

    def test_changed_paths_handle_non_ascii_and_renames(self):
        (self.repo / "tests" / "검사.py").write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "korean")
        t0 = core.working_tree_hash(self.repo)
        (self.repo / "tests" / "검사.py").write_text("y\n")
        t1 = core.working_tree_hash(self.repo)
        self.assertEqual(core.changed_paths(self.repo, t0, t1), ["tests/검사.py"])
        git(self.repo, "config", "diff.renames", "true")
        (self.repo / "tests" / "test_a.py").rename(self.repo / "gone.py")
        t2 = core.working_tree_hash(self.repo)
        self.assertEqual(sorted(core.changed_paths(self.repo, t1, t2)), ["gone.py", "tests/test_a.py"])

    def test_done_contract_dir_is_excluded(self):
        core.init_contract(self.repo, "task-1", "r")
        t0 = core.working_tree_hash(self.repo)
        (self.repo / ".done-contract" / "scratch.txt").write_text("x")
        self.assertEqual(t0, core.working_tree_hash(self.repo))

    def test_git_file_layout(self):
        """Repositories whose .git is a file (separate git dir, linked worktrees)."""
        other = Path(self.tmp.name) / "gitfile-repo"
        gitdir = Path(self.tmp.name) / "gitdir"
        other.mkdir()
        git(other, "init", "-q", "-b", "main", f"--separate-git-dir={gitdir}")
        git(other, "config", "user.email", "t@example.com")
        git(other, "config", "user.name", "t")
        (other / "a.txt").write_text("a")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "init")
        other = other.resolve()
        self.assertTrue((other / ".git").is_file())
        c = core.init_contract(other, "t", "req")
        c["items"] = [{"id": "Q1", "text": "a", "check": "test -f a.txt"}]
        core.write_json(core.task_dir(other, "t") / "contract.json", c)
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        core.approve_contract(other, "t", stdout=io.StringIO())
        self.assertEqual(core.run_check(other, "t")["verdict"], core.VERDICT_PASS)
        self.assertEqual(core.close_contract(other, "t")["verdict"], core.VERDICT_PASS)


class TestApproval(Base):
    def test_approve_requires_tty(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        with self.assertRaises(core.NotInteractive):
            core.approve_contract(self.repo, "task-1", stdout=io.StringIO())

    def test_approve_refuses_lint_failure(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "true"}])
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        with self.assertRaises(core.DoneContractError):
            core.approve_contract(self.repo, "task-1", stdout=io.StringIO())

    def test_approval_is_bound_to_contract_hash(self):
        c = self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        self.assertIsNotNone(core.find_approval(self.repo, c))
        c["items"][0]["check"] = "test -f other.md"
        core.write_json(core.task_dir(self.repo, "task-1") / "contract.json", c)
        self.assertIsNone(core.find_approval(self.repo, core.load_contract(self.repo, "task-1")))
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_UNAPPROVED)
        self.assertEqual(core.contract_state(self.repo, "task-1"), "draft")

    def test_approve_refuses_dirty_tree_unless_accepted(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        (self.repo / "early.py").write_text("print(1)\n")
        with self.assertRaises(core.DoneContractError) as ctx:
            self.approve()
        self.assertIn("early.py", str(ctx.exception))
        rec = self.approve(accept_dirty=True)
        self.assertEqual(rec["pre_approval_changes"], ["early.py"])
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["approval"]["pre_approval_changes"], ["early.py"])

    def test_approve_rechecks_tree_after_the_person_answers(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        repo = self.repo

        class RacingStdin(io.StringIO):
            def isatty(self):
                return True

            def readline(self):
                (repo / "new.py").write_text("sneaky\n")
                return "y\n"

        with self.assertRaises(core.DoneContractError) as ctx:
            core.approve_contract(self.repo, "task-1", stdin=RacingStdin(), stdout=io.StringIO())
        self.assertIn("new.py", str(ctx.exception))
        self.assertEqual(core.contract_state(self.repo, "task-1"), "draft")


class TestCheck(Base):
    def test_fail_then_pass_no_cache_by_default(self):
        self.make_contract([
            {"id": "Q1", "text": "readme", "check": "test -f README.md"},
            {"id": "Q2", "text": "docs", "check": "grep -q RATE docs/config.md"},
        ])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_FAIL)
        self.assertEqual([i["status"] for i in ev["items"]], ["PASS", "FAIL"])
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "config.md").write_text("RATE_LIMIT=5\n")
        ev2 = core.run_check(self.repo, "task-1")
        self.assertEqual(ev2["verdict"], core.VERDICT_PASS)
        self.assertEqual(ev2["hmac"], core.sign_evidence(ev2))
        ev3 = core.run_check(self.repo, "task-1")
        self.assertFalse(ev3["reused"])
        self.assertFalse(any(i["reused"] for i in ev3["items"]))
        self.assertTrue((core.task_dir(self.repo, "task-1") / "evidence.md").exists())

    def test_opt_in_cache_reuses_only_marked_items(self):
        marker = Path(self.tmp.name) / "service_up"
        marker.write_text("1")
        self.make_contract([{"id": "Q1", "text": "svc", "check": f"test -f {marker}"},
                            {"id": "Q2", "text": "local", "check": "test -f README.md", "cache": True}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)
        marker.unlink()  # external state changes, tree does not
        ev2 = core.run_check(self.repo, "task-1")
        self.assertEqual(ev2["items"][0]["status"], "FAIL")
        self.assertFalse(ev2["items"][0]["reused"])
        self.assertTrue(ev2["items"][1]["reused"])
        self.assertEqual(ev2["verdict"], core.VERDICT_FAIL)
        ev3 = core.run_check(self.repo, "task-1", reuse=False)
        self.assertFalse(ev3["items"][1]["reused"])

    def test_symlinked_external_input_is_not_cached_by_default(self):
        marker = Path(self.tmp.name) / "ext"
        marker.write_text("1")
        (self.repo / "ready").symlink_to(marker)
        self.make_contract([{"id": "Q1", "text": "ready", "check": "test -f ready"}])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)
        marker.unlink()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_FAIL)

    def test_expect_runs_once_and_searches_full_output(self):
        counter = Path(self.tmp.name) / "runs"
        self.make_contract([{"id": "Q1", "text": "a", "check": f"echo run >> {counter}; printf 'EXPECTED\\n'; seq 1 500", "expect": "EXPECTED"},
                            {"id": "Q2", "text": "b", "check": "cat README.md", "expect": "nope"}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual([i["status"] for i in ev["items"]], ["PASS", "FAIL"])
        self.assertTrue(ev["items"][0]["expect_matched"])
        self.assertFalse(ev["items"][1]["expect_matched"])
        self.assertEqual(counter.read_text().count("run"), 1)
        v = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(all(r["agree"] for r in v["rows"]))

    def test_timeout_is_fail_and_kills_children(self):
        pidfile = Path(self.tmp.name) / "child.pid"
        self.make_contract([{"id": "Q1", "text": "slow", "check": f"sleep 60 & echo $! > {pidfile}; wait", "timeout": 1}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_FAIL)
        self.assertTrue(ev["items"][0]["timed_out"])
        self.assertLess(ev["items"][0]["duration_s"], 10)
        pid = int(pidfile.read_text().strip())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_budget_cuts_running_command_as_error(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "sleep 3", "timeout": 10},
                            {"id": "Q2", "text": "b", "check": "test -f README.md"}])
        self.approve()
        started = time.monotonic()
        ev = core.run_check(self.repo, "task-1", budget_s=0.5)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(ev["items"][0]["status"], "ERROR")
        self.assertIn("time budget", ev["items"][0]["error"])
        self.assertEqual(ev["items"][1]["status"], "ERROR")
        self.assertEqual(ev["verdict"], core.VERDICT_ERROR)
        ev2 = core.run_check(self.repo, "task-1")  # ERROR evidence is never reused
        self.assertEqual(ev2["verdict"], core.VERDICT_PASS)

    def test_blocked_incomplete_paused_precedence(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"},
                            {"id": "Q2", "text": "b", "check": "test -f missing.md"}])
        self.approve()
        with self.assertRaises(core.DoneContractError):
            core.set_mark(self.repo, "task-1", "Q2", "blocked", None)
        core.set_mark(self.repo, "task-1", "Q2", "blocked", "needs credentials")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_INCOMPLETE)
        self.assertEqual(ev["items"][1]["status"], "BLOCKED")
        self.assertEqual(ev["items"][1]["blocked_reason"], "needs credentials")
        core.set_mark(self.repo, "task-1", "Q2", "open", None)
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_FAIL)
        core.set_paused(self.repo, "task-1", "waiting for user")
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PAUSED)
        core.set_paused(self.repo, "task-1", None)
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_FAIL)
        (self.repo / "missing.md").write_text("x")
        core.set_mark(self.repo, "task-1", "Q2", "blocked", "stale reason")
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)

    def test_protected_changes(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        (self.repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_TESTS_CHANGED)
        self.assertEqual(ev["protected_changed"], ["tests/test_a.py"])

    def test_protected_rename_and_non_ascii_are_detected(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        git(self.repo, "config", "diff.renames", "true")
        (self.repo / "tests" / "test_a.py").rename(self.repo / "gone.py")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_TESTS_CHANGED)
        self.assertIn("tests/test_a.py", ev["protected_changed"])
        (self.repo / "gone.py").rename(self.repo / "tests" / "test_a.py")
        (self.repo / "tests" / "검사.py").write_text("x\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_TESTS_CHANGED)
        self.assertEqual(ev["protected_changed"], ["tests/검사.py"])

    def test_protected_changes_allowed(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}], allow_protected_changes=True)
        self.approve()
        (self.repo / "tests" / "test_b.py").write_text("def test_b():\n    pass\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)
        self.assertEqual(ev["protected_changed"], ["tests/test_b.py"])

    def test_repo_checks_never_cached(self):
        marker = Path(self.tmp.name) / "m"
        marker.write_text("1")
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md", "cache": True}], repo_checks=[f"test -f {marker}"])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)
        marker.unlink()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_FAIL)

    def test_stale_when_check_modifies_tree(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "touch generated.txt"}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_STALE)
        self.assertEqual(ev["stale_paths"], ["generated.txt"])
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)

    def test_run_failure_becomes_error_and_invalidates_old_pass(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)
        with mock.patch.object(core, "run_command", side_effect=OSError("simulated spawn failure")):
            ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_ERROR)
        self.assertIn("simulated spawn failure", ev["items"][0]["error"])
        with mock.patch.object(core, "run_command", side_effect=OSError("still broken")):
            with self.assertRaises(core.DoneContractError):
                core.close_contract(self.repo, "task-1")
        self.assertEqual(core.contract_state(self.repo, "task-1"), "approved")

    def test_interrupted_check_leaves_no_usable_evidence(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)
        with mock.patch.object(core, "run_command", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                core.run_check(self.repo, "task-1")
        ev = core.load_evidence(self.repo, "task-1")
        self.assertTrue(ev["in_progress"])
        self.assertEqual(ev["verdict"], core.VERDICT_ERROR)
        contract = core.load_contract(self.repo, "task-1")
        self.assertFalse(core.evidence_is_current(self.repo, "task-1", ev, contract, core.load_marks(self.repo, "task-1"), core.working_tree_hash(self.repo)))

    def test_close_reruns_checks_under_lock(self):
        marker = Path(self.tmp.name) / "svc"
        marker.write_text("1")
        self.make_contract([{"id": "Q1", "text": "a", "check": f"test -f {marker}"}])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)
        marker.unlink()
        with self.assertRaises(core.DoneContractError) as ctx:
            core.close_contract(self.repo, "task-1")
        self.assertIn("FAIL", str(ctx.exception))
        self.assertEqual(core.contract_state(self.repo, "task-1"), "approved")
        marker.write_text("1")
        ev = core.close_contract(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)
        self.assertIsNone(core.active_task(self.repo))
        self.assertEqual(core.load_marks(self.repo, "task-1")["closed_verdict"], core.VERDICT_PASS)

    def test_close_incomplete_keeps_verdict(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"},
                            {"id": "Q2", "text": "b", "check": "test -f missing.md"}])
        self.approve()
        with self.assertRaises(core.DoneContractError):
            core.close_contract(self.repo, "task-1")
        core.set_mark(self.repo, "task-1", "Q2", "blocked", "cannot")
        ev = core.close_contract(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_INCOMPLETE)
        self.assertEqual(core.contract_state(self.repo, "task-1"), "closed")
        self.assertEqual(core.load_marks(self.repo, "task-1")["closed_verdict"], core.VERDICT_INCOMPLETE)

    def test_verify_reports_reproduction_and_currency(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        core.run_check(self.repo, "task-1")
        v = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(v["ok"] and v["agree"] and v["hmac_valid"] and v["current"])
        (self.repo / "other.txt").write_text("x")
        v2 = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(v2["agree"])
        self.assertFalse(v2["current"])
        self.assertFalse(v2["ok"])
        (self.repo / "README.md").unlink()
        v3 = core.verify_evidence(self.repo, "task-1")
        self.assertFalse(v3["agree"])

    def test_init_needs_abandon_reason_to_replace_approved_contract(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        with self.assertRaises(core.DoneContractError):
            core.init_contract(self.repo, "task-2", "another")
        core.init_contract(self.repo, "task-2", "another", abandon_reason="user changed direction")
        self.assertEqual(core.active_task(self.repo), "task-2")
        self.assertEqual(core.contract_state(self.repo, "task-1"), "abandoned")
        with self.assertRaises(core.DoneContractError):
            core.init_contract(self.repo, "task-1", "again")


class TestStopHook(Base):
    def payload(self, **kw):
        base = {"session_id": "sess-1", "cwd": str(self.repo), "hook_event_name": "Stop", "stop_hook_active": False,
                "last_assistant_message": "모든 테스트가 통과했고 작업을 완료했다", "transcript_path": "/nonexistent"}
        base.update(kw)
        return base

    def test_no_contract_is_silent(self):
        self.assertIsNone(hooks.stop(self.payload()))
        code, out = hooks.run_hook("stop", json.dumps(self.payload()))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("systemMessage", hooks.stop(self.payload(), require_contract=True))

    def test_unapproved_does_not_block(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing"}])
        out = hooks.stop(self.payload())
        self.assertIn("systemMessage", out)
        self.assertNotIn("decision", out)

    def test_block_until_fixed_regardless_of_prose(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve()
        out = hooks.stop(self.payload())
        self.assertEqual(out["decision"], "block")
        self.assertIn("Q1 FAIL", out["reason"])
        self.assertIn("1/3", out["reason"])
        out2 = hooks.stop(self.payload(stop_hook_active=True))
        self.assertEqual(out2["decision"], "block")
        self.assertIn("2/3", out2["reason"])
        (self.repo / "missing.md").write_text("x")
        out3 = hooks.stop(self.payload(stop_hook_active=True))
        self.assertNotIn("decision", out3)
        self.assertIn("PASS", out3["systemMessage"])

    def test_block_cap_releases_without_success_receipt_and_recovers_after_fix(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve()
        for n in range(3):
            self.assertEqual(hooks.stop(self.payload())["decision"], "block", n)
        out = hooks.stop(self.payload())
        self.assertNotIn("decision", out)
        self.assertIn("완료가 아니다", out["systemMessage"])
        self.assertIn("FAIL", out["systemMessage"])
        ev = core.load_evidence(self.repo, "task-1")
        self.assertEqual(ev["released"]["reason"], "block_cap")
        self.assertEqual(ev["hmac"], core.sign_evidence(ev))
        with self.assertRaises(core.DoneContractError):
            core.close_contract(self.repo, "task-1")
        # a real fix after the cap still yields a fresh PASS receipt
        (self.repo / "missing.md").write_text("x")
        out2 = hooks.stop(self.payload())
        self.assertNotIn("decision", out2)
        self.assertIn("PASS", out2["systemMessage"])
        self.assertNotIn("released", core.load_evidence(self.repo, "task-1"))

    def test_blocked_mark_allows_stop_with_disclosure(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve()
        core.set_mark(self.repo, "task-1", "Q1", "blocked", "needs prod access")
        out = hooks.stop(self.payload())
        self.assertNotIn("decision", out)
        self.assertIn("INCOMPLETE", out["systemMessage"])
        self.assertIn("needs prod access", out["systemMessage"])

    def test_block_budget_is_per_task(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve()
        for _ in range(3):
            hooks.stop(self.payload())
        self.assertNotIn("decision", hooks.stop(self.payload()))
        core.set_mark(self.repo, "task-1", "Q1", "blocked", "x")
        core.close_contract(self.repo, "task-1")
        self.make_contract([{"id": "Q1", "text": "b", "check": "test -f missing2.md"}], task="task-2")
        self.approve(task="task-2")
        self.assertEqual(hooks.stop(self.payload())["decision"], "block")

    def test_error_verdict_blocks(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "sleep 2", "timeout": 10}, {"id": "Q2", "text": "b", "check": "test -f README.md"}])
        self.approve()
        os.environ["DONE_CONTRACT_STOP_BUDGET"] = "0.3"
        out = hooks.stop(self.payload())
        self.assertEqual(out["decision"], "block")
        self.assertIn("ERROR", out["reason"])

    def test_malformed_input_fails_open(self):
        code, out = hooks.run_hook("stop", "not json")
        self.assertEqual((code, out), (0, ""))

    def test_internal_error_is_visible(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        (core.task_dir(self.repo, "task-1") / "marks.json").write_text("{not json")
        code, out = hooks.run_hook("stop", json.dumps(self.payload()))
        self.assertEqual(code, 0)
        self.assertIn("done-contract ERROR", json.loads(out)["systemMessage"])


class TestPreToolHook(Base):
    def payload(self, tool, cwd=None, **inp):
        return {"session_id": "s", "cwd": cwd or str(self.repo), "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp}

    def decision(self, tool, cwd=None, **inp):
        out = hooks.pretool(self.payload(tool, cwd=cwd, **inp))
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def test_inactive_before_approval_without_policy(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.assertIsNone(hooks.pretool(self.payload("Edit", file_path=str(self.repo / "tests" / "test_a.py"))))

    def test_require_contract_policy(self):
        p = lambda tool, **inp: hooks.pretool(self.payload(tool, **inp), require_contract=True)
        d = lambda tool, **inp: (p(tool, **inp) or {}).get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(d("Edit", file_path="src/app.py"), "deny")
        self.assertEqual(d("Bash", command="echo x > src/app.py"), "deny")
        self.assertEqual(d("Bash", command="  rm app.py"), "deny")
        self.assertEqual(d("Bash", command="python3 <<'EOF'\nfrom pathlib import Path\nPath('app.py').write_text('x')\nEOF"), "deny")
        self.assertEqual(d("Bash", command="python3 -c \"open('x','w')\""), "deny")
        self.assertEqual(d("Bash", command="node script.js"), "deny")
        self.assertEqual(d("Bash", command="make"), "ask")
        self.assertIsNone(p("Bash", command="cat src/app.py"))
        self.assertIsNone(p("Bash", command="git status && git diff"))
        self.assertIsNone(p("Bash", command="pytest -q"))
        self.assertIsNone(p("Bash", command="done-contract init --task t --request 'x'"))
        self.assertEqual(d("Bash", command="done-contract approve"), "deny")
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.assertIsNone(p("Edit", file_path=".done-contract/task-1/contract.json"))  # drafting allowed
        self.assertEqual(d("Edit", file_path="src/app.py"), "deny")
        self.approve()
        self.assertIsNone(p("Edit", file_path="src/app.py"))
        self.assertEqual(d("Edit", file_path=".done-contract/task-1/contract.json"), "deny")

    def test_denies_protected_edit_and_contract_edit(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        self.assertEqual(self.decision("Edit", file_path=str(self.repo / "tests" / "test_a.py")), "deny")
        self.assertEqual(self.decision("Write", file_path="tests/new_test.py"), "deny")
        self.assertEqual(self.decision("Write", file_path="src/test_new.py"), "deny")
        self.assertEqual(self.decision("Edit", file_path=".done-contract/task-1/contract.json"), "deny")
        self.assertEqual(self.decision("Edit", file_path=".done-contract/task-1/evidence.json"), "deny")
        self.assertIsNone(self.decision("Edit", file_path="src/app.py"))
        self.assertIsNone(self.decision("Edit", file_path="/etc/hosts"))

    def test_paths_relative_to_cwd_symlinks_and_absolute_bash(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        self.assertEqual(self.decision("Edit", cwd=str(self.repo / "tests"), file_path="fixture.txt"), "deny")
        self.assertEqual(self.decision("Edit", cwd=str(self.repo / "tests"), file_path="../src/app.py"), None)
        outside = Path(self.tmp.name) / "outside.py"
        outside.write_text("x")
        (self.repo / "tests" / "test_link.py").symlink_to(outside)
        self.assertEqual(self.decision("Edit", file_path="tests/test_link.py"), "deny")
        self.assertEqual(self.decision("Bash", command=f"rm {self.repo}/tests/test_a.py"), "ask")
        self.assertEqual(self.decision("Bash", cwd=str(self.repo / "tests"), command="rm test_a.py"), "ask")

    def test_allowed_when_contract_permits(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}], allow_protected_changes=True)
        self.approve()
        self.assertIsNone(self.decision("Edit", file_path="tests/test_a.py"))

    def test_bash_rules(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        self.assertEqual(self.decision("Bash", command="done-contract approve --yes"), "deny")
        self.assertEqual(self.decision("Bash", command="DONE_CONTRACT_APPROVE_NO_TTY=1 bin/done-contract approve"), "deny")
        self.assertEqual(self.decision("Bash", command="rm -rf .done-contract/task-1"), "deny")
        self.assertEqual(self.decision("Bash", command="cat > .done-contract/task-1/contract.json <<EOF\n{}\nEOF"), "deny")
        self.assertEqual(self.decision("Bash", command="sed -i 's/assert/pass/' tests/test_a.py"), "ask")
        self.assertEqual(self.decision("Bash", command="echo x > tests/test_a.py"), "ask")
        self.assertEqual(self.decision("Bash", command="git checkout -- tests/test_a.py"), "ask")
        self.assertIsNone(self.decision("Bash", command="pytest -q tests/test_a.py"))
        self.assertIsNone(self.decision("Bash", command="done-contract check"))
        self.assertIsNone(self.decision("Bash", command="cat tests/test_a.py"))
        self.assertIsNone(self.decision("Bash", command="echo x > src/out.txt"))


class TestCli(Base):
    def run_cli(self, *args, env=None, stdin=None, bin_path=None, cwd=None):
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run([sys.executable, str(bin_path or BIN), "--repo", str(self.repo), *args], capture_output=True,
                              text=True, env=e, input=stdin, cwd=cwd)

    def test_init_check_status_flow_and_exit_codes(self):
        r = self.run_cli("init", "--task", "feat-x", "--request", "add feature x")
        self.assertEqual(r.returncode, 0, r.stderr)
        cpath = core.task_dir(self.repo, "feat-x") / "contract.json"
        c = json.loads(cpath.read_text())
        c["items"] = [{"id": "Q1", "text": "x exists", "check": "test -f x.txt"}, {"id": "Q2", "text": "y", "check": "test -f y.txt"}]
        cpath.write_text(json.dumps(c))
        self.assertEqual(self.run_cli("check").returncode, 2)  # unapproved
        self.assertEqual(self.run_cli("approve").returncode, 3)  # no TTY
        r = self.run_cli("approve", env={"DONE_CONTRACT_APPROVE_NO_TTY": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)
        (self.repo / "x.txt").write_text("x")
        r = self.run_cli("mark", "Q2", "blocked", "--reason", "later")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("check", "--json")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"], "INCOMPLETE")
        (self.repo / "y.txt").write_text("y")
        r = self.run_cli("check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"], "PASS")
        r = self.run_cli("status")
        self.assertIn("approval: yes", r.stdout)
        self.assertIn("state: approved", r.stdout)
        self.assertEqual(self.run_cli("verify").returncode, 0)
        self.assertEqual(self.run_cli("close").returncode, 0)
        self.assertIn("state: closed", self.run_cli("status", "--task", "feat-x").stdout)

    def test_internal_errors_exit_5(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        (core.task_dir(self.repo, "task-1") / "marks.json").write_text("{not json")
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("error (internal)", r.stderr)

    def test_symlinked_launcher_works_from_elsewhere(self):
        link = Path(self.tmp.name) / "bin" / "done-contract"
        link.parent.mkdir()
        link.symlink_to(BIN)
        r = self.run_cli("version", bin_path=link, cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), core.VERSION)

    def test_hook_stop_via_cli(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve()
        payload = {"session_id": "cli-s", "cwd": str(self.repo), "stop_hook_active": False}
        r = self.run_cli("hook", "stop", stdin=json.dumps(payload))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_hook_install_merge_and_upgrade(self):
        plain = hook_snippet("/x y/done-contract")
        self.assertIn("'/x y/done-contract' hook stop", plain["hooks"]["Stop"][0]["hooks"][0]["command"])
        settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
        merged = merge_hooks(settings, plain)
        self.assertEqual(len(merged["hooks"]["Stop"]), 2)
        self.assertEqual(len(merged["hooks"]["PreToolUse"]), 1)
        strict = hook_snippet("/x y/done-contract", require_contract=True)
        merged = merge_hooks(merged, strict)
        self.assertEqual(len(merged["hooks"]["Stop"]), 2)  # replaced, not duplicated
        self.assertEqual(merged["hooks"]["Stop"][0]["hooks"][0]["command"], "other")
        self.assertIn("--require-contract", merged["hooks"]["Stop"][1]["hooks"][0]["command"])
        self.assertIn("--require-contract", merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        r = self.run_cli("hook", "install", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("hook", "install", "--write", "--require-contract")
        self.assertEqual(r.returncode, 0, r.stderr)
        written = json.loads((self.repo / ".claude" / "settings.json").read_text())
        self.assertEqual(len(written["hooks"]["Stop"]), 1)
        self.assertIn("--require-contract", written["hooks"]["Stop"][0]["hooks"][0]["command"])


class TestReviewRound2(Base):
    """Regressions for the second Codex review (N1-N6)."""

    def test_bash_policy_loopholes_closed(self):
        p = lambda cmd: hooks.pretool({"session_id": "s", "cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": cmd}}, require_contract=True)
        d = lambda cmd: (p(cmd) or {}).get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(d("env node edit.js"), "deny")
        self.assertEqual(d("FOO=1 node edit.js"), "deny")
        self.assertEqual(d("ruff check --fix app.py"), "deny")
        self.assertEqual(d("eslint --fix src"), "deny")
        self.assertEqual(d("tsc"), "ask")
        self.assertIsNone(p("tsc --noEmit"))
        self.assertEqual(d("sed 'w generated.txt' README.md"), "ask")
        self.assertEqual(d("find . -name generated.txt -delete"), "deny")
        self.assertIsNone(p("find . -name '*.py'"))
        self.assertEqual(d("cat README.md\nnode edit.js"), "deny")
        self.assertEqual(d("done-contract status && node edit.js"), "deny")
        self.assertIsNone(p("env FOO=1 pytest -q"))
        self.assertEqual(d("env"), "ask")  # dumps the environment (possibly secrets): a person decides
        self.assertIsNone(p("done-contract status && cat README.md"))

    def test_merge_hooks_preserves_other_hooks_in_same_group(self):
        settings = {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "/x/done-contract hook stop", "timeout": 900},
            {"type": "command", "command": "/x/other-audit-hook"},
        ]}, {"matcher": "", "hooks": [{"type": "command", "command": "/x/done-contract hook stop"}]}]}}
        merged = merge_hooks(settings, hook_snippet("/x/done-contract", require_contract=True))
        stop = merged["hooks"]["Stop"]
        commands = [h["command"] for g in stop for h in g["hooks"]]
        self.assertIn("/x/other-audit-hook", commands)
        self.assertEqual(sum("done-contract hook stop" in c for c in commands), 1)
        self.assertIn("--require-contract", [c for c in commands if "done-contract" in c][0])
        self.assertEqual(len(stop), 2)  # group that only held ours was dropped, new group added

    def test_verify_detects_mutation_during_run(self):
        trigger = Path(self.tmp.name) / "trigger"
        self.make_contract([{"id": "Q1", "text": "a", "check": f"test -f {trigger} && echo changed > README.md; true"}])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)
        trigger.write_text("1")
        v = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(v["agree"])
        self.assertFalse(v["current"])
        self.assertFalse(v["ok"])
        self.assertEqual(v["stale_paths"], ["README.md"])

    def test_newline_in_protected_path(self):
        weird = self.repo / "tests" / "fixture\ncase.txt"
        weird.write_text("a\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "newline name")
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        weird.write_text("b\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_TESTS_CHANGED)
        self.assertEqual(ev["protected_changed"], ["tests/fixture\ncase.txt"])
        self.assertTrue(core.matches_any("tests/fixture\ncase.txt", core.DEFAULT_PROTECTED))
        self.assertFalse(core.matches_any("tests/x.py\n", ["tests/x.py"]))

    def test_lock_wait_counts_against_budget(self):
        import threading
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        core.run_check(self.repo, "task-1")
        held = threading.Event()
        release = threading.Event()

        def holder():
            with core.repo_lock(self.repo):
                held.set()
                release.wait(5)

        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        started = time.monotonic()
        with self.assertRaises(core.LockBusy):
            core.run_check(self.repo, "task-1", budget_s=0.3)
        self.assertLess(time.monotonic() - started, 2)
        os.environ["DONE_CONTRACT_STOP_BUDGET"] = "0.3"
        payload = {"session_id": "s", "cwd": str(self.repo), "stop_hook_active": False}
        for n in range(3):
            out = hooks.stop(payload)
            self.assertEqual(out["decision"], "block", n)
            self.assertIn(f"{n + 1}/3", out["reason"])
        out = hooks.stop(payload)  # past the cap: released, visibly unverified, never "4/3"
        self.assertNotIn("decision", out)
        self.assertIn("검증되지 않았다", out["systemMessage"])
        r = subprocess.run([sys.executable, str(BIN), "--repo", str(self.repo), "check", "--budget", "0.2"], capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("unverified", r.stderr)
        release.set()
        t.join(5)
        ev = core.load_evidence(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)  # the earlier evidence was not touched

    def test_hmac_key_first_use_is_atomic_and_validated(self):
        import threading
        results = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            results.append(core._hmac_key())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(results[0]), 64)
        self.assertEqual(sorted(p.name for p in self.home.iterdir() if p.name.startswith(".key.")), [])
        (self.home / "key").write_bytes(b"")
        with self.assertRaises(core.DoneContractError):
            core._hmac_key()

    def test_expect_beyond_search_limit_is_error(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "head -c 3000 /dev/zero | tr '\\0' 'x'; printf '\\nEXPECTED\\n'", "expect": "EXPECTED"}])
        self.approve()
        with mock.patch.object(core, "MAX_OUTPUT_SEARCH_BYTES", 1000):
            ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["items"][0]["status"], "ERROR")
        self.assertIn("MiB", ev["items"][0]["error"])
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)


if __name__ == "__main__":
    unittest.main()
