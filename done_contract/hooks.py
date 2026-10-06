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
import subprocess
import time
from pathlib import Path

from . import core

# Claude Code names first; Grok (write, search_replace, run_terminal_command) and Cursor
# (Write, StrReplace, Delete, Shell) send their own tool names to the same hooks.
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit",
              "write", "search_replace", "edit_file", "write_file", "create_file", "delete_file",
              "StrReplace", "Delete", "EditNotebook"}
SHELL_TOOLS = {"Bash", "run_terminal_command", "Shell", "shell", "run_shell_command"}
PATH_KEYS = ("file_path", "notebook_path", "path", "target_file", "filePath", "file")
# agent hook configuration: turning these off would switch the gate off
HOOK_CONFIG_GLOBS = [".claude/settings.json", ".claude/settings.local.json", ".cursor/hooks.json", ".grok/hooks/**",
                     ".grok/config.toml"]
HOOK_CONFIG_RE = re.compile(r"\.claude[/\\]settings(\.local)?\.json|\.cursor[/\\]hooks\.json|\.grok[/\\](hooks[/\\]|config\.toml)|disableAllHooks")
MAX_BLOCKS_DEFAULT = 3
STOP_BUDGET_DEFAULT_S = 840  # below the 900 s hook timeout installed by `hook install`

APPROVE_RE = re.compile(r"done-contract\s+approve\b|\.done-contract[/\\]approved|DONE_CONTRACT_APPROVE_NO_TTY|DONE_CONTRACT_HOME")
STATE_PATH_RE = re.compile(r"\.done-contract\b")  # bare dir too, so `rm -rf .done-contract` is caught
# the agent's control surface: its state dir and every vendor's hook dir. Deleting or moving any of
# these turns the gate off (see _control_dir_decision), so such a command is denied while a contract
# is active. Judged per shell segment, by path component, on the real target argument.
CONTROL_DIR_NAMES = {".done-contract", ".claude", ".cursor", ".grok"}
DESTROY_VERBS = {"rm", "rmdir", "shred", "truncate"}  # target all non-flag args
MOVE_VERBS = {"mv", "rename"}                          # source or dest kills the dir
DEST_VERBS = {"cp", "ln", "install", "rsync"}          # only the destination overwrites it
WRITE_HINT_RE = re.compile(
    r"(?<![2&<])>(?!/dev/null)|\btee\b|\bsed\s+-i\b|(^\s*|[;&|(]\s*)(sudo\s+)?(rm|rmdir|mv|cp|truncate|chmod|chown|ln|touch|install|mkdir|dd)\b"
    r"|\bgit\s+(rm|checkout|restore|mv|clean|stash|apply|am|cherry-pick|merge|rebase|reset|commit|pull)\b"
    r"|\b(python3?|perl)\s+-[ciwp]\b|\bpatch\b|\bnpm\s+(i|install|ci|update|uninstall)\b|\bpip3?\s+install\b|\bcargo\s+(add|install)\b")
INTERPRETER_RE = re.compile(r"^\s*(sudo\s+)?(python3?|node|ruby|perl|php|sh|bash|zsh|nohup|xargs|eval|exec|source|\.)\b")
HEREDOC_RE = re.compile(r"<<-?\s*['\"]?\w+")
# pure readers and runners that do not write by default
READ_ONLY_RE = re.compile(
    r"^\s*(cat|ls|ll|head|tail|less|more|wc|grep|rg|egrep|fgrep|fd|stat|file|which|type|pwd|echo|printf|printenv|date|"
    r"whoami|id|uname|tree|du|df|diff|cmp|md5|md5sum|shasum|sha256sum|jq|yq|sort|uniq|cut|tr|column|basename|dirname|"
    r"realpath|readlink|true|false|test|\[|git\s+(status|log|diff|show|branch|rev-parse|ls-files|blame|describe|remote|tag|"
    r"stash\s+list|config\s+(--get|-l|--list))|python3?\s+-m\s+(pytest|unittest|json\.tool)|pytest|npm\s+(test|run\s+test|ls)|"
    r"pnpm\s+test|yarn\s+test|cargo\s+(test|check|clippy)|go\s+(test|vet)|make\s+(test|check)|mypy|pyright|ruff\s+check|"
    r"eslint|tsc\b[^\n]*--noEmit)\b")
# options that turn a reader/runner into a writer
WRITE_FLAG_RE = re.compile(r"(^|\s)(--fix|--fix-only|--write|-w|--update|-u|--emit|--outDir|--out-dir|--save|--overwrite|--in-place|-i)(\s|$)")
FIND_WRITE_RE = re.compile(r"(^|\s)-(delete|exec|execdir|ok|okdir|fprint\w*)\b")
ENV_PREFIX_RE = re.compile(r"^\s*(env\s+)?((?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)")
DONE_CONTRACT_CMD_RE = re.compile(r"^\s*(\S*/)?done-contract\s+(init|status|check|mark|pause|resume|verify|version|close)\b")
SEGMENT_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||\r?\n)\s*")


def _strip_env_prefix(segment: str) -> str:
    """`env FOO=1 cmd …` and `FOO=1 cmd …` are judged by `cmd …`."""
    m = ENV_PREFIX_RE.match(segment)
    return segment[m.end():] if m else segment


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


def _repo_of(path) -> Path | None:
    """Git top level of an existing path, or of its nearest existing ancestor."""
    if not path:
        return None
    p = Path(path)
    while not p.exists() and not p.is_symlink():
        if p.parent == p:
            return None
        p = p.parent
    try:
        return core.repo_root(p)
    except core.GitError:
        return None


def _payload_cwd(payload: dict) -> Path:
    return Path(payload.get("cwd") or os.getcwd())


def _project_repo(payload: dict) -> Path | None:
    """The repository the hooks were installed for: the --project path written by
    `hook install`, else Claude Code's CLAUDE_PROJECT_DIR, else the shell cwd. The first two
    stay fixed when the agent `cd`s elsewhere; the payload cwd follows the shell."""
    for candidate in (payload.get("__project__"), os.environ.get("CLAUDE_PROJECT_DIR")):
        if candidate:
            repo = _repo_of(candidate)
            if repo is not None:
                return repo
    return _repo_of(_payload_cwd(payload))


def _lexical_repo(path: Path) -> Path | None:
    """Repository that owns the path as written. A symlinked component that lies inside a
    repository makes that repository the owner (the link entry is its file), even when the link
    leads to, or through, another repository; symlinks above every repository (e.g. macOS /var)
    are ignored. Otherwise the owner is the nearest directory holding .git."""
    p = Path(os.path.normpath(str(path)))
    chain = [p] + list(p.parents)  # bottom-up
    for i, a in enumerate(chain):
        if a.is_symlink() and any((up / ".git").exists() for up in chain[i + 1:]):
            return _lexical_repo(a.parent)
    for a in chain:
        if a.is_dir() and (a / ".git").exists():
            try:
                return core.repo_root(a)
            except core.GitError:
                return None
    return None


def _candidate_repos(payload: dict) -> list[Path]:
    out: list[Path] = []
    for repo in (_project_repo(payload), _repo_of(_payload_cwd(payload))):
        if repo is not None and repo not in out:
            out.append(repo)
    return out


def _active_task(repo: Path) -> str | None:
    task = core.active_task(repo)
    if task and (core.task_dir(repo, task) / "contract.json").exists():
        return task
    return None


def _approved_task(repo: Path) -> str | None:
    task = _active_task(repo)
    if task and core.contract_state(repo, task) == "approved":
        return task
    return None


def _is_ignored(repo: Path, rel: str) -> bool:
    proc = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=str(repo), capture_output=True)
    return proc.returncode == 0


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
            lines.append(f"- repo check {rc['status']} (exit {rc.get('exit')}): {rc['command']}" + (f" — {rc['error']}" if rc.get("error") else ""))
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


def _with_log_note(message: str, logged: bool) -> str:
    return message if logged else message + " (경고: 결정 로그 기록 실패)"


def stop(payload: dict, *, require_contract: bool = False) -> dict | None:
    """Gate every repository this session is working in: the project the hooks belong to and
    the shell's current repository, so a `cd` cannot skip the gate. One time budget covers all
    of them, and a failure in one repository never cancels another repository's block."""
    repos = _candidate_repos(payload)
    if not repos:
        return None
    if str(payload.get("reason") or "") in ("shutdown", "channel_closed"):
        return None  # Grok also fires Stop when the session closes; its decision is ignored there
    session = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
    deadline = time.monotonic() + _budget()
    results: list[dict] = []
    errors: list[str] = []
    any_active = False
    for repo in repos:
        try:
            task = _active_task(repo)
            if task is None:
                continue
            any_active = True
            res = _stop_one(repo, task, session, require_contract, max(0.0, deadline - time.monotonic()))
            if res:
                results.append(res)
        except Exception as exc:  # isolate: keep the other repositories' decisions
            any_active = True
            logged = core.log_event({"event": "hook_stop_error", "repo": str(repo), "error": repr(exc)[:500]})
            errors.append(f"done-contract ERROR ({repo.name}): 완료 검증을 수행하지 못했다 ({type(exc).__name__}: {str(exc)[:200]}). "
                          "이 저장소의 종료는 검증되지 않았다." + ("" if logged else " (로그 기록도 실패)"))
    if not any_active:
        if require_contract:
            return {"systemMessage": "done-contract: 이 저장소는 계약이 필요하다(require-contract). 승인된 계약이 없으면 파일 변경이 거부된다. "
                                     "`done-contract init --task <slug> --request ...` 뒤 사람이 `done-contract approve`를 실행한다."}
        return None
    blocking = [r for r in results if r.get("decision") == "block"]
    if blocking:
        return {"decision": "block", "reason": "\n\n".join([r["reason"] for r in blocking] + errors)}
    messages = [r["systemMessage"] for r in results if r.get("systemMessage")] + errors
    return {"systemMessage": "\n".join(messages)} if messages else None


def _stop_one(repo: Path, task: str, session: str, require_contract: bool, budget_s: float) -> dict | None:
    contract = core.load_contract(repo, task)
    state_name = core.contract_state(repo, task)
    if state_name in ("closed", "abandoned"):
        return None
    if state_name == "draft":
        logged = core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "allow", "why": "unapproved"})
        return {"systemMessage": _with_log_note(
            f"done-contract: 계약 '{task}'은 아직 승인되지 않았다. 사람이 `done-contract approve`를 실행하기 전까지 완료 게이트는 꺼져 있다"
            + (" (require-contract: 파일 변경은 거부된다)." if require_contract else "."), logged)}
    max_blocks = _max_blocks()
    key = f"{session}__{task}__{core.contract_sha(contract)[:16]}"
    state = _load_state(key)
    try:
        ev = core.run_check(repo, task, reuse=True, session=session, budget_s=budget_s)
    except core.LockBusy as exc:
        # same block budget as any other non-passing outcome; past the cap, release visibly as unverified
        if state["blocks"] >= max_blocks:
            logged = core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "release",
                                     "why": "lock_busy_cap", "blocks": state["blocks"]})
            return {"systemMessage": _with_log_note(
                f"done-contract: 차단 상한({max_blocks})에 도달해 통과시킨다. 계약 '{task}'은 검증되지 않았다 ({exc}).", logged)}
        state["blocks"] = int(state["blocks"]) + 1
        _save_state(key, state)
        core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "block", "why": "lock_busy",
                        "blocks": state["blocks"]})
        return {"decision": "block", "reason": f"done-contract: 검증을 수행하지 못했다 ({exc}). 다른 검사가 끝난 뒤 다시 끝내거나 "
                                                 f"`done-contract check`를 직접 실행하라. 차단 {state['blocks']}/{max_blocks}."}
    if ev["verdict"] in core.ALLOW_STOP_VERDICTS:
        state["blocks"] = 0
        state["last_verdict"] = ev["verdict"]
        _save_state(key, state)
        logged = core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "allow",
                                 "verdict": ev["verdict"], "tree": ev.get("tree")})
        return {"systemMessage": _with_log_note(core.summarize_evidence(ev), logged)}
    if state["blocks"] >= max_blocks:
        released = core.mark_released(repo, task, "block_cap", session) or ev
        logged = core.log_event({"event": "stop", "repo": str(repo), "task": task, "session": session, "decision": "release",
                                 "why": "cap", "verdict": ev["verdict"], "tree": ev.get("tree")})
        detail = "; ".join(_item_lines(released)[:6])
        return {"systemMessage": _with_log_note(
            f"done-contract: 차단 상한({max_blocks})에 도달해 통과시킨다. 계약 '{task}'은 완료가 아니다 (판정 {ev['verdict']}). {detail}", logged)}
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


def _protected_hit(lexical: str | None, resolved: str | None, protected: list[str]) -> str | None:
    for rel in (lexical, resolved):
        if rel and core.matches_any(rel, protected):
            return rel
    return None


def _segment_kind(segment: str) -> str:
    """allow | deny | unknown for one shell segment, judged after env/VAR= prefixes."""
    seg = _strip_env_prefix(segment)
    if not seg.strip():
        return "allow"  # bare `env` / assignment only
    if DONE_CONTRACT_CMD_RE.match(seg) and not WRITE_HINT_RE.search(seg):
        return "allow"
    if WRITE_HINT_RE.search(seg):
        return "deny"
    if re.match(r"^\s*find\b", seg):
        return "deny" if FIND_WRITE_RE.search(seg) else "allow"
    if READ_ONLY_RE.match(seg):
        return "deny" if WRITE_FLAG_RE.search(seg) else "allow"
    if INTERPRETER_RE.match(seg):
        return "deny"
    return "unknown"  # sed/awk/custom scripts: a person decides


def _bash_policy_without_contract(cmd: str) -> dict | None:
    if APPROVE_RE.search(cmd):
        return _deny("done-contract: 에이전트는 계약을 승인할 수 없다. 사람에게 `done-contract approve`를 부탁하라.")
    if HEREDOC_RE.search(cmd):
        return _deny("done-contract: 승인된 계약 없이는 heredoc 스크립트를 실행할 수 없다(require-contract).")
    unknown = False
    for seg in SEGMENT_SPLIT_RE.split(cmd):
        if not seg.strip():
            continue
        kind = _segment_kind(seg)
        if kind == "deny":
            return _deny("done-contract: 승인된 계약 없이는 파일을 바꾸거나 스크립트를 실행하는 명령을 쓸 수 없다(require-contract). "
                         "`done-contract init`으로 계약 초안을 쓰고 사람에게 `done-contract approve`를 부탁하라.")
        if kind == "unknown":
            unknown = True
    if unknown:
        return _ask("done-contract: 승인된 계약이 없다(require-contract). 이 명령이 파일을 바꾸지 않는지 사람이 정한다.")
    return None


def _edit_decision(repo: Path, task: str, target: Path) -> dict | None:
    contract = core.load_contract(repo, task)
    protected = contract.get("protected", [])
    allow = bool(contract.get("allow_protected_changes"))
    lexical, resolved = core.repo_relative(repo, target)
    if lexical is None and resolved is None:
        return None
    if lexical == f"{core.CONTRACT_DIRNAME}/{task}/contract.json":
        return _deny(f"done-contract: 승인된 계약 '{task}'은 변경할 수 없다. 기준을 바꾸려면 사람에게 새 revision 승인을 요청하라.")
    if (lexical or "").startswith(f"{core.CONTRACT_DIRNAME}/") or (resolved or "").startswith(f"{core.CONTRACT_DIRNAME}/"):
        return _deny("done-contract: 증빙·상태 파일은 도구 명령(mark, pause, check)으로만 바꾼다.")
    config_hit = _protected_hit(lexical, resolved, HOOK_CONFIG_GLOBS)
    if config_hit:
        return _deny(f"done-contract: '{config_hit}'은 에이전트 hook 설정이다. 승인된 계약 '{task}'이 진행 중에는 바꿀 수 없다(게이트를 끄게 된다). "
                     "설정 변경이 필요하면 사람에게 부탁하라.")
    hit = None if allow else _protected_hit(lexical, resolved, protected)
    if hit:
        return _deny(f"done-contract: '{hit}'은 계약 '{task}'의 보호 경로다. 테스트 대신 테스트 대상 코드를 고쳐라. "
                     "테스트 변경이 작업에 포함되면 사람에게 allow_protected_changes 재승인을 요청하라.")
    return None


def _command_bases(cmd: str, cwd: Path) -> list[Path]:
    """The shell cwd plus every directory the command `cd`s into (best effort, in order; quoted
    paths are supported, variables and substitutions are not)."""
    bases = [cwd]
    current = cwd
    for seg in SEGMENT_SPLIT_RE.split(cmd):
        part = _strip_env_prefix(seg)
        try:
            toks = shlex.split(part, posix=True)
        except ValueError:
            toks = part.split()
        if len(toks) >= 2 and toks[0] in ("cd", "pushd"):
            arg = os.path.expanduser(toks[1])
            current = Path(os.path.normpath(str(Path(arg) if os.path.isabs(arg) else current / arg)))
            bases.append(current)
    return bases


def _command_paths(repo: Path, cmd: str, bases: list[Path]) -> list[tuple[str | None, str | None]]:
    """(lexical, resolved) repo-relative paths named by the command's tokens, for every base."""
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    inside = [b for b in bases if core.repo_relative(repo, b)[0] is not None]
    out: list[tuple[str | None, str | None]] = []
    for tok in tokens:
        t = tok.strip("'\"")
        if not t or t.startswith("-"):
            continue
        if os.path.isabs(t) or t.startswith("~"):
            out.append(core.repo_relative(repo, os.path.expanduser(t)))
            continue
        pathlike = "/" in t or t.startswith(".")
        for base in bases:
            if pathlike or os.path.lexists(base / t):
                out.append(core.repo_relative(repo, t, base))
        if not pathlike and inside:
            out.append((core.normalize_rel(t), None))
    return out


def _protected_under_dir(repo: Path, rel: str, protected: list[str]) -> str | None:
    if not (repo / rel).is_dir():
        return None
    listing = subprocess.run(["git", "ls-files", "-z", "--", rel], cwd=str(repo), capture_output=True).stdout
    for raw in listing.split(b"\0"):
        f = raw.decode("utf-8", "replace")
        if f and core.matches_any(f, protected):
            return f
    return None


def _names_control_dir(tok: str) -> bool:
    """True when a path token is, or is inside, one of the control directories, matched on whole
    path components so `.claude-notes.md` and `report.claude.txt` do not match."""
    t = os.path.expanduser(tok.strip().strip("'\""))
    if not t:
        return False
    return any(part in CONTROL_DIR_NAMES for part in t.replace("\\", "/").split("/"))


def _control_dir_decision(cmd: str) -> bool:
    """True when a command would delete, move over, or overwrite a control directory. Token-aware
    and cwd-aware: per segment it strips env/assignment and `sudo`/`command`/`exec` prefixes and an
    absolute command path, follows `cd`/`pushd` so a relative target is resolved against it, then
    checks the real target of a destructive verb (rm/mv/cp/find -delete …)."""
    cwd = Path(".")
    stack: list[Path] = []

    def target_hits(tok: str) -> bool:
        t = tok.strip().strip("'\"")
        if not t:
            return False
        if t.startswith("~") or os.path.isabs(t):
            return _names_control_dir(t)
        return _names_control_dir(os.path.normpath(str(cwd / t)))

    for seg in SEGMENT_SPLIT_RE.split(cmd):
        part = _strip_env_prefix(seg)
        try:
            toks = shlex.split(part, posix=True, comments=True)
        except ValueError:
            toks = part.split("#")[0].split()
        while toks and (toks[0] in ("sudo", "command", "exec", "nohup", "time", "env") or "=" in toks[0]):
            toks = toks[1:]
        if not toks:
            continue
        verb = os.path.basename(toks[0])
        args = [a for a in toks[1:] if not a.startswith("-")]
        if verb == "popd":
            if stack:
                cwd = stack.pop()
            continue
        if verb in ("cd", "pushd") and args:
            if verb == "pushd":
                stack.append(cwd)
            a = os.path.expanduser(args[0])
            cwd = Path(os.path.normpath(a if os.path.isabs(a) else str(cwd / a)))
            continue
        if verb == "find":
            if re.search(r"(^|\s)-(delete|exec|execdir|ok|okdir)\b", part) and any(target_hits(a) for a in args):
                return True
            continue
        if verb in DESTROY_VERBS or verb in MOVE_VERBS:
            if any(target_hits(a) for a in args):
                return True
        elif verb in DEST_VERBS:
            if args and target_hits(args[-1]):  # destination only
                return True
    return False


def _bash_decision(repo: Path, task: str, cmd: str, bases: list[Path]) -> dict | None:
    contract = core.load_contract(repo, task)
    protected = contract.get("protected", [])
    allow = bool(contract.get("allow_protected_changes"))
    if _control_dir_decision(cmd):
        return _deny(f"done-contract: 상태·hook 설정 디렉터리(.done-contract/.claude/.cursor/.grok)를 지우거나 옮기는 명령은 "
                     f"승인된 계약 '{task}'이 진행 중에는 쓸 수 없다(게이트를 끄게 된다). 사람에게 부탁하라.")
    if STATE_PATH_RE.search(cmd) and (WRITE_HINT_RE.search(cmd) or HEREDOC_RE.search(cmd)):
        return _deny("done-contract: .done-contract 상태 파일은 도구 명령(mark, pause, check)으로만 바꾼다.")
    if HOOK_CONFIG_RE.search(cmd) and (WRITE_HINT_RE.search(cmd) or HEREDOC_RE.search(cmd) or INTERPRETER_RE.search(cmd)
                                       or re.search(r"\b(claude|grok)\s+(config|hooks?|plugin)\b", cmd)):
        return _deny(f"done-contract: 에이전트 hook 설정은 승인된 계약 '{task}'이 진행 중에는 바꿀 수 없다(게이트를 끄게 된다). 설정 변경은 사람에게 부탁하라.")
    if allow or not (WRITE_HINT_RE.search(cmd) or HEREDOC_RE.search(cmd)):
        return None
    for lexical, resolved in _command_paths(repo, cmd, bases):
        for rel in (lexical, resolved):  # each spelling on its own: an ignored alias never hides a tracked target
            if not rel or rel == ".":
                continue
            hit = rel if core.matches_any(rel, protected) else _protected_under_dir(repo, rel, protected)
            if hit and not _is_ignored(repo, hit):  # ignored build output under tests/ (e.g. __pycache__) is not a test change
                return _ask(f"done-contract: 이 명령은 보호 경로 '{hit}'를 바꿀 수 있다(계약 '{task}'). 허용할지 사람이 정한다.")
    return None


def _touches_repo(repo: Path, cmd: str, bases: list[Path]) -> bool:
    """Whether a shell command may act on the repository: it runs or `cd`s inside it, or names one of its paths."""
    if any(core.repo_relative(repo, b)[0] is not None for b in bases):
        return True
    return any(lexical or resolved for lexical, resolved in _command_paths(repo, cmd, bases))


def pretool(payload: dict, *, require_contract: bool = False) -> dict | None:
    """Edits are judged by the repositories that own the edited file (as written and as resolved);
    shell commands by the project repository and the shell's current repository. The
    require-contract policy covers the project repository (where the hooks are installed)."""
    tool = payload.get("tool_name") or payload.get("toolName")
    inp = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(inp, dict):
        inp = {}
    cwd = _payload_cwd(payload)
    project = _project_repo(payload)
    if tool in EDIT_TOOLS:
        fp = next((inp.get(k) for k in PATH_KEYS if isinstance(inp.get(k), str) and inp.get(k)), None)
        if not fp:
            return None
        target = Path(fp) if Path(fp).is_absolute() else cwd / fp
        target = Path(os.path.normpath(str(target)))
        repos: list[Path] = []
        for r in (_lexical_repo(target), _repo_of(target.resolve())):
            if r is not None and r not in repos:
                repos.append(r)
        for repo in repos:
            task = _approved_task(repo)
            if task:
                decision = _edit_decision(repo, task, target)
                if decision:
                    return decision
        if require_contract and project is not None and project in repos and _approved_task(project) is None:
            lexical, resolved = core.repo_relative(project, target)
            if lexical is None and resolved is None:
                return None
            draft = _active_task(project)
            if draft and lexical == f"{core.CONTRACT_DIRNAME}/{draft}/contract.json":
                return None
            return _deny("done-contract: 이 저장소는 승인된 계약 없이는 파일을 바꿀 수 없다(require-contract). "
                         "`done-contract init`으로 계약 초안을 쓰고 사람에게 `done-contract approve`를 부탁하라.")
        return None
    if tool in SHELL_TOOLS:
        cmd = str(inp.get("command") or "")
        if APPROVE_RE.search(cmd):
            return _deny("done-contract: 에이전트는 계약을 승인하거나 done-contract 승인 상태를 만질 수 없다. 사람에게 `done-contract approve`를 부탁하라.")
        bases = _command_bases(cmd, cwd)
        repos: list[Path] = []
        for r in _candidate_repos(payload) + [_repo_of(b) for b in bases[1:]]:
            if r is not None and r not in repos:
                repos.append(r)
        for repo in repos:
            task = _approved_task(repo)
            if task:
                decision = _bash_decision(repo, task, cmd, bases)
                if decision:
                    return decision
        if (require_contract and project is not None and _approved_task(project) is None
                and _touches_repo(project, cmd, bases)):
            return _bash_policy_without_contract(cmd)
        return None
    return None


def run_hook(kind: str, stdin_text: str, *, require_contract: bool = False, project: str | None = None) -> tuple[int, str]:
    """Returns (exit_code, stdout). Always exit 0; decisions ride in the JSON.

    On an internal error the stop hook allows the stop but says so loudly, and
    the pretool hook falls open (no decision)."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except json.JSONDecodeError:
        payload = {}
    if project:
        payload["__project__"] = project
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
