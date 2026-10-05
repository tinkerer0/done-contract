"""done-contract command line."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import core, hooks

HOOK_EVENTS = {
    "Stop": {"timeout": 900},
    "PreToolUse": {"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "timeout": 30},
}


def _repo(args) -> Path:
    return core.repo_root(Path(getattr(args, "repo", None) or os.getcwd()))


def _read_request(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def cmd_init(args) -> int:
    repo = _repo(args)
    items = json.loads(Path(args.items).read_text(encoding="utf-8")) if args.items else None
    contract = core.init_contract(
        repo, args.task, _read_request(args.request), abandon_reason=args.abandon_reason, items=items,
        repo_checks=args.repo_check or None, protected=args.protected or None,
        allow_protected_changes=args.allow_protected_changes)
    path = core.task_dir(repo, args.task) / "contract.json"
    print(f"contract created: {path}")
    print("next: fill items[] (id, text, check[, expect, timeout, cache]) in that file, then ask a person to run `done-contract approve`")
    if not contract["items"]:
        print("items: (empty) — every requirement needs an id, text and an executable check that fails when it is not done")
    return 0


def cmd_approve(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    rec = core.approve_contract(repo, task, approver=args.approver, assume_yes=args.yes, accept_dirty=args.accept_dirty)
    print(f"approved {task} sha256={rec['sha256']} at {rec['approved_at']}")
    return 0


def _print_evidence(ev: dict, args) -> None:
    if getattr(args, "json", False):
        print(json.dumps(ev, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(core.render_evidence_md(ev), end="")


def cmd_check(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    ev = core.run_check(repo, task, reuse=not args.no_reuse, budget_s=args.budget)
    _print_evidence(ev, args)
    return core.EXIT_CODES.get(ev["verdict"], 5)


def cmd_mark(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    marks = core.set_mark(repo, task, args.item, args.status, args.reason)
    print(json.dumps(marks["items"], ensure_ascii=False, indent=2))
    return 0


def cmd_pause(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    core.set_paused(repo, task, args.reason)
    print(f"paused {task}: {args.reason}")
    return 0


def cmd_resume(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    core.set_paused(repo, task, None)
    print(f"resumed {task}")
    return 0


def cmd_status(args) -> int:
    repo = _repo(args)
    task = args.task or core.active_task(repo)
    if not task:
        print("no active contract")
        return 0
    contract = core.load_contract(repo, task)
    approval = core.find_approval(repo, contract)
    marks = core.load_marks(repo, task)
    ev = core.load_evidence(repo, task)
    if args.json:
        print(json.dumps({"task": task, "state": core.contract_state(repo, task), "contract": contract, "approval": approval,
                          "marks": marks, "evidence": ev}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(core.render_contract_summary(contract))
    print(f"contract sha256: {core.contract_sha(contract)}")
    print(f"state: {core.contract_state(repo, task)}")
    print(f"approval: {'yes, ' + approval['approved_at'] + ' by ' + approval.get('approver', '?') if approval else 'NO (a person runs done-contract approve)'}")
    for iid, m in marks.get("items", {}).items():
        print(f"mark {iid}: {m.get('status')} — {m.get('reason')}")
    if marks.get("paused"):
        print(f"paused: {marks['paused'].get('reason')}")
    if marks.get("abandoned"):
        print(f"abandoned: {marks['abandoned'].get('reason')} ({marks['abandoned'].get('at')})")
    if marks.get("closed_at"):
        print(f"closed_at: {marks['closed_at']} with verdict {marks.get('closed_verdict')}")
    if ev:
        print(f"last evidence: {ev.get('verdict')} at {ev.get('checked_at')} tree {str(ev.get('tree'))[:12]}"
              + (" (in progress / interrupted)" if ev.get("in_progress") else "")
              + (f"  released: {ev['released'].get('reason')}" if ev.get("released") else ""))
    else:
        print("last evidence: none")
    return 0


def cmd_close(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    ev = core.close_contract(repo, task)
    print(f"closed {task} with verdict {ev['verdict']}" + ("" if ev["verdict"] == core.VERDICT_PASS else " (NOT complete)"))
    return 0 if ev["verdict"] == core.VERDICT_PASS else 4


def cmd_verify(args) -> int:
    repo = _repo(args)
    task = core.resolve_task(repo, args.task)
    result = core.verify_evidence(repo, task)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"evidence tree {str(result['evidence_tree'])[:12]} current {str(result['current_tree'])[:12]} "
              f"same_tree={result['same_tree']} hmac_valid={result['hmac_valid']} current={result['current']}")
        for row in result["rows"]:
            print(f"  {row['id']}: recorded {row['recorded']} now {row['now']} {'OK' if row['agree'] else 'MISMATCH'}")
        print("ok: reproduced and current" if result["ok"] else ("reproduced but NOT current" if result["agree"] and result["hmac_valid"] else "MISMATCH"))
    return 0 if result["ok"] else 1


def hook_snippet(bin_path: str, require_contract: bool = False) -> dict:
    flag = " --require-contract" if require_contract else ""
    quoted = core.shell_quote(bin_path)
    return {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f"{quoted} hook stop{flag}", "timeout": HOOK_EVENTS["Stop"]["timeout"]}]}],
            "PreToolUse": [{"matcher": HOOK_EVENTS["PreToolUse"]["matcher"],
                            "hooks": [{"type": "command", "command": f"{quoted} hook pretool{flag}", "timeout": HOOK_EVENTS["PreToolUse"]["timeout"]}]}],
        }
    }


def _is_ours(group: dict, kind: str) -> bool:
    return any("done-contract" in str(h.get("command", "")) and kind in str(h.get("command", "")) for h in group.get("hooks", []))


def merge_hooks(settings: dict, snippet: dict) -> dict:
    """Install or upgrade our hook groups; other tools' hooks are preserved untouched."""
    hooks_cfg = settings.setdefault("hooks", {})
    for event, groups in snippet["hooks"].items():
        kind = "hook stop" if event == "Stop" else "hook pretool"
        existing = [g for g in hooks_cfg.get(event, []) if not _is_ours(g, kind)]
        existing.extend(groups)
        hooks_cfg[event] = existing
    return settings


def cmd_hook(args) -> int:
    if args.hook_cmd in ("stop", "pretool"):
        code, out = hooks.run_hook(args.hook_cmd, sys.stdin.read(), require_contract=args.require_contract)
        if out:
            sys.stdout.write(out + "\n")
        return code
    if args.hook_cmd == "install":
        bin_path = str(Path(sys.argv[0]).resolve())
        snippet = hook_snippet(bin_path, require_contract=args.require_contract)
        if not args.write:
            print(json.dumps(snippet, ensure_ascii=False, indent=2))
            print("# add to <repo>/.claude/settings.json, or run with --write to merge", file=sys.stderr)
            return 0
        repo = _repo(args)
        settings_path = repo / ".claude" / "settings.json"
        settings = core.read_json(settings_path, default={}) or {}
        merge_hooks(settings, snippet)
        core.write_json(settings_path, settings)
        print(f"hooks merged into {settings_path}")
        for event in ("Stop", "PreToolUse"):
            for g in settings["hooks"].get(event, []):
                for h in g.get("hooks", []):
                    if "done-contract" in str(h.get("command", "")):
                        print(f"  {event}: {h['command']}  (timeout {h.get('timeout')})")
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="done-contract",
                                description="Per-item completion contracts that a gate executes; the agent's prose is never read.")
    p.add_argument("--repo", help="repository path (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a contract skeleton for a task and make it active")
    s.add_argument("--task", required=True)
    s.add_argument("--request", required=True, help="the user's request verbatim, or @file")
    s.add_argument("--items", help="JSON file with items [{id,text,check,expect?,timeout?,cache?}]")
    s.add_argument("--repo-check", action="append", help="repository-level check command (repeatable)")
    s.add_argument("--protected", action="append", help="protected glob (repeatable; default: common test paths)")
    s.add_argument("--allow-protected-changes", action="store_true")
    s.add_argument("--abandon-reason", help="required to replace an approved, unfinished active contract; recorded")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("approve", help="(person) approve and lock the contract")
    s.add_argument("--task")
    s.add_argument("--approver")
    s.add_argument("--yes", action="store_true", help="skip the y/N prompt (still requires a TTY)")
    s.add_argument("--accept-dirty", action="store_true", help="approve even though files changed since init (recorded)")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("check", help="run every item check and write evidence (exit 0 only for PASS)")
    s.add_argument("--task")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-reuse", action="store_true", help="re-run even items marked cache:true")
    s.add_argument("--budget", type=float, help="seconds; the whole run, including running commands, stops at this deadline")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("mark", help="mark an item blocked (with reason) or open")
    s.add_argument("item")
    s.add_argument("status", choices=["blocked", "open"])
    s.add_argument("--reason")
    s.add_argument("--task")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("pause", help="declare the task paused (e.g. waiting for the user); allows stopping, shown in evidence")
    s.add_argument("--reason", required=True)
    s.add_argument("--task")
    s.set_defaults(func=cmd_pause)

    s = sub.add_parser("resume", help="clear a pause")
    s.add_argument("--task")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("status", help="show contract, approval, marks and last evidence")
    s.add_argument("--task")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("close", help="re-run the checks under the lock and close the task (exit 0 only for PASS)")
    s.add_argument("--task")
    s.set_defaults(func=cmd_close)

    s = sub.add_parser("verify", help="re-run recorded commands without cache and compare with the evidence (for reviewers)")
    s.add_argument("--task")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("hook", help="Claude Code hook adapters")
    s.add_argument("hook_cmd", choices=["stop", "pretool", "install"])
    s.add_argument("--require-contract", action="store_true",
                   help="policy: deny file changes unless an approved contract is active (also used by install)")
    s.add_argument("--write", action="store_true", help="install: merge into <repo>/.claude/settings.json")
    s.set_defaults(func=cmd_hook)

    s = sub.add_parser("version")
    s.set_defaults(func=lambda a: (print(core.VERSION) or 0))
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except core.NotInteractive as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except core.DoneContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # infrastructure failure: never report success
        print(f"error (internal): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
