import io
import json
import os
import shutil
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
        for k in ("DONE_CONTRACT_APPROVE_NO_TTY", "DONE_CONTRACT_MAX_BLOCKS", "DONE_CONTRACT_STOP_BUDGET", "CLAUDE_PROJECT_DIR"):
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
        self.approve(dry_run=False)  # the approval dry run would count as a run
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
        with self.assertRaises(core.DoneContractError):
            self.approve()  # the dry run notices that the check writes into the tree
        (self.repo / "generated.txt").unlink()  # the dry run's side effect
        self.approve(dry_run=False)
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


class TestScopedReuse(Base):
    """Small change: only the items that depend on it run. Big change: everything runs."""

    def counter_check(self, name, extra="test -f README.md"):
        path = Path(self.tmp.name) / name
        return path, f"echo run >> {path}; {extra}"

    def runs(self, path):
        return path.read_text().count("run") if path.exists() else 0

    def test_watch_reruns_only_when_watched_paths_change(self):
        c1, cmd1 = self.counter_check("q1")
        c2, cmd2 = self.counter_check("q2")
        self.make_contract([{"id": "Q1", "text": "auth", "check": cmd1, "watch": ["src/auth/**", "tests/test_auth.py"]},
                            {"id": "Q2", "text": "docs", "check": cmd2}])
        self.approve(dry_run=False)
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual((self.runs(c1), self.runs(c2)), (1, 1))
        (self.repo / "README.md").write_text("docs only\n")  # outside Q1's watch
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual((self.runs(c1), self.runs(c2)), (1, 2))
        self.assertTrue(ev["items"][0]["reused"])
        self.assertEqual(ev["items"][0]["reuse_reason"], "no watched path changed")
        self.assertFalse(ev["items"][1]["reused"])
        self.assertEqual(ev["changed_since_previous_run"], ["README.md"])
        (self.repo / "src" / "auth").mkdir(parents=True)
        (self.repo / "src" / "auth" / "limit.py").write_text("x\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual((self.runs(c1), self.runs(c2)), (2, 3))
        self.assertFalse(ev["items"][0]["reused"])
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)

    def test_watch_does_not_reuse_across_marks_or_contract_changes(self):
        c1, cmd1 = self.counter_check("q1", extra="test -f missing.md")
        self.make_contract([{"id": "Q1", "text": "a", "check": cmd1, "watch": ["src/**"]}])
        self.approve(dry_run=False)
        core.run_check(self.repo, "task-1")
        self.assertEqual(self.runs(c1), 1)
        core.set_mark(self.repo, "task-1", "Q1", "blocked", "later")
        ev = core.run_check(self.repo, "task-1")  # marks changed: the previous run is not reused
        self.assertEqual(self.runs(c1), 2)
        self.assertEqual(ev["verdict"], core.VERDICT_INCOMPLETE)

    def test_repo_watch_scopes_repo_checks(self):
        cr, cmdr = self.counter_check("repo_counter")
        c = self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md", "watch": ["src/**"]}], repo_checks=[cmdr])
        c["repo_watch"] = ["src/**", "tests/**"]
        core.write_json(core.task_dir(self.repo, "task-1") / "contract.json", c)
        self.approve(dry_run=False)
        core.run_check(self.repo, "task-1")
        self.assertEqual(self.runs(cr), 1)
        (self.repo / "docs.md").write_text("d\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(self.runs(cr), 1)
        self.assertTrue(ev["repo_checks"][0]["reused"])
        self.assertTrue(ev["reused"])
        (self.repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")  # protected AND watched
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(self.runs(cr), 2)
        self.assertEqual(ev["verdict"], core.VERDICT_TESTS_CHANGED)

    def test_broad_check_detection_and_warnings(self):
        self.assertTrue(core.is_broad_check("pytest -q"))
        self.assertTrue(core.is_broad_check("npm test"))
        self.assertTrue(core.is_broad_check("CI=1 cargo test"))
        self.assertFalse(core.is_broad_check("pytest tests/test_login.py -q"))
        self.assertFalse(core.is_broad_check("cargo test --lib physics"))
        self.assertFalse(core.is_broad_check("grep -q x README.md"))
        c = self.make_contract([{"id": "Q1", "text": "a", "check": "pytest -q"},
                                {"id": "Q2", "text": "b", "check": "pytest tests/test_x.py -q", "watch": ["src/**"]}], repo_checks=["npm test"])
        w = "\n".join(core.lint_warnings(c))
        self.assertIn("Q1: 'pytest -q' runs the whole suite", w)
        self.assertIn("Q1: no watch/cache", w)
        self.assertNotIn("Q2:", w)
        self.assertIn("repo_checks without repo_watch", w)

    def test_approve_dry_run_shows_cost(self):
        self.make_contract([{"id": "Q1", "text": "slow", "check": "sleep 1; test -f README.md"},
                            {"id": "Q2", "text": "fast", "check": "test -f README.md", "watch": ["src/**"]}])
        os.environ["DONE_CONTRACT_SLOW_S"] = "0.5"
        out = io.StringIO()
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        rec = core.approve_contract(self.repo, "task-1", stdout=out)
        text = out.getvalue()
        self.assertIn("SLOW", text)
        self.assertIn("re-run EVERY time the agent stops", text)
        self.assertIn("re-run when these change: src/**", text)
        self.assertIn("dry run: all checks together took", text)
        self.assertGreaterEqual(rec["items"][0]["dry_run_s"], 1.0)
        self.assertTrue(any("Q1: no watch/cache" in w for w in rec["warnings"]))
        out2 = io.StringIO()
        core.write_json(core.task_dir(self.repo, "task-1") / "contract.json", core.load_contract(self.repo, "task-1") | {"request": "again"})
        core.approve_contract(self.repo, "task-1", stdout=out2, dry_run=False)
        self.assertNotIn("dry run:", out2.getvalue())


class TestReviewV04(Base):
    """Regressions for the v0.4 Codex review (F1-F6, L1)."""

    def fresh_tree(self, repo):
        """v0.3-style hash with a throwaway index: the reference the cache must agree with."""
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(td, "index"))
            if core.head_sha(repo):
                core.git(repo, "read-tree", "HEAD", env=env)
            else:
                core.git(repo, "read-tree", "--empty", env=env)
            core.git(repo, "add", "-A", "--", ".", env=env)
            return core.git(repo, "write-tree", env=env).strip()

    def test_same_second_same_size_edit_is_detected(self):
        target = self.repo / "input.txt"
        target.write_text("GOOD\n")
        h1 = core.working_tree_hash(self.repo)
        target.write_text("FAIL\n")  # same size, same second
        time.sleep(1.2)
        h2 = core.working_tree_hash(self.repo)
        self.assertNotEqual(h1, h2)
        self.assertEqual(h2, self.fresh_tree(self.repo))
        st = target.stat()
        target.write_text("GOOD\n")
        os.utime(target, (st.st_atime, st.st_mtime))  # mtime preserved, content changed
        time.sleep(1.2)
        h3 = core.working_tree_hash(self.repo)
        self.assertEqual(h3, h1)
        self.assertEqual(h3, self.fresh_tree(self.repo))

    def test_watch_patterns_dot_prefix_and_class(self):
        for pattern in ("./input.txt", "[i]nput.txt", "src//**", "docs/"):
            self.assertIsNone(core.glob_problem(pattern), pattern)
        self.assertTrue(core.matches_any("input.txt", ["./input.txt"]))
        self.assertTrue(core.matches_any("input.txt", ["[i]nput.txt"]))
        self.assertFalse(core.matches_any("input.txt", ["[!i]nput.txt"]))
        self.assertTrue(core.matches_any("docs/a/b.md", ["docs/"]))
        self.assertTrue(core.matches_any("src/a.py", ["src//**"]))
        self.assertIn("brace", core.glob_problem("src/{a,b}/**"))
        self.assertIn("unbalanced", core.glob_problem("src/[abc"))
        c = self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md", "watch": ["src/{a,b}/**"]}])
        self.assertTrue(any("brace" in p for p in core.lint_contract(c)))
        counter, cmd = Path(self.tmp.name) / "c", f"echo run >> {Path(self.tmp.name) / 'c'}; test -f README.md"
        c = self.make_contract([{"id": "Q1", "text": "a", "check": cmd, "watch": ["./input.txt"]}])
        (self.repo / "input.txt").write_text("GOOD\n")
        self.approve(dry_run=False, accept_dirty=True)
        core.run_check(self.repo, "task-1")
        (self.repo / "input.txt").write_text("BAD INPUT\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertFalse(ev["items"][0]["reused"])
        self.assertEqual(counter.read_text().count("run"), 2)

    def test_close_reruns_everything(self):
        c1 = Path(self.tmp.name) / "c1"
        cr = Path(self.tmp.name) / "cr"
        c = self.make_contract([{"id": "Q1", "text": "a", "check": f"echo run >> {c1}; test -f README.md", "watch": ["src/**"]}],
                               repo_checks=[f"echo run >> {cr}; test -f README.md"])
        c["repo_watch"] = ["src/**"]
        core.write_json(core.task_dir(self.repo, "task-1") / "contract.json", c)
        self.approve(dry_run=False)
        core.run_check(self.repo, "task-1")
        core.run_check(self.repo, "task-1")  # nothing changed: reused
        self.assertEqual((c1.read_text().count("run"), cr.read_text().count("run")), (1, 1))
        ev = core.close_contract(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)
        self.assertFalse(ev["reused"])
        self.assertEqual((c1.read_text().count("run"), cr.read_text().count("run")), (2, 2))

    def test_unborn_repository(self):
        repo = Path(self.tmp.name) / "unborn"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "input.txt").write_text("x\n")
        repo = repo.resolve()
        h = core.working_tree_hash(repo)
        self.assertEqual(h, self.fresh_tree(repo))
        c = core.init_contract(repo, "t", "req")
        self.assertEqual(c["baseline_tree"], h)
        self.assertIsNone(c["baseline_head"])

    def test_new_ignore_rule_rebuilds_the_cache(self):
        (self.repo / "generated.txt").write_text("g\n")
        h_with = core.working_tree_hash(self.repo)
        (self.repo / ".gitignore").write_text("generated.txt\n")
        h_after = core.working_tree_hash(self.repo)
        self.assertEqual(h_after, self.fresh_tree(self.repo))
        self.assertEqual(sorted(core.changed_paths(self.repo, h_with, h_after)), [".gitignore", "generated.txt"])
        self.assertEqual(core.working_tree_hash(self.repo), h_after)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "ignore")  # HEAD change also rebuilds
        self.assertEqual(core.working_tree_hash(self.repo), self.fresh_tree(self.repo))

    def test_reapproval_invalidates_previous_results(self):
        counter = Path(self.tmp.name) / "c"
        self.make_contract([{"id": "Q1", "text": "a", "check": f"echo run >> {counter}; test -f README.md", "watch": ["src/**"]}])
        self.approve(dry_run=False)
        ev1 = core.run_check(self.repo, "task-1")
        self.assertEqual(counter.read_text().count("run"), 1)
        time.sleep(1.1)
        self.approve(dry_run=False, approver="someone-else")
        ev2 = core.run_check(self.repo, "task-1")
        self.assertNotEqual(ev1["approval_id"], ev2["approval_id"])
        self.assertFalse(ev2["items"][0]["reused"])
        self.assertEqual(counter.read_text().count("run"), 2)

    def test_dry_run_shows_failures(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "printf WRONG", "expect": "EXPECTED"},
                            {"id": "Q2", "text": "b", "check": "exit 7"}], repo_checks=["exit 3"])
        out = io.StringIO()
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        core.approve_contract(self.repo, "task-1", stdout=out)
        text = out.getvalue()
        self.assertIn("FAIL(expect mismatch)", text)
        self.assertIn("FAIL(exit 7)", text)
        self.assertIn("FAIL(exit 3)", text)


class TestRedTeam(Base):
    """Cheap bypasses a red-team (Gemini via Cursor) found 2026-10-06. Deleting the control
    dirs turns the gate off, so a destructive op naming one is denied while a contract is active.
    (Module/PATH hijack and HMAC forgery stay out of scope: the documented adversarial boundary.)"""

    def approved(self, **kw):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}], **kw)
        self.approve(dry_run=False)

    def bash(self, cmd, cwd=None):
        out = hooks.pretool({"session_id": "s", "cwd": str(cwd or self.repo), "tool_name": "Bash", "tool_input": {"command": cmd}})
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def test_deleting_or_moving_control_dirs_is_denied(self):
        self.approved()
        for cmd in ("rm -rf .done-contract", "rm -rf .done-contract/", "rmdir .done-contract",
                    "mv .done-contract dc_backup", "mv bak .done-contract", "rm -rf .claude", "rm -rf .claude/",
                    "mv .cursor x", "rm -rf .grok", "cp -r .done-contract /tmp/x && rm -rf .done-contract",
                    "rm -rf x/.done-contract", "rm -rf .done-contract/slugify",
                    # F3: find -delete/-exec, absolute/prefixed verbs
                    "find .claude -delete", "find .done-contract -exec rm {} +", "/bin/rm -rf .claude",
                    "env rm -rf .claude", "command rm -rf .claude", "X=1 rm -rf .claude", "sudo rm -rf .grok",
                    "cp evil.json .claude/settings.json", "ln -sf /dev/null .done-contract/active",
                    # R2: cd into a control dir then act with a relative target
                    "cd .claude && rm -f settings.json", "cd .done-contract && rm -rf slugify",
                    "pushd .claude && rm -f settings.json"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_cd_back_out_is_not_overblocked(self):
        # R3: pushd/popd and cd .. return to the root; a plain file delete there is allowed
        self.approved()
        for cmd in ("pushd .claude && popd && rm scratch.txt", "cd .claude && cd .. && rm foo.txt",
                    "cd subdir && rm foo.txt", "cd .claude && cp settings.json ../backup.json"):
            self.assertIsNone(self.bash(cmd), cmd)

    def test_normal_commands_near_those_names_are_not_blocked(self):
        self.approved()
        for cmd in ("cat .done-contract/active", "ls .claude", "done-contract check",
                    "grep -r pattern .done-contract", "find . -name '*.claude'",
                    # F2: files whose names merely contain the dir name, and reads/backups
                    "rm .claude-notes.md", "rm docs/.grok-example.txt", "rm report.claude.txt",
                    "cp -r .claude backup", "cp app.py backup.py # .grok",
                    "cp app.py backup.py && ls .grok"):
            self.assertIsNone(self.bash(cmd), cmd)

    def test_python_rmtree_of_state_dir_is_denied(self):
        self.approved()
        self.assertEqual(self.bash("python3 -c \"import shutil; shutil.rmtree('.done-contract')\""), "deny")

    def test_forged_evidence_with_swapped_command_is_caught(self):
        # the gate's own check is "test -f missing.md" (FAIL). Forge a PASS evidence whose command
        # was swapped to a passing one and re-sign it. check (cache) is fooled, but verify sees the
        # command no longer matches the contract, and close re-runs the contract's own check.
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md", "cache": True}])
        self.approve(dry_run=False)
        core.run_check(self.repo, "task-1")  # FAIL, writes evidence
        ev = core.load_evidence(self.repo, "task-1")
        ev["verdict"] = "PASS"
        ev["items"][0].update({"status": "PASS", "command": "test -f README.md", "exit": 0, "timed_out": False})
        ev["hmac"] = core.sign_evidence(ev)  # the key is readable by the same account
        core.write_evidence(self.repo, "task-1", ev)
        v = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(v["hmac_valid"])        # forgery is correctly signed
        self.assertFalse(v["matches_contract"])  # but the command is not the contract's
        self.assertFalse(v["ok"])
        with self.assertRaises(core.DoneContractError):
            core.close_contract(self.repo, "task-1")  # close re-runs the contract check -> FAIL

    def test_forged_repo_check_duplicate_is_caught_and_real_duplicates_pass(self):
        # R1: swapping a failing repo_check for a copy of a passing one must not reproduce.
        c = self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}],
                               repo_checks=["test -f README.md", "test -f missing.md"])
        self.approve(dry_run=False)
        core.run_check(self.repo, "task-1")  # FAIL (repo check 2 fails)
        ev = core.load_evidence(self.repo, "task-1")
        ev["verdict"] = "PASS"
        ev["repo_checks"][1] = dict(ev["repo_checks"][0])  # replace the failing row with the passing one
        for rc in ev["repo_checks"]:
            rc["status"] = "PASS"
        ev["hmac"] = core.sign_evidence(ev)
        core.write_evidence(self.repo, "task-1", ev)
        v = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(v["hmac_valid"])
        self.assertFalse(v["matches_contract"])  # multiset differs
        self.assertFalse(v["ok"])
        # and a legitimately duplicated repo_checks contract verifies cleanly
        c2 = self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}],
                                repo_checks=["test -f README.md", "test -f README.md"], task="dup",
                                abandon_reason="switch to dup test")
        self.approve(task="dup", dry_run=False)
        core.run_check(self.repo, "dup")
        v2 = core.verify_evidence(self.repo, "dup")
        self.assertTrue(v2["matches_contract"])
        self.assertTrue(v2["ok"])


class TestMultiCli(Base):
    """Payload shapes measured from Grok 1.0.46 and cursor-agent 2026.10.01 (probe, 2026-10-06)."""

    def approved(self, **kw):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}], **kw)
        self.approve(dry_run=False)

    def decide(self, payload):
        out = hooks.pretool(payload)
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def test_grok_payload_shapes(self):
        self.approved()
        base = {"sessionId": "g1", "session_id": "g1", "cwd": str(self.repo), "workspaceRoot": str(self.repo) + "/"}
        self.assertEqual(self.decide({**base, "toolName": "write", "toolInput": {"file_path": str(self.repo / "tests" / "test_a.py"), "content": "x"}}), "deny")
        self.assertEqual(self.decide({**base, "tool_name": "search_replace", "tool_input": {"file_path": "tests/test_a.py"}}), "deny")
        self.assertEqual(self.decide({**base, "tool_name": "run_terminal_command", "tool_input": {"command": "rm tests/test_a.py"}}), "ask")
        self.assertIsNone(self.decide({**base, "tool_name": "write", "tool_input": {"file_path": str(self.repo / "app.py")}}))
        out = hooks.stop({**base, "stopHookActive": False, "reason": "end_turn"})
        self.assertEqual(out["decision"], "block")
        self.assertIsNone(hooks.stop({**base, "stopHookActive": False, "reason": "shutdown"}))  # session close: no check, no block count
        state = hooks._load_state(f"g1__task-1__{core.contract_sha(core.load_contract(self.repo, 'task-1'))[:16]}")
        self.assertEqual(state["blocks"], 1)

    def test_cursor_payload_shapes(self):
        self.approved()
        base = {"session_id": "c1", "conversation_id": "c1", "cwd": str(self.repo), "workspace_roots": [str(self.repo)]}
        self.assertEqual(self.decide({**base, "tool_name": "Write", "tool_input": {"file_path": str(self.repo / "tests" / "test_a.py")}}), "deny")
        self.assertEqual(self.decide({**base, "tool_name": "Delete", "tool_input": {"path": str(self.repo / "tests" / "test_a.py")}}), "deny")
        self.assertEqual(self.decide({**base, "tool_name": "Shell", "tool_input": {"command": "rm tests/test_a.py", "cwd": str(self.repo)}}), "ask")
        self.assertIsNone(self.decide({**base, "tool_name": "Read", "tool_input": {"file_path": str(self.repo / "tests" / "test_a.py")}}))

    def test_hook_config_is_protected_while_a_contract_is_approved(self):
        def d(tool, **inp):
            return self.decide({"session_id": "s", "cwd": str(self.repo), "tool_name": tool, "tool_input": inp})
        self.assertIsNone(d("Edit", file_path=".claude/settings.json"))  # no contract yet: not our business
        self.approved()
        self.assertEqual(d("Edit", file_path=".claude/settings.json"), "deny")
        self.assertEqual(d("Write", file_path=".claude/settings.local.json"), "deny")
        self.assertEqual(d("Write", file_path=".cursor/hooks.json"), "deny")
        self.assertEqual(d("Write", file_path=".grok/hooks/x.json"), "deny")
        self.assertEqual(d("Bash", command="sed -i 's/hook stop//' .claude/settings.json"), "deny")
        self.assertEqual(d("Bash", command="python3 -c \"import json; open('.claude/settings.local.json','w').write('{\\\"disableAllHooks\\\": true}')\""), "deny")
        self.assertEqual(d("Bash", command="echo '{\"disableAllHooks\": true}' > ~/.claude/settings.json"), "deny")
        self.assertIsNone(d("Bash", command="cat .claude/settings.json"))
        self.assertIsNone(d("Edit", file_path="app.py"))


class TestReviewV043(Base):
    """Regressions for the v0.4.3 review (F1-F4) and the residual cases it listed."""

    def second_repo(self, name="b"):
        b = Path(self.tmp.name) / name
        b.mkdir()
        git(b, "init", "-q", "-b", "main")
        git(b, "config", "user.email", "t@example.com")
        git(b, "config", "user.name", "t")
        (b / "src").mkdir()
        (b / "src" / "app.py").write_text("x\n")
        git(b, "add", "-A")
        git(b, "commit", "-q", "-m", "init")
        return b.resolve()

    def approved(self, **kw):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}], **kw)
        self.approve(dry_run=False, accept_dirty=True)

    def edit(self, fp, cwd=None, **kw):
        out = hooks.pretool({"session_id": "s", "cwd": str(cwd or self.repo), "tool_name": "Edit", "tool_input": {"file_path": str(fp)}}, **kw)
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def bash(self, cmd, cwd=None, **kw):
        out = hooks.pretool({"session_id": "s", "cwd": str(cwd or self.repo), "tool_name": "Bash", "tool_input": {"command": cmd}}, **kw)
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def test_f1_symlinked_dir_and_dangling_link_keep_their_own_repo(self):
        b = self.second_repo()
        (self.repo / "tests" / "link").symlink_to(b / "src")
        (self.repo / "tests" / "dangling.py").symlink_to(b / "src" / "not-created.py")
        self.approved()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)
        self.assertEqual(self.edit(self.repo / "tests" / "link" / "app.py"), "deny")
        self.assertEqual(self.edit(self.repo / "tests" / "dangling.py"), "deny")
        (self.repo / "vendor").symlink_to(b)  # a link to another repository's top
        self.assertIsNone(self.edit(self.repo / "vendor" / "src" / "app.py"))  # not a protected path of A

    def test_f1b_nested_repository_behind_a_link(self):
        b = self.second_repo()
        nested = b / "src" / "module"
        nested.mkdir()
        git(nested, "init", "-q", "-b", "main")
        (nested / "src").mkdir()
        (nested / "src" / "app.py").write_text("x\n")
        (self.repo / "tests" / "link").symlink_to(b / "src")
        self.approved()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)
        target = self.repo / "tests" / "link" / "module" / "src" / "app.py"
        self.assertEqual(hooks._lexical_repo(target), self.repo)
        self.assertEqual(self.edit(target), "deny")
        self.assertEqual(hooks._lexical_repo(self.repo / "README.md"), self.repo)  # ordinary path, ancestors may hold OS symlinks

    def test_quoted_cd_path(self):
        spaced = Path(self.tmp.name) / "space project"
        spaced.mkdir()
        git(spaced, "init", "-q", "-b", "main")
        git(spaced, "config", "user.email", "t@example.com")
        git(spaced, "config", "user.name", "t")
        (spaced / "tests").mkdir()
        (spaced / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
        git(spaced, "add", "-A")
        git(spaced, "commit", "-q", "-m", "init")
        spaced = spaced.resolve()
        c = core.init_contract(spaced, "t", "x")
        c["items"] = [{"id": "Q1", "text": "a", "check": "test -d tests"}]
        core.write_json(core.task_dir(spaced, "t") / "contract.json", c)
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        core.approve_contract(spaced, "t", stdout=io.StringIO(), dry_run=False)
        elsewhere = self.second_repo("elsewhere")
        os.environ["CLAUDE_PROJECT_DIR"] = str(spaced)
        self.assertEqual(self.bash(f"cd '{spaced}' && rm tests/test_a.py", cwd=elsewhere), "ask")

    def test_f2_ignored_alias_never_hides_a_tracked_test(self):
        (self.repo / ".gitignore").write_text("tests/ignored-link.py\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "ignore")
        (self.repo / "tests" / "ignored-link.py").symlink_to(self.repo / "tests" / "test_a.py")
        self.approved()
        self.assertEqual(self.bash("echo changed > tests/ignored-link.py"), "ask")

    def test_f3_an_error_in_one_repository_keeps_another_repositorys_block(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve(dry_run=False)
        b = self.second_repo()
        core.init_contract(b, "broken", "x")
        (core.task_dir(b, "broken") / "contract.json").write_text("{broken")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)
        code, out = hooks.run_hook("stop", json.dumps({"session_id": "s", "cwd": str(b), "stop_hook_active": False}))
        res = json.loads(out)
        self.assertEqual(res["decision"], "block")
        self.assertIn("done-contract ERROR", res["reason"])
        os.environ["CLAUDE_PROJECT_DIR"] = str(b)  # other order: broken project, failing cwd repo
        code, out = hooks.run_hook("stop", json.dumps({"session_id": "s2", "cwd": str(self.repo), "stop_hook_active": False}))
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_f4_project_policy_does_not_reach_another_approved_repo(self):
        project = self.second_repo("project")
        self.approved()  # self.repo is another repository with an approved contract
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        self.assertIsNone(self.bash("touch src/app.py", cwd=self.repo, require_contract=True))
        self.assertEqual(self.bash(f"touch {project}/src/new.py", cwd=self.repo, require_contract=True), "deny")
        self.assertEqual(self.bash(f"cd {project} && touch x.py", cwd=self.repo, require_contract=True), "deny")
        self.assertEqual(self.bash("touch x.py", cwd=project, require_contract=True), "deny")

    def test_residual_cd_inside_a_command_is_followed(self):
        self.approved()
        b = self.second_repo()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)
        self.assertEqual(self.bash(f"cd {self.repo} && rm tests/test_a.py", cwd=b), "ask")

    def test_residual_directory_token_covers_tracked_files_below(self):
        self.approved()
        self.assertEqual(self.bash("rm -rf tests/"), "ask")
        self.assertEqual(self.bash("rm -rf tests"), "ask")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "a.py").write_text("x\n")
        self.assertIsNone(self.bash("rm -rf src"))

    def test_residual_installed_project_path_works_without_env(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve(dry_run=False)
        b = self.second_repo()
        code, out = hooks.run_hook("stop", json.dumps({"session_id": "s", "cwd": str(b), "stop_hook_active": False}), project=str(self.repo))
        self.assertEqual(json.loads(out)["decision"], "block")
        snippet = hook_snippet("/x/done-contract", project="/a b/repo")
        self.assertIn("--project '/a b/repo'", snippet["hooks"]["Stop"][0]["hooks"][0]["command"])

    def test_one_budget_for_all_repositories(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "sleep 2", "timeout": 10}])
        self.approve(dry_run=False)
        b = self.second_repo()
        c = core.init_contract(b, "t2", "x")
        c["items"] = [{"id": "Q1", "text": "b", "check": "sleep 2", "timeout": 10}]
        core.write_json(core.task_dir(b, "t2") / "contract.json", c)
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        core.approve_contract(b, "t2", stdout=io.StringIO(), dry_run=False)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)
        os.environ["DONE_CONTRACT_STOP_BUDGET"] = "2.5"
        started = time.monotonic()
        out = hooks.stop({"session_id": "s", "cwd": str(b), "stop_hook_active": False})
        self.assertLess(time.monotonic() - started, 4.0)  # not 2 x 2.5 s
        self.assertEqual(out["decision"], "block")
        self.assertIn("ERROR", out["reason"])


class TestCwdIndependence(Base):
    """Live test T5: the agent `cd`ed out of the repository and the hooks judged the wrong one."""

    def other_repo(self):
        o = Path(self.tmp.name) / "other"
        o.mkdir()
        git(o, "init", "-q", "-b", "main")
        return o.resolve()

    def test_stop_follows_project_dir_when_cwd_moves(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve(dry_run=False)
        elsewhere = self.other_repo()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)
        out = hooks.stop({"session_id": "s", "cwd": str(elsewhere), "stop_hook_active": False})
        self.assertEqual(out["decision"], "block")
        out = hooks.stop({"session_id": "s2", "cwd": self.tmp.name, "stop_hook_active": False})  # not a repo at all
        self.assertEqual(out["decision"], "block")

    def test_edit_is_judged_by_the_target_files_repo(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve(dry_run=False)
        elsewhere = self.other_repo()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)

        def d(fp, **kw):
            out = hooks.pretool({"session_id": "s", "cwd": str(elsewhere), "tool_name": "Edit", "tool_input": {"file_path": fp}}, **kw)
            return out["hookSpecificOutput"]["permissionDecision"] if out else None

        self.assertEqual(d(str(self.repo / "tests" / "test_a.py")), "deny")  # protected file, shell elsewhere
        self.assertIsNone(d(str(self.repo / "app.py"), require_contract=True))  # the T5 false denial
        self.assertIsNone(d(str(elsewhere / "x.py"), require_contract=True))  # another repo is outside this project's policy
        self.assertEqual(d(str(self.repo / ".done-contract" / "task-1" / "contract.json")), "deny")

    def test_bash_is_judged_for_the_project_even_from_elsewhere(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve(dry_run=False)
        elsewhere = self.other_repo()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.repo)

        def d(cmd, **kw):
            out = hooks.pretool({"session_id": "s", "cwd": str(elsewhere), "tool_name": "Bash", "tool_input": {"command": cmd}}, **kw)
            return out["hookSpecificOutput"]["permissionDecision"] if out else None

        self.assertEqual(d(f"rm {self.repo}/tests/test_a.py"), "ask")
        self.assertIsNone(d(f"cd {self.repo} && done-contract status", require_contract=True))  # approved project: no policy
        self.assertIsNone(d("rm conftest.py"))  # bare name while outside the repo: not this repo's file
        self.assertEqual(d("done-contract approve"), "deny")

    def test_without_project_dir_edits_still_use_the_target_repo(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve(dry_run=False)
        elsewhere = self.other_repo()
        out = hooks.pretool({"session_id": "s", "cwd": str(elsewhere), "tool_name": "Write",
                             "tool_input": {"file_path": str(self.repo / "tests" / "test_new.py")}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_ignored_build_output_under_tests_is_not_flagged(self):
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "ignore")
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve(dry_run=False)
        (self.repo / "tests" / "__pycache__").mkdir()
        (self.repo / "tests" / "__pycache__" / "test_a.cpython-314.pyc").write_bytes(b"x")

        def d(cmd):
            out = hooks.pretool({"session_id": "s", "cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": cmd}})
            return out["hookSpecificOutput"]["permissionDecision"] if out else None

        self.assertIsNone(d("python3 -m unittest -q; rm -rf tests/__pycache__"))
        self.assertEqual(d("rm tests/test_a.py"), "ask")
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_PASS)  # ignored pyc is not a protected change


class TestReviewV04b(Base):
    """Regressions for the v0.4.1 delta review (B1-B3, L2, L3) and the cache self-check."""

    def test_ignore_stat_setting_does_not_hide_changes(self):
        git(self.repo, "config", "core.ignoreStat", "true")
        target = self.repo / "input.txt"
        target.write_text("GOOD\n")
        h1 = core.working_tree_hash(self.repo)
        target.write_text("FAIL DIFFERENT SIZE\n")
        h2 = core.working_tree_hash(self.repo)
        self.assertNotEqual(h1, h2)
        self.assertEqual(h2, core.fresh_tree_hash(self.repo))

    def test_cache_key_covers_every_ignore_source(self):
        # 1. a .gitignore that is itself excluded still contributes rules
        exclude = Path(core.git(self.repo, "rev-parse", "--git-path", "info/exclude").strip())
        exclude = exclude if exclude.is_absolute() else self.repo / exclude
        (self.repo / "generated.txt").write_text("g\n")
        h0 = core.working_tree_hash(self.repo)
        with open(exclude, "a") as fh:
            fh.write(".gitignore\n")
        (self.repo / ".gitignore").write_text("generated.txt\n")
        h1 = core.working_tree_hash(self.repo)
        self.assertEqual(h1, core.fresh_tree_hash(self.repo))
        self.assertEqual(core.changed_paths(self.repo, h0, h1), ["generated.txt"])
        # 2. core.excludesFile given relative to the repo, caller cwd elsewhere
        (self.repo / ".gitignore").unlink()
        git(self.repo, "config", "core.excludesFile", "local-ignore")
        (self.repo / "other.txt").write_text("o\n")
        h2 = core.working_tree_hash(self.repo)
        (self.repo / "local-ignore").write_text("other.txt\n")
        h3 = core.working_tree_hash(self.repo)
        self.assertEqual(h3, core.fresh_tree_hash(self.repo))
        self.assertIn("other.txt", core.changed_paths(self.repo, h2, h3))
        # 3. git's default global ignore file when core.excludesFile is unset
        git(self.repo, "config", "--unset", "core.excludesFile")
        xdg = Path(self.tmp.name) / "xdg"
        (xdg / "git").mkdir(parents=True)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        (self.repo / "third.txt").write_text("t\n")
        h4 = core.working_tree_hash(self.repo)
        (xdg / "git" / "ignore").write_text("third.txt\n")
        h5 = core.working_tree_hash(self.repo)
        self.assertEqual(h5, core.fresh_tree_hash(self.repo))
        self.assertIn("third.txt", core.changed_paths(self.repo, h4, h5))

    def test_cache_file_name_is_its_generation(self):
        root = core.contract_root(self.repo)
        core.working_tree_hash(self.repo)
        caches = sorted(p.name for p in root.glob(".index.*"))
        self.assertEqual(len(caches), 1)
        stale = root / ".index.deadbeefdeadbeefdeadbeefdeadbeef"
        stale.write_bytes(b"garbage")  # a cache of another generation is never read and gets removed
        h = core.working_tree_hash(self.repo)
        self.assertEqual(h, core.fresh_tree_hash(self.repo))
        self.assertFalse(stale.exists())
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "move HEAD")
        core.working_tree_hash(self.repo)
        self.assertEqual(len(list(root.glob(".index.*"))), 1)  # old generation dropped
        self.assertNotIn(caches[0], [p.name for p in root.glob(".index.*")])

    def test_cache_self_check_prefers_fresh_value(self):
        core.working_tree_hash(self.repo)
        with mock.patch.object(core, "fresh_tree_hash", return_value="0" * 40):
            h = core.working_tree_hash(self.repo, verify_cache=True)
        self.assertEqual(h, "0" * 40)
        self.assertEqual(list(core.contract_root(self.repo).glob(".index.*")), [])
        log = (self.home / "log.jsonl").read_text()
        self.assertIn("index_cache_mismatch", log)
        self.assertEqual(core.working_tree_hash(self.repo, verify_cache=True), core.fresh_tree_hash(self.repo))

    def test_lint_rejects_patterns_that_do_not_compile(self):
        import warnings
        self.assertIsNotNone(core.glob_problem("[!]"))
        self.assertIsNotNone(core.glob_problem("[z-a]"))
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # Python's "possible nested set" FutureWarning must not fire
            self.assertIsNone(core.glob_problem("[[]literal"))
            self.assertTrue(core.matches_any("[literal", ["[[]literal"]))

    def test_cache_removed_between_check_and_copy_is_a_miss(self):
        core.working_tree_hash(self.repo)
        real_copy2 = shutil.copy2
        calls = {"n": 0}

        def flaky_copy2(src, dst, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                os.unlink(src)  # a concurrent generation cleanup wins the race
                raise FileNotFoundError(src)
            return real_copy2(src, dst, *a, **k)

        with mock.patch.object(core.shutil, "copy2", side_effect=flaky_copy2):
            h = core.working_tree_hash(self.repo)
        self.assertEqual(h, core.fresh_tree_hash(self.repo))
        self.assertEqual(len(list(core.contract_root(self.repo).glob(".index.*"))), 1)

    def test_same_second_reapproval_is_a_new_approval(self):
        counter = Path(self.tmp.name) / "c"
        self.make_contract([{"id": "Q1", "text": "a", "check": f"echo run >> {counter}; test -f README.md", "watch": ["src/**"]}])
        a1 = self.approve(dry_run=False)
        ev1 = core.run_check(self.repo, "task-1")
        a2 = self.approve(dry_run=False)  # same contract, same approver, same second
        self.assertNotEqual(a1["nonce"], a2["nonce"])
        ev2 = core.run_check(self.repo, "task-1")
        self.assertNotEqual(ev1["approval_id"], ev2["approval_id"])
        self.assertFalse(ev2["items"][0]["reused"])
        self.assertEqual(counter.read_text().count("run"), 2)


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
