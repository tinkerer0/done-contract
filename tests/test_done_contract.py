import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
            return core.approve_contract(self.repo, task, **kw)
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

    def test_strength_and_cacheability(self):
        self.assertEqual(core.strength_of("pytest -q tests"), "test")
        self.assertEqual(core.strength_of("npm test"), "test")
        self.assertEqual(core.strength_of("cargo test --lib physics"), "test")
        self.assertEqual(core.strength_of("test -f docs/x.md"), "existence")
        self.assertEqual(core.strength_of("grep -q RATE docs/x.md"), "content")
        self.assertEqual(core.strength_of("curl -fsS http://localhost:8080/health"), "http")
        self.assertEqual(core.strength_of("./scripts/smoke.sh"), "other")
        self.assertTrue(core.item_cacheable({"check": "pytest -q"}))
        self.assertFalse(core.item_cacheable({"check": "curl -f http://x"}))
        self.assertFalse(core.item_cacheable({"check": "./scripts/smoke.sh"}))
        self.assertFalse(core.item_cacheable({"check": "pytest -q", "cache": False}))

    def test_globs(self):
        self.assertTrue(core.matches_any("tests/test_x.py", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("tests/a/b/test_x.py", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("src/foo_test.go", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("web/app.spec.ts", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("pkg/__tests__/x.js", core.DEFAULT_PROTECTED))
        self.assertTrue(core.matches_any("./tests/x.py", core.DEFAULT_PROTECTED))
        self.assertFalse(core.matches_any("src/app.py", core.DEFAULT_PROTECTED))
        self.assertFalse(core.matches_any("docs/testing.md", core.DEFAULT_PROTECTED))


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

    def test_done_contract_dir_is_excluded(self):
        core.init_contract(self.repo, "task-1", "r")
        t0 = core.working_tree_hash(self.repo)
        (self.repo / ".done-contract" / "scratch.txt").write_text("x")
        self.assertEqual(t0, core.working_tree_hash(self.repo))


class TestApproval(Base):
    def test_approve_requires_tty(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        with self.assertRaises(core.NotInteractive):
            core.approve_contract(self.repo, "task-1")

    def test_approve_refuses_lint_failure(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "true"}])
        os.environ["DONE_CONTRACT_APPROVE_NO_TTY"] = "1"
        with self.assertRaises(core.DoneContractError):
            core.approve_contract(self.repo, "task-1")

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


class TestCheck(Base):
    def test_fail_then_pass_and_reuse(self):
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
        self.assertFalse(ev2["reused"])
        self.assertEqual(ev2["hmac"], core.sign_evidence(ev2))
        ev3 = core.run_check(self.repo, "task-1")
        self.assertTrue(ev3["reused"])
        self.assertTrue(all(i["reused"] for i in ev3["items"]))
        self.assertTrue((core.task_dir(self.repo, "task-1") / "evidence.md").exists())

    def test_external_checks_are_never_cached(self):
        marker = self.repo.parent / "service_up"
        marker.write_text("1")
        self.make_contract([{"id": "Q1", "text": "svc", "check": f"curl --version >/dev/null 2>&1; test -f {marker}"},
                            {"id": "Q2", "text": "local", "check": "test -f README.md"}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)
        marker.unlink()  # external state changes, tree does not
        ev2 = core.run_check(self.repo, "task-1")
        self.assertEqual(ev2["items"][0]["status"], "FAIL")
        self.assertFalse(ev2["items"][0]["reused"])
        self.assertTrue(ev2["items"][1]["reused"])
        self.assertEqual(ev2["verdict"], core.VERDICT_FAIL)

    def test_expect_substring(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "cat README.md", "expect": "hello"},
                            {"id": "Q2", "text": "b", "check": "cat README.md", "expect": "nope"}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual([i["status"] for i in ev["items"]], ["PASS", "FAIL"])
        self.assertFalse(ev["items"][1]["expect_matched"])

    def test_timeout_is_fail_and_kills_children(self):
        self.make_contract([{"id": "Q1", "text": "slow", "check": "sleep 30 & sleep 30", "timeout": 1}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_FAIL)
        self.assertTrue(ev["items"][0]["timed_out"])
        self.assertLess(ev["items"][0]["duration_s"], 10)

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
        # a blocked item that actually passes counts as PASS
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

    def test_protected_changes_allowed(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}], allow_protected_changes=True)
        self.approve()
        (self.repo / "tests" / "test_b.py").write_text("def test_b():\n    pass\n")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_PASS)
        self.assertEqual(ev["protected_changed"], ["tests/test_b.py"])

    def test_repo_checks(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}], repo_checks=["test -f nope"])
        self.approve()
        self.assertEqual(core.run_check(self.repo, "task-1")["verdict"], core.VERDICT_FAIL)

    def test_stale_when_check_modifies_tree(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "touch generated.txt"}])
        self.approve()
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_STALE)
        self.assertEqual(ev["stale_paths"], ["generated.txt"])
        ev2 = core.run_check(self.repo, "task-1")  # file now exists before the run -> no change during the run
        self.assertEqual(ev2["verdict"], core.VERDICT_PASS)

    def test_budget_marks_unrun_items_error(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "sleep 1"},
                            {"id": "Q2", "text": "b", "check": "test -f README.md"}])
        self.approve()
        ev = core.run_check(self.repo, "task-1", budget_s=0.5)
        self.assertEqual(ev["items"][1]["status"], "ERROR")
        self.assertEqual(ev["verdict"], core.VERDICT_ERROR)
        ev2 = core.run_check(self.repo, "task-1")  # ERROR evidence is never reused
        self.assertEqual(ev2["verdict"], core.VERDICT_PASS)
        self.assertFalse(ev2["items"][0]["reused"])

    def test_close_requires_current_evidence(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"},
                            {"id": "Q2", "text": "b", "check": "test -f missing.md"}])
        self.approve()
        with self.assertRaises(core.DoneContractError):
            core.close_contract(self.repo, "task-1")
        core.set_mark(self.repo, "task-1", "Q2", "blocked", "cannot")
        ev = core.run_check(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_INCOMPLETE)
        core.set_mark(self.repo, "task-1", "Q2", "open", None)  # marks changed after the evidence
        with self.assertRaises(core.DoneContractError):
            core.close_contract(self.repo, "task-1")
        core.set_mark(self.repo, "task-1", "Q2", "blocked", "cannot")
        core.run_check(self.repo, "task-1")
        ev = core.close_contract(self.repo, "task-1")
        self.assertEqual(ev["verdict"], core.VERDICT_INCOMPLETE)
        self.assertIsNone(core.active_task(self.repo))
        self.assertEqual(core.load_marks(self.repo, "task-1")["closed_verdict"], core.VERDICT_INCOMPLETE)
        self.assertEqual(core.contract_state(self.repo, "task-1"), "closed")

    def test_verify(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        core.run_check(self.repo, "task-1")
        v = core.verify_evidence(self.repo, "task-1")
        self.assertTrue(v["agree"] and v["hmac_valid"] and v["same_tree"])
        (self.repo / "README.md").unlink()
        v2 = core.verify_evidence(self.repo, "task-1")
        self.assertFalse(v2["agree"])
        self.assertFalse(v2["same_tree"])

    def test_init_needs_abandon_reason_to_replace_approved_contract(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.approve()
        with self.assertRaises(core.DoneContractError):
            core.init_contract(self.repo, "task-2", "another")
        core.init_contract(self.repo, "task-2", "another", abandon_reason="user changed direction")
        self.assertEqual(core.active_task(self.repo), "task-2")
        self.assertEqual(core.contract_state(self.repo, "task-1"), "abandoned")
        self.assertEqual(core.load_marks(self.repo, "task-1")["abandoned"]["reason"], "user changed direction")
        with self.assertRaises(core.DoneContractError):
            core.init_contract(self.repo, "task-1", "again")  # abandoned slugs are not reused


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
        # stopping again without a fix is blocked again (no shortcut)
        out2 = hooks.stop(self.payload(stop_hook_active=True))
        self.assertEqual(out2["decision"], "block")
        self.assertIn("2/3", out2["reason"])
        (self.repo / "missing.md").write_text("x")
        out3 = hooks.stop(self.payload(stop_hook_active=True))
        self.assertNotIn("decision", out3)
        self.assertIn("PASS", out3["systemMessage"])

    def test_block_cap_releases_without_success_receipt(self):
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
            core.close_contract(self.repo, "task-1")  # released evidence is FAIL, so no close

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
        core.run_check(self.repo, "task-1")
        core.close_contract(self.repo, "task-1")
        self.make_contract([{"id": "Q1", "text": "b", "check": "test -f missing2.md"}], task="task-2")
        self.approve(task="task-2")
        self.assertEqual(hooks.stop(self.payload())["decision"], "block")

    def test_error_verdict_blocks(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "sleep 1"}, {"id": "Q2", "text": "b", "check": "test -f README.md"}])
        self.approve()
        os.environ["DONE_CONTRACT_STOP_BUDGET"] = "0.2"
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
    def payload(self, tool, **inp):
        return {"session_id": "s", "cwd": str(self.repo), "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp}

    def decision(self, tool, **inp):
        out = hooks.pretool(self.payload(tool, **inp))
        return out["hookSpecificOutput"]["permissionDecision"] if out else None

    def test_inactive_before_approval_without_policy(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f README.md"}])
        self.assertIsNone(hooks.pretool(self.payload("Edit", file_path=str(self.repo / "tests" / "test_a.py"))))

    def test_require_contract_policy(self):
        p = lambda tool, **inp: hooks.pretool(self.payload(tool, **inp), require_contract=True)
        d = lambda tool, **inp: (p(tool, **inp) or {}).get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(d("Edit", file_path="src/app.py"), "deny")
        self.assertEqual(d("Bash", command="echo x > src/app.py"), "deny")
        self.assertIsNone(p("Bash", command="cat src/app.py"))
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
    def run_cli(self, *args, env=None, stdin=None):
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run([sys.executable, str(BIN), "--repo", str(self.repo), *args], capture_output=True, text=True, env=e, input=stdin)

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
        self.assertEqual(r.returncode, 4, r.stderr)  # INCOMPLETE is not success
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

    def test_hook_stop_via_cli(self):
        self.make_contract([{"id": "Q1", "text": "a", "check": "test -f missing.md"}])
        self.approve()
        payload = {"session_id": "cli-s", "cwd": str(self.repo), "stop_hook_active": False}
        r = self.run_cli("hook", "stop", stdin=json.dumps(payload))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_hook_install_merge(self):
        snippet = hook_snippet("/x/done-contract", require_contract=True)
        self.assertIn("--require-contract", snippet["hooks"]["Stop"][0]["hooks"][0]["command"])
        settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
        merged = merge_hooks(settings, snippet)
        self.assertEqual(len(merged["hooks"]["Stop"]), 2)
        self.assertEqual(len(merged["hooks"]["PreToolUse"]), 1)
        merged = merge_hooks(merged, snippet)
        self.assertEqual(len(merged["hooks"]["Stop"]), 2)  # idempotent
        self.assertEqual(merged["hooks"]["Stop"][1]["hooks"][0]["timeout"], 900)
        r = self.run_cli("hook", "install", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        written = json.loads((self.repo / ".claude" / "settings.json").read_text())
        self.assertIn("hook stop", written["hooks"]["Stop"][0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
