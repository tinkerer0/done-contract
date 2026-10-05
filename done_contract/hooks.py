"""Claude Code hook adapters. Input is the hook JSON on stdin; output is JSON on stdout.

Two decisions are kept apart: the completion verdict (evidence.verdict, PASS only
means done) and the stop decision (allow/block). A stop may be allowed for a
disclosed INCOMPLETE/PAUSED verdict or when the block budget is exhausted; in the
latter case the evidence records `released` and no success receipt is shown.

Internal errors never block (a hook must not brick a session) but they are made
visible with a systemMessage and a log line. The agent's message and transcript
fields of the payload are never read.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from . import core

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_BLOCKS_DEFAULT = 3
STOP_BUDGET_DEFAULT_S = 840  # below the 900 s hook timeout installed by `hook install`

APPROVE_RE = re.compile(r"done-contract\s+approve\b|\.done-contract[/\\]approved|DONE_CONTRACT_APPROVE_NO_TTY|DONE_CONTRACT_HOME")
STATE_PATH_RE = re.compile(r"\.done-contract[/\\]")
WRITE_HINT_RE = re.compile(
    r"(?<![2&])>(?!/dev/null)|\btee\b|\bsed\s+-i\b|(^|[;&|(]\s*)(sudo\s+)?(rm|mv|cp|truncate|chmod|ln|touch|install)\b"
    r"|\bgit\s+(rm|checkout|restore|mv|clean|stash|apply|am|cherry-pick|merge|rebase|reset)\b|\bpython3?\s+-c\b|\bperl\s+-[pi]\b|\bpatch\b")
DONE_CONTRACT_CMD_RE = re.compile(r"(^|[;&|]\s*)(\S*/)?done-contract\s+(init|status|check|mark|pause|resume|verify|version|close)\b")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120] or "unknown"


def _state_path(key: str) -> Path:
    d = core.home_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe_name(key)}.json"


def _load_state(key: str) -> dict:
    data = core.read_json(_state_path(key), default=None) or {}
    data.setdefault("blocks", 0)
    return data


def _save_state(key: str, state: dict) -> None:
    core.write_json(_state_path(key), state, mode=0o600)


def _repo_and_task(payload: dict) -> tuple[Path | None, str | None]:
    cwd = payload.get("cwd") or os.getcwd()
    try:
        repo = core.repo_root(Path(cwd))
    except core.GitError:
        return None, None
    task = core.active_task(repo)
    if not task or not (core.task_dir(repo, task) / "contract.json").exists():
        return repo, None
    return repo, task


def _max_blocks() -> int:
    try:
        return max(1, int(os.environ.get("DONE_CONTRACT_MAX_BLOCKS", MAX_BLOCKS_DEFAULT)))
    except ValueError:
        return MAX_BLOCKS_DEFAULT


def _budget() -> float:
    try:
        return float(os.environ.get("DONE_CONTRACT_STOP_BUDGET", STOP_BUDGET_DEFAULT_S))
    except ValueError:
        return STOP_BUDGET_DEFAULT_S


def _item_lines(ev: dict) -> list[str]:
    lines = []
    for it in ev.get("items", []):
        if it["status"] == "PASS":
            lines.append(f"- {it['id']} PASS")
        elif it["status"] == "BLOCKED":
            lines.append(f"- {it['id']} BLOCKED: {it.get('blocked_reason')}")
        elif it["status"] == "ERROR":
            lines.append(f"- {it['id']} ERROR: {it.get('error')}")
        else:
            exit_s = "timeout" if it.get("timed_out") else f"exit {it.get('exit')}"
            why = " (expect 불일치)" if it.get("expect") and it.get("expect_matched") is False else ""
            lines.append(f"- {it['id']} FAIL ({exit_s}{why}): {it['command']}")
            tail = (it.get("output_tail") or "").strip().splitlines()[-12:]
            lines.extend("    " + t for t in tail)
    for rc in ev.get("repo_checks", []):
        if rc["status"] != "PASS":
            lines.append(f"- repo check {rc['status']} (exit {rc.get('exit')}): {rc['command']}")
            tail = (rc.get("output_tail") or "").strip().splitlines()[-12:]
            lines.extend("    " + t for t in tail)
    if ev.get("protected_changed") and not ev.get("allow_protected_changes"):
        lines.append("- 보호 경로(테스트 등)가 baseline 이후 바뀌었다. 테스트를 고쳐 통과시키는 것은 허용되지 않는다:")
        lines.extend(f"    {p}" for p in ev["protected_changed"])
        lines.append("  테스트 변경이 작업에 포함된다면 사람에게 allow_protected_changes 재승인을 요청하고, 아니면 변경을 되돌린다.")
    if ev.get("verdict") == core.VERDICT_STALE:
        lines.append("- 검사 중에 작업 트리가 바뀌어 결과를 믿을 수 없다(STALE): " + ", ".join(ev.get("stale_paths", [])[:10]))
        lines.append("  check 명령이 파일을 만든다면 그 산출물을 .gitignore에 넣고, 아니면 다른 프로세스의 편집이 끝난 뒤 다시 확인한다.")
    return lines


def _block_reason(task: str, ev: dict, blocks: int, max_blocks: int) -> str:
    lines = [f"done-contract: 계약 '{task}' 판정 {ev['verdict']}. 완료로 끝낼 수 없다."]
    lines.extend(_item_lines(ev))
    if ev.get("verdict") == core.VERDICT_ERROR:
        lines.append("일부 check를 실행하지 못했다. `done-contract check`를 직접 실행해 증빙을 만든 뒤 다시 끝내라.")
    lines.append("다음 중 하나를 한다: (1) 고친 뒤 `done-contract check`로 확인 "
                 "(2) 할 수 없는 항목은 `done-contract mark <id> blocked --reason \"<이유>\"` "
                 "(3) 사용자 답을 기다려야 하면 `done-contract pause --reason \"<이유>\"`. "
                 "승인된 계약 자체는 바꿀 수 없다. 산문으로 완료라고 쓰는 것은 효력이 없다.")
    lines.append(f"차단 {blocks}/{max_blocks}.")
    return "\n".join(lines)


def stop(payload: dict, *, require_contract: bool = False) -> dict | None:
    repo, task = _repo_and_task(payload)
    if repo is None:
        return None
    session = str(payload.get("session_id") or "unknown")
    if task is None:
        if require_contract:
            return {"systemMessage": "done-contract: 이 저장소는 계약이 필요하다(require-contract). 승인된 계약이 없으면 파일 변경이 거부된다. "
                                     "`done-contract init --task <slug> --request ...` 뒤 사람이 `done-contract approve`를 실행한다."}
        return None
    contract = core.load_contract(repo, task)
    state_name = core.contract_state(repo, task)
    if state_name in ("closed", "abandoned"):
        return None
    if state_name == "draft":
        core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "allow", "why": "unapproved"})
        return {"systemMessage": f"done-contract: 계약 '{task}'은 아직 승인되지 않았다. 사람이 `done-contract approve`를 실행하기 전까지 완료 게이트는 꺼져 있다"
                                 + (" (require-contract: 파일 변경은 거부된다)." if require_contract else ".")}
    max_blocks = _max_blocks()
    key = f"{session}__{task}__{core.contract_sha(contract)[:16]}"
    state = _load_state(key)
    if state["blocks"] >= max_blocks:
        ev = core.mark_released(repo, task, "block_cap", session)
        core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "release", "why": "cap",
                        "verdict": (ev or {}).get("verdict")})
        detail = "; ".join(_item_lines(ev)[:6]) if ev else ""
        return {"systemMessage": f"done-contract: 차단 상한({max_blocks})에 도달해 통과시킨다. 계약 '{task}'은 완료가 아니다"
                                 f" (마지막 판정 {(ev or {}).get('verdict')}). {detail}"}
    ev = core.run_check(repo, task, reuse=True, session=session, budget_s=_budget())
    if ev["verdict"] in core.ALLOW_STOP_VERDICTS:
        core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "allow", "verdict": ev["verdict"], "tree": ev.get("tree")})
        return {"systemMessage": core.summarize_evidence(ev)}
    state["blocks"] = int(state["blocks"]) + 1
    state["last_verdict"] = ev["verdict"]
    state["last_tree"] = ev.get("tree")
    _save_state(key, state)
    core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "block", "verdict": ev["verdict"],
                    "tree": ev.get("tree"), "blocks": state["blocks"]})
    return {"decision": "block", "reason": _block_reason(task, ev, state["blocks"], max_blocks)}


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


def _ask(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": reason}}


def _rel_path(repo: Path, fp: str) -> str | None:
    p = Path(fp)
    if not p.is_absolute():
        p = repo / p
    try:
        return p.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def pretool(payload: dict, *, require_contract: bool = False) -> dict | None:
    tool = payload.get("tool_name")
    inp = payload.get("tool_input") or {}
    repo, task = _repo_and_task(payload)
    if repo is None:
        return None
    contract = core.load_contract(repo, task) if task else None
    state_name = core.contract_state(repo, task) if task else "none"
    gate_on = state_name == "approved"
    if not gate_on:
        if not require_contract:
            return None
        # policy: no approved contract -> no mutations, except drafting the contract itself
        if tool in EDIT_TOOLS:
            fp = inp.get("file_path") or inp.get("notebook_path")
            rel = _rel_path(repo, fp) if fp else None
            if rel and task and rel == f"{core.CONTRACT_DIRNAME}/{task}/contract.json" and state_name == "draft":
                return None
            if rel is None:
                return None
            return _deny("done-contract: 이 저장소는 승인된 계약 없이는 파일을 바꿀 수 없다(require-contract). "
                         "`done-contract init`으로 계약 초안을 쓰고 사람에게 `done-contract approve`를 부탁하라.")
        if tool == "Bash":
            cmd = str(inp.get("command") or "")
            if APPROVE_RE.search(cmd):
                return _deny("done-contract: 에이전트는 계약을 승인할 수 없다. 사람에게 `done-contract approve`를 부탁하라.")
            if DONE_CONTRACT_CMD_RE.search(cmd) and not WRITE_HINT_RE.search(cmd):
                return None
            if WRITE_HINT_RE.search(cmd):
                return _deny("done-contract: 승인된 계약 없이는 파일을 바꾸는 명령을 실행할 수 없다(require-contract).")
        return None
    protected = contract.get("protected", [])
    allow = bool(contract.get("allow_protected_changes"))
    if tool in EDIT_TOOLS:
        fp = inp.get("file_path") or inp.get("notebook_path")
        if not fp:
            return None
        rel = _rel_path(repo, fp)
        if rel is None:
            return None
        if rel == f"{core.CONTRACT_DIRNAME}/{task}/contract.json":
            return _deny(f"done-contract: 승인된 계약 '{task}'은 변경할 수 없다. 기준을 바꾸려면 사람에게 새 revision 승인을 요청하라.")
        if rel.startswith(f"{core.CONTRACT_DIRNAME}/"):
            return _deny("done-contract: 증빙·상태 파일은 도구 명령(mark, pause, check)으로만 바꾼다.")
        if not allow and core.matches_any(rel, protected):
            return _deny(f"done-contract: '{rel}'은 계약 '{task}'의 보호 경로다. 테스트 대신 테스트 대상 코드를 고쳐라. "
                         "테스트 변경이 작업에 포함되면 사람에게 allow_protected_changes 재승인을 요청하라.")
        return None
    if tool == "Bash":
        cmd = str(inp.get("command") or "")
        if APPROVE_RE.search(cmd):
            return _deny("done-contract: 에이전트는 계약을 승인하거나 done-contract 승인 상태를 만질 수 없다. 사람에게 `done-contract approve`를 부탁하라.")
        if STATE_PATH_RE.search(cmd) and WRITE_HINT_RE.search(cmd):
            return _deny("done-contract: .done-contract 상태 파일은 도구 명령(mark, pause, check)으로만 바꾼다.")
        if not allow and WRITE_HINT_RE.search(cmd):
            try:
                tokens = shlex.split(cmd, posix=True)
            except ValueError:
                tokens = cmd.split()
            for tok in tokens:
                t = tok.strip("'\"")
                while t.startswith("./"):
                    t = t[2:]
                if t and core.matches_any(t, protected):
                    return _ask(f"done-contract: 이 명령은 보호 경로 '{t}'를 바꿀 수 있다(계약 '{task}'). 허용할지 사람이 정한다.")
        return None
    return None


def run_hook(kind: str, stdin_text: str, *, require_contract: bool = False) -> tuple[int, str]:
    """Returns (exit_code, stdout). Always exit 0; decisions ride in the JSON.

    On an internal error the stop hook allows the stop but says so loudly, and
    the pretool hook falls open (no decision)."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except json.JSONDecodeError:
        payload = {}
    try:
        if kind == "stop":
            result = stop(payload, require_contract=require_contract)
        elif kind == "pretool":
            result = pretool(payload, require_contract=require_contract)
        else:
            result = None
        return 0, (json.dumps(result, ensure_ascii=False) if result else "")
    except Exception as exc:  # fail open, visibly
        logged = core.log_event({"event": f"hook_{kind}_error", "error": repr(exc)[:500], "cwd": payload.get("cwd")})
        if kind == "stop":
            msg = (f"done-contract ERROR: 완료 검증을 수행하지 못했다 ({type(exc).__name__}: {str(exc)[:200]}). "
                   "이 종료는 검증되지 않았다. `done-contract check`를 직접 실행해 원인을 확인하라."
                   + ("" if logged else " (로그 기록도 실패)"))
            return 0, json.dumps({"systemMessage": msg}, ensure_ascii=False)
        return 0, ""
