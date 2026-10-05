"""done-contract core: contracts, approval, checks, evidence.

The tool never reads the agent's prose or transcript. Inputs are the contract
file, git state, and command exit codes.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.2.0"
CONTRACT_DIRNAME = ".done-contract"

DEFAULT_PROTECTED = [
    "tests/**",
    "test/**",
    "**/__tests__/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*_test.go",
    "**/*.test.*",
    "**/*.spec.*",
    "**/conftest.py",
]

# checks that can never fail are refused by lint
TRIVIAL_CHECK_RE = re.compile(r"^\s*(true|:|exit\s+0|echo(\s[^|&;]*)?)\s*$")

STRENGTH_RULES = [
    ("test", re.compile(
        r"(^|[\s;&|(])(pytest|python3?\s+-m\s+(pytest|unittest)|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|"
        r"yarn\s+test|bun\s+test|cargo\s+test|go\s+test|npx\s+(jest|vitest|mocha)|jest|vitest|mocha|"
        r"swift\s+test|dotnet\s+test|gradlew?\s+test|mvn\s+test|make\s+test|rspec|phpunit|"
        r"unittest\s+discover)\b")),
    ("build", re.compile(
        r"(^|[\s;&|(])(cargo\s+(build|check|clippy)|go\s+(build|vet)|npm\s+run\s+(build|lint|typecheck)|"
        r"npx\s+tsc|tsc|swift\s+build|mypy|pyright|ruff|eslint|flake8|make\s+(build|lint))\b")),
    ("http", re.compile(r"(^|[\s;&|(])(curl|wget|http|xh|nc|ping)\b")),
    ("content", re.compile(r"(^|[\s;&|(])(grep|rg|diff|cmp|jq|yq|python3?\s+-c)\b")),
    ("existence", re.compile(r"(^|[\s;&|(])(test\s+-[efdsx]|\[\s+-[efdsx]|ls|stat|file|which)\b")),
]
# only these strengths may reuse a previous result for an unchanged tree
CACHEABLE_STRENGTHS = {"test", "build", "content", "existence"}

TASK_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
ITEM_KEYS = {"id", "text", "check", "expect", "timeout", "cache"}
CONTRACT_KEYS = {"version", "task", "request", "created_at", "baseline_head", "baseline_tree", "items",
                 "repo_checks", "protected", "allow_protected_changes"}

VERDICT_PASS = "PASS"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_PAUSED = "PAUSED"
VERDICT_FAIL = "FAIL"
VERDICT_TESTS_CHANGED = "TESTS_CHANGED"
VERDICT_STALE = "STALE"
VERDICT_ERROR = "ERROR"
VERDICT_UNAPPROVED = "UNAPPROVED"
# verdicts under which the Stop hook lets the agent stop (completion is still only PASS)
ALLOW_STOP_VERDICTS = {VERDICT_PASS, VERDICT_INCOMPLETE, VERDICT_PAUSED}

EXIT_CODES = {
    VERDICT_PASS: 0,
    VERDICT_FAIL: 1, VERDICT_TESTS_CHANGED: 1, VERDICT_STALE: 1,
    VERDICT_UNAPPROVED: 2,
    VERDICT_INCOMPLETE: 4, VERDICT_PAUSED: 4,
    VERDICT_ERROR: 5,
}


class DoneContractError(Exception):
    pass


class GitError(DoneContractError):
    pass


class NotInteractive(DoneContractError):
    pass


# ---------------------------------------------------------------- utilities

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path: Path, obj, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def home_dir() -> Path:
    override = os.environ.get("DONE_CONTRACT_HOME")
    base = Path(override).expanduser() if override else Path.home() / CONTRACT_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def log_event(event: dict) -> bool:
    """Append one decision line to ~/.done-contract/log.jsonl. Returns False if logging failed."""
    try:
        rec = {"ts": now_iso(), **event}
        with open(home_dir() / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except Exception:
        return False


@contextlib.contextmanager
def repo_lock(repo: Path):
    """Serialize check/mark/close within one worktree (two sessions, one .done-contract/)."""
    root = contract_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    ensure_excluded(repo)  # before the lock file exists, so it never enters a tree hash
    with open(root / ".lock", "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------- git

def git(repo: Path, *args: str, env: dict | None = None, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Path | str) -> Path:
    start = Path(start)
    if not start.exists():
        raise GitError(f"path does not exist: {start}")
    out = git(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    return Path(out.strip()).resolve()


def head_sha(repo: Path) -> str | None:
    proc = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(repo), capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def working_tree_hash(repo: Path) -> str:
    """Hash of the whole working tree: tracked and untracked files, .gitignore and
    .git/info/exclude respected, symlinks as blobs, submodules as gitlinks.

    Uses a temporary index so the user's index is untouched. The tree object is
    stored in the object database so later `git diff <tree> <tree>` works.
    """
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = os.path.join(td, "index")
        if head_sha(repo):
            git(repo, "read-tree", "HEAD", env=env)
        git(repo, "add", "-A", "--", ".", env=env)
        return git(repo, "write-tree", env=env).strip()


def changed_paths(repo: Path, tree_a: str, tree_b: str) -> list[str]:
    if tree_a == tree_b:
        return []
    out = git(repo, "diff", "--name-only", tree_a, tree_b)
    return [line for line in out.splitlines() if line.strip()]


def ensure_excluded(repo: Path) -> None:
    """Keep .done-contract/ out of git without touching the user's .gitignore."""
    info = repo / ".git" / "info"
    try:
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        line = f"{CONTRACT_DIRNAME}/"
        if line not in existing.splitlines():
            with open(exclude, "a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- globs

def glob_to_regex(pattern: str) -> re.Pattern:
    out = ""
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        c = pattern[i]
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    return re.compile("^" + out + "$")


def matches_any(path: str, globs: list[str]) -> bool:
    norm = path.replace(os.sep, "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return any(glob_to_regex(g).match(norm) for g in globs)


# ---------------------------------------------------------------- contract files

def contract_root(repo: Path) -> Path:
    return repo / CONTRACT_DIRNAME


def task_dir(repo: Path, task: str) -> Path:
    return contract_root(repo) / task


def active_task(repo: Path) -> str | None:
    p = contract_root(repo) / "active"
    try:
        value = p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def set_active(repo: Path, task: str | None) -> None:
    p = contract_root(repo) / "active"
    if task is None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(task + "\n", encoding="utf-8")


def resolve_task(repo: Path, task: str | None) -> str:
    chosen = task or active_task(repo)
    if not chosen:
        raise DoneContractError("no active contract; run `done-contract init --task <slug> --request ...`")
    if not (task_dir(repo, chosen) / "contract.json").exists():
        raise DoneContractError(f"contract not found: {task_dir(repo, chosen) / 'contract.json'}")
    return chosen


def load_contract(repo: Path, task: str) -> dict:
    data = read_json(task_dir(repo, task) / "contract.json")
    if data is None:
        raise DoneContractError(f"contract not found for task {task}")
    if not isinstance(data, dict):
        raise DoneContractError("contract.json must contain a JSON object")
    return data


def contract_sha(contract: dict) -> str:
    return sha256_bytes(canonical_json(contract))


def strength_of(check: str) -> str:
    for label, rx in STRENGTH_RULES:
        if rx.search(check):
            return label
    return "other"


def item_cacheable(item: dict) -> bool:
    if item.get("cache") is False:
        return False
    return strength_of(str(item.get("check", ""))) in CACHEABLE_STRENGTHS


def lint_contract(contract: dict) -> list[str]:
    problems: list[str] = []
    unknown = sorted(set(contract) - CONTRACT_KEYS)
    if unknown:
        problems.append(f"unknown contract keys: {', '.join(unknown)}")
    if contract.get("version") != 1:
        problems.append("version must be 1")
    task = contract.get("task")
    if not isinstance(task, str) or not TASK_SLUG_RE.match(task):
        problems.append("task must be a slug like login-rate-limit")
    if not isinstance(contract.get("request"), str) or not contract["request"].strip():
        problems.append("request must quote the user's request")
    tree = contract.get("baseline_tree")
    if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
        problems.append("baseline_tree must be a git tree hash (set by init)")
    items = contract.get("items")
    if not isinstance(items, list) or not items:
        problems.append("items must be a non-empty list")
        items = []
    seen: set[str] = set()
    for idx, item in enumerate(items):
        where = f"items[{idx}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object")
            continue
        unknown_item = sorted(set(item) - ITEM_KEYS)
        if unknown_item:
            problems.append(f"{where} has unknown keys: {', '.join(unknown_item)} (allowed: {', '.join(sorted(ITEM_KEYS))})")
        iid = item.get("id")
        if not isinstance(iid, str) or not ITEM_ID_RE.match(iid):
            problems.append(f"{where}.id must be a short id such as Q1")
        elif iid in seen:
            problems.append(f"{where}.id duplicates {iid}")
        else:
            seen.add(iid)
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            problems.append(f"{where}.text must quote the requirement")
        check = item.get("check")
        if not isinstance(check, str) or not check.strip():
            problems.append(f"{where}.check is required: an executable command that fails when the item is not done")
        elif TRIVIAL_CHECK_RE.match(check):
            problems.append(f"{where}.check '{check.strip()}' can never fail; use a real check")
        timeout = item.get("timeout", 300)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0 or timeout > 3600:
            problems.append(f"{where}.timeout must be an integer 1..3600 (seconds)")
        expect = item.get("expect")
        if expect is not None and (not isinstance(expect, str) or not expect):
            problems.append(f"{where}.expect must be a non-empty string or null")
        if "cache" in item and not isinstance(item["cache"], bool):
            problems.append(f"{where}.cache must be true or false")
    repo_checks = contract.get("repo_checks", [])
    if not isinstance(repo_checks, list):
        problems.append("repo_checks must be a list of commands")
    else:
        for idx, cmd in enumerate(repo_checks):
            if not isinstance(cmd, str) or not cmd.strip():
                problems.append(f"repo_checks[{idx}] must be a command")
            elif TRIVIAL_CHECK_RE.match(cmd):
                problems.append(f"repo_checks[{idx}] '{cmd.strip()}' can never fail")
    protected = contract.get("protected", [])
    if not isinstance(protected, list) or not all(isinstance(g, str) and g for g in protected):
        problems.append("protected must be a list of glob strings")
    if not isinstance(contract.get("allow_protected_changes", False), bool):
        problems.append("allow_protected_changes must be true or false")
    return problems


def new_contract(repo: Path, task: str, request: str, *, items: list | None = None,
                 repo_checks: list[str] | None = None, protected: list[str] | None = None,
                 allow_protected_changes: bool = False) -> dict:
    return {
        "version": 1,
        "task": task,
        "request": request,
        "created_at": now_iso(),
        "baseline_head": head_sha(repo),
        "baseline_tree": working_tree_hash(repo),
        "items": items or [],
        "repo_checks": repo_checks or [],
        "protected": protected if protected is not None else list(DEFAULT_PROTECTED),
        "allow_protected_changes": allow_protected_changes,
    }


def contract_state(repo: Path, task: str) -> str:
    """draft | approved | closed | abandoned"""
    marks = load_marks(repo, task)
    if marks.get("closed_at"):
        return "closed"
    if marks.get("abandoned"):
        return "abandoned"
    contract = read_json(task_dir(repo, task) / "contract.json")
    if contract and find_approval(repo, contract):
        return "approved"
    return "draft"


def init_contract(repo: Path, task: str, request: str, *, abandon_reason: str | None = None, **kw) -> dict:
    if not TASK_SLUG_RE.match(task):
        raise DoneContractError("task must be a slug like login-rate-limit")
    with repo_lock(repo):
        existing = active_task(repo)
        if existing and existing != task and (task_dir(repo, existing) / "contract.json").exists():
            state = contract_state(repo, existing)
            if state == "approved":
                if not (abandon_reason and abandon_reason.strip()):
                    raise DoneContractError(
                        f"active contract '{existing}' is approved and not closed; finish it with `done-contract close`, "
                        "or abandon it explicitly with --abandon-reason \"<why>\" (recorded in evidence)")
                _abandon(repo, existing, abandon_reason.strip())
            elif state == "draft":
                log_event({"event": "draft_replaced", "repo": str(repo), "task": existing, "by": task})
        tdir = task_dir(repo, task)
        if (tdir / "contract.json").exists():
            state = contract_state(repo, task)
            if state == "approved":
                raise DoneContractError(f"contract '{task}' is already approved; close it first")
            if state in ("closed", "abandoned"):
                raise DoneContractError(f"contract '{task}' is {state}; choose a new task slug")
        contract = new_contract(repo, task, request, **kw)
        tdir.mkdir(parents=True, exist_ok=True)
        write_json(tdir / "contract.json", contract)
        write_json(tdir / "marks.json", {"items": {}, "paused": None, "closed_at": None})
        set_active(repo, task)
        ensure_excluded(repo)
        log_event({"event": "init", "repo": str(repo), "task": task})
        return contract


def _abandon(repo: Path, task: str, reason: str) -> None:
    marks = load_marks(repo, task)
    marks["abandoned"] = {"reason": reason, "at": now_iso()}
    save_marks(repo, task, marks)
    ev = load_evidence(repo, task)
    log_event({"event": "abandon", "repo": str(repo), "task": task, "reason": reason,
               "last_verdict": ev.get("verdict") if ev else None})


# ---------------------------------------------------------------- approval

def approval_path(sha: str) -> Path:
    d = home_dir() / "approved"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d / f"{sha}.json"


def find_approval(repo: Path, contract: dict) -> dict | None:
    rec = read_json(approval_path(contract_sha(contract)))
    if not rec or not isinstance(rec, dict):
        return None
    if rec.get("repo") and Path(rec["repo"]).resolve() != Path(repo).resolve():
        return None
    return rec


def approve_contract(repo: Path, task: str, *, approver: str | None = None, assume_yes: bool = False,
                     accept_dirty: bool = False, stdin=None, stdout=None) -> dict:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    contract = load_contract(repo, task)
    problems = lint_contract(contract)
    if problems:
        raise DoneContractError("contract lint failed:\n  - " + "\n  - ".join(problems))
    if contract_state(repo, task) in ("closed", "abandoned"):
        raise DoneContractError(f"contract '{task}' is {contract_state(repo, task)}")
    interactive = bool(getattr(stdin, "isatty", lambda: False)())
    if not interactive and not os.environ.get("DONE_CONTRACT_APPROVE_NO_TTY"):
        raise NotInteractive(
            "approve must be run by a person in an interactive terminal "
            "(stdin is not a TTY). Agents must not approve their own contract.")
    current_tree = working_tree_hash(repo)
    dirty = changed_paths(repo, contract["baseline_tree"], current_tree)
    if dirty and not accept_dirty:
        raise DoneContractError(
            "working tree changed since init (work before approval): " + ", ".join(dirty[:20])
            + ("..." if len(dirty) > 20 else "")
            + "\nreview those changes; approve with --accept-dirty to record them as pre-approval work")
    sha = contract_sha(contract)
    stdout.write(render_contract_summary(contract) + "\n")
    if dirty:
        stdout.write(f"WARNING: {len(dirty)} path(s) changed before approval (recorded): {', '.join(dirty[:10])}\n")
    stdout.write(f"contract sha256: {sha}\n")
    if interactive and not assume_yes:
        stdout.write("Approve this contract? [y/N] ")
        stdout.flush()
        answer = stdin.readline().strip().lower()
        if answer not in ("y", "yes"):
            raise DoneContractError("approval declined")
    record = {
        "sha256": sha,
        "task": task,
        "repo": str(repo),
        "approved_at": now_iso(),
        "approver": approver or os.environ.get("USER") or "unknown",
        "interactive": interactive,
        "tree_at_approval": current_tree,
        "pre_approval_changes": dirty,
        "items": [{"id": it["id"], "strength": strength_of(it["check"]), "cacheable": item_cacheable(it)} for it in contract["items"]],
        "contract": contract,
    }
    write_json(approval_path(sha), record, mode=0o600)
    log_event({"event": "approve", "repo": str(repo), "task": task, "sha": sha, "interactive": interactive,
               "pre_approval_changes": len(dirty)})
    return record


def render_contract_summary(contract: dict) -> str:
    lines = [f"task: {contract.get('task')}",
             "request: " + str(contract.get("request", "")).strip().replace("\n", " ")[:400]]
    lines.append("items:")
    for it in contract.get("items", []):
        check = str(it.get("check", ""))
        cache = "cached when tree unchanged" if item_cacheable(it) else "re-run every time"
        lines.append(f"  {it.get('id')}: {it.get('text')}")
        lines.append(f"      check [{strength_of(check)}, {cache}]: {check}")
        if it.get("expect"):
            lines.append(f"      expect: {it['expect']}")
    if contract.get("repo_checks"):
        lines.append("repo_checks: " + "; ".join(contract["repo_checks"]))
    lines.append(f"protected: {', '.join(contract.get('protected', [])) or '(none)'}")
    if contract.get("allow_protected_changes"):
        lines.append("allow_protected_changes: true — the agent may add, change AND delete files under protected paths")
    else:
        lines.append("allow_protected_changes: false — any change under protected paths blocks")
    weak = [it["id"] for it in contract.get("items", []) if strength_of(str(it.get("check", ""))) in ("existence", "other")]
    if weak:
        lines.append(f"note: items with weak checks (existence/other): {', '.join(weak)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- marks

def load_marks(repo: Path, task: str) -> dict:
    data = read_json(task_dir(repo, task) / "marks.json")
    if not data or not isinstance(data, dict):
        data = {"items": {}, "paused": None, "closed_at": None}
    data.setdefault("items", {})
    data.setdefault("paused", None)
    data.setdefault("closed_at", None)
    return data


def save_marks(repo: Path, task: str, marks: dict) -> None:
    write_json(task_dir(repo, task) / "marks.json", marks)


def marks_digest(marks: dict) -> str:
    return sha256_bytes(canonical_json(marks))


def set_mark(repo: Path, task: str, item_id: str, status: str, reason: str | None) -> dict:
    contract = load_contract(repo, task)
    ids = {it["id"] for it in contract.get("items", []) if isinstance(it, dict) and "id" in it}
    if item_id not in ids:
        raise DoneContractError(f"unknown item {item_id}; known: {', '.join(sorted(ids))}")
    if status not in ("blocked", "open"):
        raise DoneContractError("status must be blocked or open")
    if status == "blocked" and not (reason and reason.strip()):
        raise DoneContractError("blocked requires --reason")
    with repo_lock(repo):
        marks = load_marks(repo, task)
        if marks.get("closed_at"):
            raise DoneContractError("contract is closed")
        if status == "open":
            marks["items"].pop(item_id, None)
        else:
            marks["items"][item_id] = {"status": "blocked", "reason": reason.strip(), "at": now_iso()}
        save_marks(repo, task, marks)
    log_event({"event": "mark", "repo": str(repo), "task": task, "item": item_id, "status": status, "reason": reason})
    return marks


def set_paused(repo: Path, task: str, reason: str | None) -> dict:
    with repo_lock(repo):
        marks = load_marks(repo, task)
        if marks.get("closed_at"):
            raise DoneContractError("contract is closed")
        if reason is None:
            marks["paused"] = None
        else:
            if not reason.strip():
                raise DoneContractError("pause requires --reason")
            marks["paused"] = {"reason": reason.strip(), "at": now_iso()}
        save_marks(repo, task, marks)
    log_event({"event": "pause" if reason else "resume", "repo": str(repo), "task": task, "reason": reason})
    return marks


def is_closed(repo: Path, task: str) -> bool:
    return bool(load_marks(repo, task).get("closed_at"))


# ---------------------------------------------------------------- running checks

def run_command(cmd: str, cwd: Path, timeout: int, tail_lines: int = 40, tail_bytes: int = 4000) -> dict:
    env = dict(os.environ)
    env.setdefault("CI", "1")
    env["DONE_CONTRACT"] = "1"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    started = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    output = ""
    proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, text=True, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            output, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            output = ""
    duration = round(time.monotonic() - started, 2)
    output = output or ""
    lines = output.splitlines()[-tail_lines:]
    tail = "\n".join(lines)[-tail_bytes:]
    return {"command": cmd, "exit": exit_code, "timed_out": timed_out, "duration_s": duration, "output_tail": tail,
            "output_sha256": sha256_bytes(output.encode("utf-8", "replace"))}


def _hmac_key() -> bytes:
    path = home_dir() / "key"
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        key = secrets.token_hex(32).encode("ascii")
        with open(path, "wb") as fh:
            fh.write(key + b"\n")
        os.chmod(path, 0o600)
        return key


def sign_evidence(evidence: dict) -> str:
    """HMAC over the evidence. Detects hand edits and corruption; the key is readable by
    the user account, so this is tamper-evident, not a security boundary."""
    body = {k: v for k, v in evidence.items() if k != "hmac"}
    return hmac.new(_hmac_key(), canonical_json(body), hashlib.sha256).hexdigest()


def evidence_path(repo: Path, task: str) -> Path:
    return task_dir(repo, task) / "evidence.json"


def load_evidence(repo: Path, task: str) -> dict | None:
    data = read_json(evidence_path(repo, task))
    return data if isinstance(data, dict) else None


def evidence_is_current(repo: Path, task: str, ev: dict | None, contract: dict, marks: dict, tree: str) -> bool:
    """Shared validity rule for reuse/close: same task, contract, approval, marks, tree, intact HMAC."""
    if not ev:
        return False
    try:
        return (ev.get("task") == task and ev.get("repo") == str(repo)
                and ev.get("contract_sha256") == contract_sha(contract)
                and find_approval(repo, contract) is not None
                and ev.get("marks_sha256") == marks_digest(marks)
                and ev.get("tree") == tree
                and ev.get("tool_version") == VERSION
                and ev.get("verdict") not in (VERDICT_UNAPPROVED, VERDICT_ERROR, VERDICT_STALE)
                and ev.get("hmac") == sign_evidence(ev))
    except Exception:
        return False


def compute_verdict(item_results: list[dict], repo_results: list[dict], protected_changed: list[str],
                    allow_protected: bool, paused: dict | None, stale: bool) -> str:
    """Precedence: STALE > ERROR > (paused → PAUSED) > FAIL > TESTS_CHANGED > INCOMPLETE > PASS."""
    if stale:
        return VERDICT_STALE
    if any(r["status"] == "ERROR" for r in item_results) or any(r["status"] == "ERROR" for r in repo_results):
        return VERDICT_ERROR
    if paused:
        return VERDICT_PAUSED
    failing = [r for r in item_results if r["status"] == "FAIL"]
    repo_fail = [r for r in repo_results if r["status"] != "PASS"]
    if failing or repo_fail:
        return VERDICT_FAIL
    if protected_changed and not allow_protected:
        return VERDICT_TESTS_CHANGED
    if any(r["status"] == "BLOCKED" for r in item_results):
        return VERDICT_INCOMPLETE
    return VERDICT_PASS


def _run_item(it: dict, repo: Path) -> tuple[bool, bool, dict]:
    res = run_command(it["check"], repo, int(it.get("timeout", 300)))
    passed = res["exit"] == 0 and not res["timed_out"]
    expect = it.get("expect")
    expect_ok = True
    if passed and expect:
        expect_ok = expect in res["output_tail"] or _expect_in_full(it, repo, expect)
        passed = passed and expect_ok
    return passed, expect_ok, res


def run_check(repo: Path, task: str, *, reuse: bool = True, session: str | None = None,
              budget_s: float | None = None) -> dict:
    with repo_lock(repo):
        return _run_check_locked(repo, task, reuse=reuse, session=session, budget_s=budget_s)


def _run_check_locked(repo: Path, task: str, *, reuse: bool, session: str | None, budget_s: float | None) -> dict:
    started = time.monotonic()
    contract = load_contract(repo, task)
    csha = contract_sha(contract)
    approval = find_approval(repo, contract)
    marks = load_marks(repo, task)
    msha = marks_digest(marks)
    tree_before = working_tree_hash(repo)
    base = {
        "tool": "done-contract", "tool_version": VERSION, "task": task, "repo": str(repo),
        "contract_sha256": csha, "tree": tree_before, "baseline_tree": contract.get("baseline_tree"),
        "marks_sha256": msha, "session": session,
    }
    if approval is None:
        evidence = {**base, "verdict": VERDICT_UNAPPROVED, "checked_at": now_iso(), "items": [], "repo_checks": [],
                    "changed_paths": [], "protected_changed": [], "reused": False, "approval": None, "paused": marks.get("paused")}
        evidence["hmac"] = sign_evidence(evidence)
        write_evidence(repo, task, evidence)
        return evidence
    lint_problems = lint_contract(contract)
    if lint_problems:
        raise DoneContractError("approved contract fails lint (file changed?): " + "; ".join(lint_problems))
    prev = load_evidence(repo, task) if reuse else None
    prev_ok = evidence_is_current(repo, task, prev, contract, marks, tree_before)
    prev_items = {it["id"]: it for it in (prev or {}).get("items", [])} if prev_ok else {}
    prev_repo = {rc["command"]: rc for rc in (prev or {}).get("repo_checks", [])} if prev_ok else {}

    def over_budget() -> bool:
        return budget_s is not None and (time.monotonic() - started) > budget_s

    item_results = []
    for it in contract["items"]:
        mark = marks["items"].get(it["id"])
        cached = prev_items.get(it["id"])
        if cached and item_cacheable(it) and cached.get("status") in ("PASS", "FAIL", "BLOCKED") and cached.get("command") == it["check"]:
            res = {k: cached.get(k) for k in ("command", "exit", "timed_out", "duration_s", "output_tail", "output_sha256")}
            passed = cached["status"] == "PASS"
            expect_ok = cached.get("expect_matched")
            reused = True
        elif over_budget():
            item_results.append({"id": it["id"], "text": it["text"], "strength": strength_of(it["check"]), "status": "ERROR",
                                 "error": "not run: stop-hook time budget exhausted; run `done-contract check` manually",
                                 "command": it["check"], "exit": None, "timed_out": False, "duration_s": 0,
                                 "output_tail": "", "output_sha256": None, "blocked_reason": None,
                                 "expect": it.get("expect"), "expect_matched": None, "reused": False})
            continue
        else:
            passed, expect_ok, res = _run_item(it, repo)
            reused = False
        if passed:
            status = "PASS"
        elif mark and mark.get("status") == "blocked":
            status = "BLOCKED"
        else:
            status = "FAIL"
        item_results.append({
            "id": it["id"], "text": it["text"], "strength": strength_of(it["check"]), "status": status,
            "blocked_reason": (mark or {}).get("reason") if status == "BLOCKED" else None,
            "expect": it.get("expect"), "expect_matched": (expect_ok if it.get("expect") else None),
            "reused": reused, **res,
        })
    repo_results = []
    for cmd in contract.get("repo_checks", []):
        cached = prev_repo.get(cmd)
        if cached and strength_of(cmd) in CACHEABLE_STRENGTHS and cached.get("status") in ("PASS", "FAIL"):
            repo_results.append({**cached, "reused": True})
        elif over_budget():
            repo_results.append({"status": "ERROR", "error": "not run: time budget exhausted", "command": cmd, "exit": None,
                                 "timed_out": False, "duration_s": 0, "output_tail": "", "output_sha256": None, "reused": False})
        else:
            res = run_command(cmd, repo, 900)
            repo_results.append({"status": "PASS" if res["exit"] == 0 and not res["timed_out"] else "FAIL", "reused": False, **res})
    tree_after = working_tree_hash(repo)
    stale = tree_after != tree_before
    changed = changed_paths(repo, contract["baseline_tree"], tree_before)
    protected_changed = [p for p in changed if matches_any(p, contract.get("protected", []))]
    verdict = compute_verdict(item_results, repo_results, protected_changed,
                              bool(contract.get("allow_protected_changes")), marks.get("paused"), stale)
    evidence = {
        **base,
        "verdict": verdict,
        "checked_at": now_iso(),
        "duration_s": round(time.monotonic() - started, 2),
        "tree_after": tree_after,
        "stale_paths": changed_paths(repo, tree_before, tree_after) if stale else [],
        "approval": {"approved_at": approval.get("approved_at"), "approver": approval.get("approver"),
                     "interactive": approval.get("interactive"),
                     "pre_approval_changes": approval.get("pre_approval_changes", [])},
        "items": item_results,
        "repo_checks": repo_results,
        "changed_paths": changed,
        "protected_changed": protected_changed,
        "allow_protected_changes": bool(contract.get("allow_protected_changes")),
        "paused": marks.get("paused"),
        "reused": all(r.get("reused") for r in item_results + repo_results) and bool(item_results),
    }
    evidence["hmac"] = sign_evidence(evidence)
    write_evidence(repo, task, evidence)
    log_event({"event": "check", "repo": str(repo), "task": task, "verdict": verdict, "tree": tree_before, "session": session})
    return evidence


def _expect_in_full(item: dict, repo: Path, expect: str) -> bool:
    """Re-run only when the tail missed the expected text (rare, large outputs)."""
    res = run_command(item["check"], repo, int(item.get("timeout", 300)), tail_lines=10_000_000, tail_bytes=50_000_000)
    return res["exit"] == 0 and not res["timed_out"] and expect in res["output_tail"]


def write_evidence(repo: Path, task: str, evidence: dict) -> None:
    write_json(evidence_path(repo, task), evidence)
    (task_dir(repo, task) / "evidence.md").write_text(render_evidence_md(evidence), encoding="utf-8")


def mark_released(repo: Path, task: str, reason: str, session: str | None) -> dict | None:
    """Record that the Stop hook let the agent stop without a passing verdict."""
    ev = load_evidence(repo, task)
    if not ev:
        return None
    ev["released"] = {"reason": reason, "at": now_iso(), "session": session, "verdict_at_release": ev.get("verdict")}
    ev["hmac"] = sign_evidence(ev)
    write_evidence(repo, task, ev)
    return ev


def render_evidence_md(ev: dict) -> str:
    lines = [f"# done-contract evidence — {ev.get('task')}", "",
             f"- verdict: **{ev.get('verdict')}**" + ("  (all results reused)" if ev.get("reused") else ""),
             f"- checked_at: {ev.get('checked_at')}  duration: {ev.get('duration_s')}s",
             f"- tree: `{ev.get('tree')}`  baseline: `{ev.get('baseline_tree')}`",
             f"- contract sha256: `{ev.get('contract_sha256')}`"]
    ap = ev.get("approval")
    if ap:
        lines.append(f"- approval: {ap.get('approved_at')} by {ap.get('approver')}"
                     + (f" (pre-approval changes: {len(ap.get('pre_approval_changes') or [])})" if ap.get("pre_approval_changes") else ""))
    else:
        lines.append("- approval: **none**")
    if ev.get("paused"):
        lines.append(f"- paused: {ev['paused'].get('reason')} ({ev['paused'].get('at')})")
    if ev.get("released"):
        r = ev["released"]
        lines.append(f"- **released without completion**: {r.get('reason')} at {r.get('at')} (verdict then: {r.get('verdict_at_release')})")
    if ev.get("verdict") == VERDICT_STALE:
        lines.append("- working tree changed while checks ran: " + ", ".join(ev.get("stale_paths", [])[:20]))
    lines.append("")
    if ev.get("items"):
        lines.append("| item | status | strength | exit | time | check |")
        lines.append("|---|---|---|---|---|---|")
        for it in ev["items"]:
            exit_s = "timeout" if it.get("timed_out") else ("not run" if it["status"] == "ERROR" else str(it.get("exit")))
            flag = " (cached)" if it.get("reused") else ""
            lines.append(f"| {it['id']} {it['text']} | {it['status']}{flag} | {it['strength']} | {exit_s} | {it.get('duration_s')}s | `{it['command']}` |")
        for it in ev["items"]:
            if it["status"] == "BLOCKED":
                lines.append(f"\n- {it['id']} blocked: {it.get('blocked_reason')}")
            if it["status"] == "ERROR":
                lines.append(f"\n- {it['id']} error: {it.get('error')}")
            if it["status"] == "FAIL" and it.get("output_tail"):
                lines.append(f"\n<details><summary>{it['id']} output</summary>\n\n```\n{it['output_tail']}\n```\n</details>")
    if ev.get("repo_checks"):
        lines.append("")
        lines.append("repo checks:")
        for rc in ev["repo_checks"]:
            lines.append(f"- {rc['status']} `{rc['command']}` exit {rc.get('exit')} {rc.get('duration_s')}s" + (" (cached)" if rc.get("reused") else ""))
    if ev.get("protected_changed"):
        lines.append("")
        lines.append("protected paths changed since baseline" + (" (allowed)" if ev.get("allow_protected_changes") else " (**blocks**)") + ":")
        for p in ev["protected_changed"]:
            lines.append(f"- {p}")
    return "\n".join(lines) + "\n"


def summarize_evidence(ev: dict) -> str:
    items = ev.get("items", [])
    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    parts = [f"{k} {v}" for k, v in sorted(counts.items())]
    s = f"done-contract {ev.get('task')}: {ev.get('verdict')} (items: {', '.join(parts) or 'none'}"
    if ev.get("protected_changed"):
        s += f"; protected changed: {len(ev['protected_changed'])}"
    if ev.get("paused"):
        s += f"; paused: {ev['paused'].get('reason')}"
    blocked = [it for it in items if it["status"] == "BLOCKED"]
    if blocked:
        s += "; blocked: " + "; ".join(f"{it['id']} {it.get('blocked_reason')}" for it in blocked)
    s += f"; tree {str(ev.get('tree'))[:12]})"
    return s


def close_contract(repo: Path, task: str) -> dict:
    with repo_lock(repo):
        contract = load_contract(repo, task)
        marks = load_marks(repo, task)
        if marks.get("closed_at"):
            raise DoneContractError("already closed")
        ev = load_evidence(repo, task)
        tree = working_tree_hash(repo)
        if not evidence_is_current(repo, task, ev, contract, marks, tree) or ev.get("verdict") not in ALLOW_STOP_VERDICTS:
            raise DoneContractError(
                "close needs current evidence (same contract, approval, marks and tree) with verdict PASS, INCOMPLETE or PAUSED; "
                "run `done-contract check` first")
        marks["closed_at"] = now_iso()
        marks["closed_verdict"] = ev["verdict"]
        save_marks(repo, task, marks)
        if active_task(repo) == task:
            set_active(repo, None)
    log_event({"event": "close", "repo": str(repo), "task": task, "verdict": ev.get("verdict"), "tree": tree})
    return ev


def verify_evidence(repo: Path, task: str) -> dict:
    """Re-run every command recorded in the evidence (no cache) and compare statuses."""
    ev = load_evidence(repo, task)
    if not ev or ev.get("verdict") in (VERDICT_UNAPPROVED, None):
        raise DoneContractError("no evidence to verify; run `done-contract check` first")
    tree = working_tree_hash(repo)
    rows = []
    agree = True
    for it in ev.get("items", []):
        if it["status"] == "ERROR":
            rows.append({"id": it["id"], "recorded": "ERROR", "now": "skipped", "agree": False, "exit": None})
            agree = False
            continue
        res = run_command(it["command"], repo, 300)
        passed = res["exit"] == 0 and not res["timed_out"] and (not it.get("expect") or it["expect"] in res["output_tail"])
        recorded = it["status"]
        now = "PASS" if passed else "FAIL"
        same = (recorded == "PASS") == passed
        agree = agree and same
        rows.append({"id": it["id"], "recorded": recorded, "now": now, "agree": same, "exit": res["exit"]})
    for rc in ev.get("repo_checks", []):
        res = run_command(rc["command"], repo, 900)
        now = "PASS" if res["exit"] == 0 and not res["timed_out"] else "FAIL"
        same = rc["status"] == now
        agree = agree and same
        rows.append({"id": f"repo:{rc['command']}", "recorded": rc["status"], "now": now, "agree": same, "exit": res["exit"]})
    return {"task": task, "evidence_tree": ev.get("tree"), "current_tree": tree, "same_tree": ev.get("tree") == tree,
            "hmac_valid": ev.get("hmac") == sign_evidence(ev), "rows": rows, "agree": agree}
