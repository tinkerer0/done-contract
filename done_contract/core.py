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
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.4.4"
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

# strength labels are receipt information only; they never decide caching
STRENGTH_RULES = [
    ("http", re.compile(r"(^|[\s;&|(])(curl|wget|http|xh|nc|ping|ssh|scp|rsync)\b")),
    ("test", re.compile(
        r"(^|[\s;&|(])(pytest|python3?\s+-m\s+(pytest|unittest)|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|"
        r"yarn\s+test|bun\s+test|cargo\s+test|go\s+test|npx\s+(jest|vitest|mocha)|jest|vitest|mocha|"
        r"swift\s+test|dotnet\s+test|gradlew?\s+test|mvn\s+test|make\s+test|rspec|phpunit|"
        r"unittest\s+discover)\b")),
    ("build", re.compile(
        r"(^|[\s;&|(])(cargo\s+(build|check|clippy)|go\s+(build|vet)|npm\s+run\s+(build|lint|typecheck)|"
        r"npx\s+tsc|tsc|swift\s+build|mypy|pyright|ruff|eslint|flake8|make\s+(build|lint))\b")),
    ("content", re.compile(r"(^|[\s;&|(])(grep|rg|diff|cmp|jq|yq|python3?\s+-c)\b")),
    ("existence", re.compile(r"(^|[\s;&|(])(test\s+-[efdsx]|\[\s+-[efdsx]|ls|stat|file|which)\b")),
]

TASK_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
ITEM_KEYS = {"id", "text", "check", "expect", "timeout", "cache", "watch"}
CONTRACT_KEYS = {"version", "task", "request", "created_at", "baseline_head", "baseline_tree", "items",
                 "repo_checks", "repo_watch", "protected", "allow_protected_changes"}

# a test runner invoked with no target: the whole suite for one item (warning, not an error)
BROAD_CHECK_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(pytest|python3?\s+-m\s+pytest|python3?\s+-m\s+unittest(\s+discover)?|npm\s+(run\s+)?test|"
    r"pnpm\s+(run\s+)?test|yarn\s+test|bun\s+test|cargo\s+test|go\s+test\s+\./\.\.\.|make\s+test|swift\s+test|dotnet\s+test|"
    r"gradlew?\s+test|mvn\s+test|rspec|phpunit)(\s+-[A-Za-z]+)*\s*$")
SLOW_CHECK_DEFAULT_S = 30.0

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

DEFAULT_ITEM_TIMEOUT = 300
REPO_CHECK_TIMEOUT = 900
MAX_OUTPUT_SEARCH_BYTES = 200 * 1024 * 1024


class DoneContractError(Exception):
    pass


class GitError(DoneContractError):
    pass


class NotInteractive(DoneContractError):
    pass


class LockBusy(DoneContractError):
    pass


# ---------------------------------------------------------------- utilities

def _counter(items) -> dict:
    """Minimal multiset so repo_checks compare by command AND count (no collections import)."""
    out: dict = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


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
    """Atomic replace through a uniquely named temp file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def home_dir() -> Path:
    override = os.environ.get("DONE_CONTRACT_HOME")
    base = Path(override).expanduser() if override else Path.home() / CONTRACT_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(base, 0o700)
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
def repo_lock(repo: Path, timeout: float | None = None):
    """Serialize state changes within one worktree (two sessions, one .done-contract/).

    With a timeout the wait is bounded and counts against the caller's budget;
    an exhausted wait raises LockBusy instead of producing stale results."""
    root = contract_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    ensure_excluded(repo)  # before the lock file exists, so it never enters a tree hash
    with open(root / ".lock", "a+") as fh:
        if timeout is None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + max(0.0, float(timeout))
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockBusy("another done-contract run holds the worktree lock; the time budget ran out while waiting")
                    time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------- git

def git(repo: Path, *args: str, env: dict | None = None, check: bool = True, binary: bool = False):
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=not binary, env=env)
    if check and proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        raise GitError(f"git {' '.join(args)}: {err.strip()}")
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


def working_tree_hash(repo: Path, *, verify_cache: bool = False) -> str:
    """Hash of the whole working tree: tracked and untracked files, .gitignore and
    info/exclude respected, symlinks as blobs (link text), submodules as gitlinks.

    Uses a temporary index so the user's index is untouched. The tree object is
    stored in the object database so later `git diff <tree> <tree>` works.
    Not covered: symlink targets outside the repo, ignored files, submodule contents.
    """
    # A private index under .done-contract/ keeps git's stat cache between runs, so only
    # files that changed get re-hashed (a fresh index re-hashes every file: ~10 s on a
    # 20k-file tree). Each run works on its own copy and publishes it atomically, so
    # concurrent runs can only make the cache slightly stale, never corrupt it.
    # The copy keeps the index file's own timestamp (copy2): git's racy-stat guard
    # re-reads files modified in the same second as that timestamp, which a newer
    # timestamp would silently disable. The cache is rebuilt from HEAD whenever HEAD
    # or any ignore rule changes, because `git add -A` never drops an entry that merely
    # became ignored.
    # The cache file's name carries its generation (HEAD + ignore sources + format), so an
    # index can never be paired with the wrong key. With verify_cache=True (close, verify)
    # the result is cross-checked against a throwaway index; on any disagreement the cache
    # is discarded and the fresh value wins, and the event is logged.
    root = contract_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    ensure_excluded(repo)  # the index cache must never enter the tree it hashes
    key = _index_cache_key(repo)
    cache = root / f".index.{key[:32]}"
    fd, work = tempfile.mkstemp(prefix=".work.", dir=str(root))
    os.close(fd)
    try:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = work
        copied = False
        if cache.exists():
            try:
                shutil.copy2(cache, work)
                copied = True
            except FileNotFoundError:
                copied = False  # another run's generation cleanup removed it between the check and the copy: cache miss
        if not copied:
            os.unlink(work)  # let git create a valid index file itself
            if head_sha(repo):
                git(repo, "read-tree", "HEAD", env=env)
            else:
                git(repo, "read-tree", "--empty", env=env)
        git(repo, *GIT_STAT_OPTS, "add", "-A", "--", ".", env=env)
        tree = git(repo, "write-tree", env=env).strip()
        os.replace(work, cache)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(work)
        raise
    for stale in root.glob(".index.*"):  # other generations: HEAD moved, ignore rules or format changed
        if stale.name != cache.name:
            with contextlib.suppress(OSError):
                stale.unlink()
    if verify_cache:
        fresh = fresh_tree_hash(repo)
        if fresh != tree:
            log_event({"event": "index_cache_mismatch", "repo": str(repo), "cached": tree, "fresh": fresh})
            with contextlib.suppress(OSError):
                cache.unlink()
            return fresh
    return tree


INDEX_CACHE_FORMAT = "3"  # bump whenever the way the private index is built changes
GIT_STAT_OPTS = ("-c", "core.checkStat=default", "-c", "core.trustctime=true", "-c", "core.ignoreStat=false")


def fresh_tree_hash(repo: Path) -> str:
    """Reference hash with a throwaway index: every file is re-read. Slow on big trees."""
    ensure_excluded(repo)
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = os.path.join(td, "index")
        if head_sha(repo):
            git(repo, "read-tree", "HEAD", env=env)
        else:
            git(repo, "read-tree", "--empty", env=env)
        git(repo, *GIT_STAT_OPTS, "add", "-A", "--", ".", env=env)
        return git(repo, "write-tree", env=env).strip()


def _index_cache_key(repo: Path) -> str:
    """HEAD plus every ignore source git reads: when any of them changes the private index is rebuilt.

    Sources: every .gitignore in the tree (also ones that are themselves ignored: git still
    reads them), the repo's info/exclude, core.excludesFile resolved the way git does
    (relative to the repo), or git's default global ignore file when it is not set."""
    h = hashlib.sha256()
    h.update(INDEX_CACHE_FORMAT.encode("utf-8"))
    h.update((head_sha(repo) or "unborn").encode("utf-8"))
    seen: set[bytes] = set()
    for args in (("-co", "--exclude-standard"), ("-o", "-i", "--exclude-standard")):
        listing = git(repo, "ls-files", *args, "-z", "--", ".gitignore", ":(glob)**/.gitignore", check=False, binary=True)
        seen.update(p for p in listing.split(b"\0") if p)
    for raw in sorted(seen):
        h.update(b"\0" + raw)
        with contextlib.suppress(OSError):
            h.update(Path(repo, raw.decode("utf-8", "replace")).read_bytes())
    sources: list[Path] = []
    with contextlib.suppress(GitError):
        ex = git(repo, "rev-parse", "--git-path", "info/exclude").strip()
        sources.append(Path(ex) if os.path.isabs(ex) else repo / ex)
    extra = git(repo, "config", "--get", "core.excludesFile", check=False).strip()
    if extra:
        p = Path(os.path.expanduser(extra))
        sources.append(p if p.is_absolute() else repo / p)
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(str(Path.home()), ".config")
        sources.append(Path(xdg) / "git" / "ignore")
    for src in sources:
        h.update(b"\0" + str(src).encode("utf-8"))
        with contextlib.suppress(OSError):
            h.update(src.read_bytes())
    return h.hexdigest()


def changed_paths(repo: Path, tree_a: str, tree_b: str) -> list[str]:
    """Paths that differ between two trees. NUL-separated, no quoting, renames as delete+add."""
    if tree_a == tree_b:
        return []
    out = git(repo, "-c", "core.quotePath=false", "diff", "--name-only", "-z", "--no-renames", tree_a, tree_b, binary=True)
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def ensure_excluded(repo: Path) -> None:
    """Keep .done-contract/ out of git without touching the user's .gitignore.

    Works for .git directories, .git files (linked worktrees, separate git dirs)
    through `git rev-parse --git-path`. Failure is an error, not silence."""
    out = git(repo, "rev-parse", "--git-path", "info/exclude").strip()
    exclude = Path(out)
    if not exclude.is_absolute():
        exclude = repo / exclude
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        line = f"{CONTRACT_DIRNAME}/"
        if line not in existing.splitlines():
            with open(exclude, "a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(line + "\n")
    except OSError as exc:
        raise GitError(f"cannot write {exclude}: {exc}") from exc


# ---------------------------------------------------------------- globs and paths

def normalize_glob(pattern: str) -> str:
    """Repo-relative POSIX form: `./a` → `a`, `a//b` → `a/b`, `dir/` → `dir/**`."""
    p = pattern.replace(os.sep, "/")
    while p.startswith("./"):
        p = p[2:]
    while "//" in p:
        p = p.replace("//", "/")
    if p.endswith("/"):
        p += "**"
    return p


def glob_problem(pattern: str) -> str | None:
    """Why a pattern is not accepted: only `*`, `**`, `?` and `[…]` classes are supported."""
    p = normalize_glob(pattern)
    if not p.strip():
        return "empty pattern"
    if "{" in p or "}" in p:
        return "brace sets like {a,b} are not supported; list each pattern separately"
    i = 0
    while i < len(p):
        if p[i] == "[":
            j = p.find("]", i + 2)
            if j < 0:
                return "unbalanced '[' (write '[[]' for a literal bracket)"
            i = j + 1
        else:
            i += 1
    try:
        glob_to_regex(p)
    except re.error as exc:
        return f"invalid pattern: {exc}"
    return None


def glob_to_regex(pattern: str) -> re.Pattern:
    pattern = normalize_glob(pattern)
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
        elif c == "[":
            j = pattern.find("]", i + 2)
            if j < 0:
                out += re.escape(c)
            else:
                body = pattern[i + 1:j]
                negate = body.startswith(("!", "^"))
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\").replace("]", "\\]").replace("[", "\\[")
                out += "[" + ("^" if negate else "") + body + "]"
                i = j + 1
                continue
        else:
            out += re.escape(c)
        i += 1
    # DOTALL: a newline inside a file name is a valid path character; fullmatch below
    return re.compile(out, re.DOTALL)


def normalize_rel(path: str) -> str:
    norm = path.replace(os.sep, "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def matches_any(path: str, globs: list[str]) -> bool:
    norm = normalize_rel(path)
    return any(glob_to_regex(g).fullmatch(norm) for g in globs)


def repo_relative(repo: Path, candidate: str | Path, cwd: Path | None = None) -> tuple[str | None, str | None]:
    """(lexical, resolved) repo-relative POSIX paths for a file reference.

    lexical: normalized without following symlinks (what the agent names).
    resolved: after symlink resolution. Either may be None when outside the repo."""
    p = Path(candidate)
    if not p.is_absolute():
        p = (cwd or repo) / p
    repo_r = repo.resolve()
    lexical = None
    resolved = None
    try:
        lexical = Path(os.path.normpath(str(p))).relative_to(repo).as_posix()
    except ValueError:
        try:
            lexical = Path(os.path.normpath(str(p))).relative_to(repo_r).as_posix()
        except ValueError:
            lexical = None
    try:
        resolved = p.resolve().relative_to(repo_r).as_posix()
    except (ValueError, OSError):
        resolved = None
    return lexical, resolved


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
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
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
    """Only an explicit, human-approved `cache: true` allows reuse for an unchanged tree."""
    return item.get("cache") is True


def reuse_policy(item: dict) -> str:
    """'watch' (re-run only when a watched path changed), 'tree' (re-run when anything
    changed), or 'always' (re-run every time). Both reuse modes are human-approved."""
    if isinstance(item.get("watch"), list) and item["watch"]:
        return "watch"
    if item_cacheable(item):
        return "tree"
    return "always"


def can_reuse(policy_watch: list[str] | None, policy: str, changed_since_prev: list[str] | None) -> tuple[bool, str | None]:
    """changed_since_prev is None when there is no compatible previous run."""
    if changed_since_prev is None or policy == "always":
        return False, None
    if policy == "tree":
        return (not changed_since_prev), ("tree unchanged" if not changed_since_prev else None)
    hits = [p for p in changed_since_prev if matches_any(p, policy_watch or [])]
    if hits:
        return False, None
    return True, "no watched path changed"


def is_broad_check(check: str) -> bool:
    return bool(BROAD_CHECK_RE.match(check))


def lint_warnings(contract: dict) -> list[str]:
    """Non-fatal advice shown at approval: cost and scope of the checks."""
    warnings: list[str] = []
    for it in contract.get("items", []) or []:
        if not isinstance(it, dict):
            continue
        check = str(it.get("check", ""))
        if is_broad_check(check):
            warnings.append(f"{it.get('id')}: '{check.strip()}' runs the whole suite for one item; point it at the item's test file, "
                            "or put the suite in repo_checks")
        if reuse_policy(it) == "always":
            warnings.append(f"{it.get('id')}: no watch/cache — this check runs every time the agent stops; add \"watch\": [paths it depends on] if its result depends on repository files only")
    if contract.get("repo_checks") and not contract.get("repo_watch"):
        warnings.append("repo_checks without repo_watch run every time the agent stops; add \"repo_watch\": [\"src/**\", \"tests/**\"] to run them only when code changed")
    return warnings


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
        timeout = item.get("timeout", DEFAULT_ITEM_TIMEOUT)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0 or timeout > 3600:
            problems.append(f"{where}.timeout must be an integer 1..3600 (seconds)")
        expect = item.get("expect")
        if expect is not None and (not isinstance(expect, str) or not expect):
            problems.append(f"{where}.expect must be a non-empty string or null")
        if "cache" in item and not isinstance(item["cache"], bool):
            problems.append(f"{where}.cache must be true or false")
        if "watch" in item:
            if not isinstance(item["watch"], list) or not item["watch"] or not all(isinstance(g, str) and g.strip() for g in item["watch"]):
                problems.append(f"{where}.watch must be a non-empty list of path globs")
            else:
                for g in item["watch"]:
                    why = glob_problem(g)
                    if why:
                        problems.append(f"{where}.watch '{g}': {why}")
    repo_watch = contract.get("repo_watch")
    if repo_watch is not None:
        if not isinstance(repo_watch, list) or not repo_watch or not all(isinstance(g, str) and g.strip() for g in repo_watch):
            problems.append("repo_watch must be a non-empty list of path globs")
        else:
            for g in repo_watch:
                why = glob_problem(g)
                if why:
                    problems.append(f"repo_watch '{g}': {why}")
    for g in contract.get("protected", []) or []:
        if isinstance(g, str) and glob_problem(g):
            problems.append(f"protected '{g}': {glob_problem(g)}")
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
    if isinstance(contract, dict) and find_approval(repo, contract):
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
        with contextlib.suppress(FileNotFoundError):
            (tdir / "evidence.json").unlink()
        set_active(repo, task)
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
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d / f"{sha}.json"


def find_approval(repo: Path, contract: dict) -> dict | None:
    rec = read_json(approval_path(contract_sha(contract)))
    if not rec or not isinstance(rec, dict):
        return None
    if rec.get("repo") and Path(rec["repo"]).resolve() != Path(repo).resolve():
        return None
    # identity of this approval record: evidence from an earlier approval is never reused under a new one
    rec["approval_id"] = sha256_bytes(canonical_json({k: v for k, v in rec.items() if k != "approval_id"}))
    return rec


def approve_contract(repo: Path, task: str, *, approver: str | None = None, assume_yes: bool = False,
                     accept_dirty: bool = False, dry_run: bool = True, stdin=None, stdout=None) -> dict:
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
    shown_tree = working_tree_hash(repo)
    dirty = changed_paths(repo, contract["baseline_tree"], shown_tree)
    if dirty and not accept_dirty:
        raise DoneContractError(
            "working tree changed since init (work before approval): " + ", ".join(dirty[:20])
            + ("..." if len(dirty) > 20 else "")
            + "\nreview those changes; approve with --accept-dirty to record them as pre-approval work")
    sha = contract_sha(contract)
    timings = None
    if dry_run:
        timings = dry_run_checks(repo, contract)
        if working_tree_hash(repo) != shown_tree:
            raise DoneContractError("the checks themselves changed the working tree during the dry run; "
                                    "add their outputs to .gitignore or fix the checks, then run approve again")
    try:
        slow_s = float(os.environ.get("DONE_CONTRACT_SLOW_S", SLOW_CHECK_DEFAULT_S))
    except ValueError:
        slow_s = SLOW_CHECK_DEFAULT_S
    stdout.write(render_contract_summary(contract, timings, slow_s) + "\n")
    if dirty:
        stdout.write(f"WARNING: {len(dirty)} path(s) changed before approval (recorded): {', '.join(dirty[:10])}\n")
    stdout.write(f"contract sha256: {sha}\n")
    if interactive and not assume_yes:
        stdout.write("Approve this contract? [y/N] ")
        stdout.flush()
        answer = stdin.readline().strip().lower()
        if answer not in ("y", "yes"):
            raise DoneContractError("approval declined")
    with repo_lock(repo):
        # re-check what was shown: the contract and the tree must not have moved while the person was reading
        if contract_sha(load_contract(repo, task)) != sha:
            raise DoneContractError("contract changed while waiting for approval; review it and run approve again")
        if active_task(repo) != task:
            raise DoneContractError("active contract changed while waiting for approval; run approve again")
        final_tree = working_tree_hash(repo)
        if final_tree != shown_tree:
            moved = changed_paths(repo, shown_tree, final_tree)
            raise DoneContractError("working tree changed while waiting for approval: " + ", ".join(moved[:20])
                                    + "\nreview the changes and run approve again")
        record = {
            "sha256": sha,
            "task": task,
            "repo": str(repo),
            "approved_at": now_iso(),
            "approver": approver or os.environ.get("USER") or "unknown",
            "nonce": secrets.token_hex(8),  # every approval event is distinct, even within one second
            "interactive": interactive,
            "tree_at_approval": final_tree,
            "pre_approval_changes": dirty,
            "items": [{"id": it["id"], "strength": strength_of(it["check"]), "reuse_policy": reuse_policy(it),
                       "dry_run_s": (timings or {}).get(it["id"], {}).get("duration_s")} for it in contract["items"]],
            "warnings": lint_warnings(contract),
            "contract": contract,
        }
        write_json(approval_path(sha), record, mode=0o600)
    log_event({"event": "approve", "repo": str(repo), "task": task, "sha": sha, "interactive": interactive,
               "pre_approval_changes": len(dirty)})
    return record


def render_contract_summary(contract: dict, timings: dict | None = None, slow_s: float = SLOW_CHECK_DEFAULT_S) -> str:
    lines = [f"task: {contract.get('task')}",
             "request: " + str(contract.get("request", "")).strip().replace("\n", " ")[:400]]
    lines.append("items:")
    for it in contract.get("items", []):
        check = str(it.get("check", ""))
        policy = reuse_policy(it)
        if policy == "watch":
            when = "re-run when these change: " + ", ".join(it["watch"])
        elif policy == "tree":
            when = "re-run when anything in the tree changes"
        else:
            when = "re-run EVERY time the agent stops"
        timing = ""
        if timings and it.get("id") in timings:
            t = timings[it["id"]]
            timing = f" — {t['duration_s']}s {t.get('status', '')}".rstrip() + (" SLOW" if t["duration_s"] >= slow_s else "")
        lines.append(f"  {it.get('id')}: {it.get('text')}")
        lines.append(f"      check [{strength_of(check)}]: {check}")
        lines.append(f"      {when}{timing}")
        if it.get("expect"):
            lines.append(f"      expect: {it['expect']}")
    if contract.get("repo_checks"):
        when = ("re-run when these change: " + ", ".join(contract["repo_watch"])) if contract.get("repo_watch") else "re-run EVERY time the agent stops"
        lines.append("repo_checks (" + when + "): " + "; ".join(contract["repo_checks"]))
        if timings:
            for cmd in contract["repo_checks"]:
                t = timings.get("repo:" + cmd)
                if t:
                    lines.append(f"      {cmd}: {t['duration_s']}s {t.get('status', '')}".rstrip() + (" SLOW" if t["duration_s"] >= slow_s else ""))
    lines.append(f"protected: {', '.join(contract.get('protected', [])) or '(none)'}")
    if contract.get("allow_protected_changes"):
        lines.append("allow_protected_changes: true — the agent may add, change AND delete files under protected paths")
    else:
        lines.append("allow_protected_changes: false — any change under protected paths blocks")
    weak = [it["id"] for it in contract.get("items", []) if strength_of(str(it.get("check", ""))) in ("existence", "other")]
    if weak:
        lines.append(f"note: items with weak checks (existence/other): {', '.join(weak)}")
    for w in lint_warnings(contract):
        lines.append(f"warning: {w}")
    if timings:
        total = round(sum(t["duration_s"] for k, t in timings.items()), 1)
        lines.append(f"dry run: all checks together took {total}s; items without watch/cache repeat that cost at every stop")
    return "\n".join(lines)


def dry_run_checks(repo: Path, contract: dict) -> dict:
    """Run every check once (no evidence is written) to show the person what approval costs."""
    def status_of(res: dict) -> str:
        if res["timed_out"]:
            return "TIMEOUT"
        if res["exit"] != 0:
            return f"FAIL(exit {res['exit']})"
        if res.get("expect_matched") is False:
            return "FAIL(expect mismatch)"
        return "PASS"

    timings: dict[str, dict] = {}
    for it in contract.get("items", []):
        res = run_command(it["check"], repo, float(it.get("timeout", DEFAULT_ITEM_TIMEOUT)), expect=it.get("expect"))
        timings[it["id"]] = {"duration_s": res["duration_s"], "exit": res["exit"], "timed_out": res["timed_out"],
                             "expect_matched": res.get("expect_matched"), "status": status_of(res)}
    for cmd in contract.get("repo_checks", []):
        res = run_command(cmd, repo, float(REPO_CHECK_TIMEOUT))
        timings["repo:" + cmd] = {"duration_s": res["duration_s"], "exit": res["exit"], "timed_out": res["timed_out"],
                                  "expect_matched": None, "status": status_of(res)}
    return timings


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

def _search_file(path: str, needle: bytes) -> tuple[bool, bool]:
    """(found, truncated). truncated=True means the search stopped at MAX_OUTPUT_SEARCH_BYTES."""
    size = os.path.getsize(path)
    chunk = 8 * 1024 * 1024
    overlap = len(needle) - 1
    limit = min(size, MAX_OUTPUT_SEARCH_BYTES)
    with open(path, "rb") as fh:
        if size <= chunk and size <= limit:
            return (needle in fh.read(), False)
        prev = b""
        read = 0
        while read < limit:
            buf = fh.read(min(chunk, limit - read))
            if not buf:
                break
            read += len(buf)
            window = (prev[-overlap:] if overlap > 0 else b"") + buf
            if needle in window:
                return (True, False)
            prev = buf
    return (False, size > MAX_OUTPUT_SEARCH_BYTES)


def _tail_of_file(path: str, tail_lines: int, tail_bytes: int) -> str:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 64 * 1024))
        data = fh.read()
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()[-tail_lines:]
    return "\n".join(lines)[-tail_bytes:]


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for buf in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(buf)
    return h.hexdigest()


def run_command(cmd: str, cwd: Path, timeout: float, *, expect: str | None = None,
                tail_lines: int = 40, tail_bytes: int = 4000) -> dict:
    """Run one check exactly once. Output is streamed to a temp file; `expect` is
    searched over the full output; only a tail is kept in the evidence."""
    env = dict(os.environ)
    env.setdefault("CI", "1")
    env["DONE_CONTRACT"] = "1"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    timeout = max(0.01, float(timeout))
    started = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    fd, out_path = tempfile.mkstemp(prefix="done-contract-out-")
    try:
        with os.fdopen(fd, "wb") as out_fh:
            proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=out_fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
        duration = round(time.monotonic() - started, 2)
        tail = _tail_of_file(out_path, tail_lines, tail_bytes)
        expect_matched = None
        search_truncated = False
        if expect is not None:
            expect_matched, search_truncated = _search_file(out_path, expect.encode("utf-8"))
        return {"command": cmd, "exit": exit_code, "timed_out": timed_out, "timeout_s": round(timeout, 2),
                "duration_s": duration, "output_tail": tail, "output_bytes": os.path.getsize(out_path),
                "output_sha256": _hash_file(out_path), "expect_matched": expect_matched,
                "search_truncated": search_truncated}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)


KEY_RE = re.compile(rb"^[0-9a-f]{64}$")


def _hmac_key() -> bytes:
    """Create-once key. The key is written completely to a private temp file and then
    published with os.link (fails if the key exists), so no reader ever sees a partial key."""
    path = home_dir() / "key"
    for _ in range(50):
        try:
            data = path.read_bytes().strip()
        except FileNotFoundError:
            data = None
        if data is not None:
            if KEY_RE.match(data):
                return data
            raise DoneContractError(f"HMAC key file is invalid: {path} (remove it to regenerate; older evidence signatures will stop verifying)")
        key = secrets.token_hex(32).encode("ascii")
        fd, tmp = tempfile.mkstemp(prefix=".key.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(key + b"\n")
            os.chmod(tmp, 0o600)
            try:
                os.link(tmp, path)
                return key
            except FileExistsError:
                continue
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    raise DoneContractError("could not create the HMAC key")


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
    """Shared validity rule: same task, repo, contract, approval, marks, tree, finished run, intact HMAC."""
    if not ev:
        return False
    try:
        approval = find_approval(repo, contract)
        return (ev.get("task") == task and ev.get("repo") == str(repo)
                and not ev.get("in_progress")
                and ev.get("contract_sha256") == contract_sha(contract)
                and approval is not None
                and ev.get("approval_id") == approval.get("approval_id")
                and ev.get("marks_sha256") == marks_digest(marks)
                and ev.get("tree") == tree
                and ev.get("tool_version") == VERSION
                and ev.get("verdict") not in (VERDICT_UNAPPROVED, VERDICT_ERROR, VERDICT_STALE)
                and ev.get("hmac") == sign_evidence(ev))
    except Exception:
        return False


def compute_verdict(item_results: list[dict], repo_results: list[dict], protected_changed: list[str],
                    allow_protected: bool, paused: dict | None, stale: bool) -> str:
    """Precedence: STALE > ERROR > PAUSED > FAIL > TESTS_CHANGED > INCOMPLETE > PASS."""
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


def run_check(repo: Path, task: str, *, reuse: bool = True, session: str | None = None,
              budget_s: float | None = None) -> dict:
    """The budget covers the whole run, including waiting for the worktree lock."""
    deadline = None if budget_s is None else time.monotonic() + float(budget_s)
    with repo_lock(repo, timeout=budget_s):
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        return _run_check_locked(repo, task, reuse=reuse, session=session, budget_s=remaining)


def _blank_result(cmd: str) -> dict:
    return {"command": cmd, "exit": None, "timed_out": False, "timeout_s": None, "duration_s": 0, "output_tail": "",
            "output_bytes": 0, "output_sha256": None, "expect_matched": None, "search_truncated": False}


def _run_check_locked(repo: Path, task: str, *, reuse: bool, session: str | None, budget_s: float | None,
                      verify_cache: bool = False) -> dict:
    started = time.monotonic()
    contract = load_contract(repo, task)
    csha = contract_sha(contract)
    approval = find_approval(repo, contract)
    marks = load_marks(repo, task)
    msha = marks_digest(marks)
    tree_before = working_tree_hash(repo, verify_cache=verify_cache)
    base = {
        "tool": "done-contract", "tool_version": VERSION, "task": task, "repo": str(repo),
        "contract_sha256": csha, "tree": tree_before, "baseline_tree": contract.get("baseline_tree"),
        "marks_sha256": msha, "session": session,
        "approval_id": (approval or {}).get("approval_id"),
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
    # a previous run is reusable per item when it belongs to this contract, approval and marks
    # and finished normally; which items may skip depends on their watch/cache policy
    prev_compatible = bool(prev) and evidence_is_current(repo, task, prev, contract, marks, prev.get("tree") or "")
    changed_since_prev = changed_paths(repo, prev["tree"], tree_before) if prev_compatible else None
    prev_items = {it["id"]: it for it in (prev or {}).get("items", [])} if prev_compatible else {}
    prev_repo = {rc["command"]: rc for rc in (prev or {}).get("repo_checks", [])} if prev_compatible else {}

    # Invalidate older evidence first: if this run dies, nobody can close on a stale PASS.
    stub = {**base, "verdict": VERDICT_ERROR, "in_progress": True, "checked_at": now_iso(), "items": [], "repo_checks": [],
            "changed_paths": [], "protected_changed": [], "reused": False, "approval": None, "paused": marks.get("paused"),
            "error": "check interrupted before it finished"}
    stub["hmac"] = sign_evidence(stub)
    try:
        write_evidence(repo, task, stub)
    except Exception:
        with contextlib.suppress(OSError):
            evidence_path(repo, task).unlink()
        raise

    def remaining() -> float | None:
        if budget_s is None:
            return None
        return budget_s - (time.monotonic() - started)

    def effective_timeout(wanted: float) -> tuple[float, bool]:
        rem = remaining()
        if rem is None:
            return wanted, False
        return (min(wanted, rem), rem < wanted)

    item_results = []
    for it in contract["items"]:
        mark = marks["items"].get(it["id"])
        cached = prev_items.get(it["id"])
        base_row = {"id": it["id"], "text": it["text"], "strength": strength_of(it["check"]),
                    "expect": it.get("expect"), "blocked_reason": None, "reuse_policy": reuse_policy(it), "reuse_reason": None}
        ok_prev = bool(cached) and cached.get("status") in ("PASS", "FAIL", "BLOCKED") and cached.get("command") == it["check"] \
            and cached.get("expect") == it.get("expect")
        reusable, why = can_reuse(it.get("watch"), reuse_policy(it), changed_since_prev) if ok_prev else (False, None)
        if reusable:
            res = {k: cached.get(k) for k in _blank_result(it["check"])}
            passed = cached["status"] == "PASS"
            reused = True
            error = None
            base_row["reuse_reason"] = why
        else:
            rem = remaining()
            if rem is not None and rem <= 0:
                item_results.append({**base_row, **_blank_result(it["check"]), "status": "ERROR", "reused": False,
                                     "error": "not run: time budget exhausted; run `done-contract check` manually"})
                continue
            wanted = float(it.get("timeout", DEFAULT_ITEM_TIMEOUT))
            timeout, cut = effective_timeout(wanted)
            try:
                res = run_command(it["check"], repo, timeout, expect=it.get("expect"))
                error = None
            except Exception as exc:
                res = _blank_result(it["check"])
                error = f"could not run: {type(exc).__name__}: {str(exc)[:200]}"
            if error is None and res["timed_out"] and cut:
                error = f"stopped at the time budget ({timeout:.0f}s of {wanted:.0f}s); run `done-contract check` manually"
            if error is None and res.get("search_truncated") and not res.get("expect_matched"):
                error = f"output larger than {MAX_OUTPUT_SEARCH_BYTES // (1024 * 1024)} MiB; expect text not found in the searched part"
            passed = error is None and res["exit"] == 0 and not res["timed_out"] and (res["expect_matched"] is not False)
            reused = False
        if error:
            status = "ERROR"
        elif passed:
            status = "PASS"
        elif mark and mark.get("status") == "blocked":
            status = "BLOCKED"
        else:
            status = "FAIL"
        item_results.append({**base_row, **res, "status": status, "reused": reused, "error": error,
                             "blocked_reason": (mark or {}).get("reason") if status == "BLOCKED" else None})
    repo_results = []
    repo_watch = contract.get("repo_watch")
    for cmd in contract.get("repo_checks", []):
        cached = prev_repo.get(cmd)
        if cached and cached.get("status") in ("PASS", "FAIL") and repo_watch:
            reusable, why = can_reuse(repo_watch, "watch", changed_since_prev)
            if reusable:
                repo_results.append({**{k: cached.get(k) for k in _blank_result(cmd)}, "status": cached["status"], "reused": True,
                                     "error": None, "reuse_reason": why})
                continue
        rem = remaining()
        if rem is not None and rem <= 0:
            repo_results.append({**_blank_result(cmd), "status": "ERROR", "reused": False, "error": "not run: time budget exhausted"})
            continue
        timeout, cut = effective_timeout(float(REPO_CHECK_TIMEOUT))
        try:
            res = run_command(cmd, repo, timeout)
            error = None
        except Exception as exc:
            res = _blank_result(cmd)
            error = f"could not run: {type(exc).__name__}: {str(exc)[:200]}"
        if error is None and res["timed_out"] and cut:
            error = f"stopped at the time budget ({timeout:.0f}s)"
        status = "ERROR" if error else ("PASS" if res["exit"] == 0 and not res["timed_out"] else "FAIL")
        repo_results.append({**res, "status": status, "reused": False, "error": error})
    tree_after = working_tree_hash(repo, verify_cache=verify_cache)
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
        "budget_s": budget_s,
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
        "reused": bool(item_results) and all(r.get("reused") for r in item_results + repo_results),
        "changed_since_previous_run": changed_since_prev,
    }
    evidence["hmac"] = sign_evidence(evidence)
    try:
        write_evidence(repo, task, evidence)
    except Exception:
        with contextlib.suppress(OSError):
            evidence_path(repo, task).unlink()
        raise
    log_event({"event": "check", "repo": str(repo), "task": task, "verdict": verdict, "tree": tree_before, "session": session})
    return evidence


def write_evidence(repo: Path, task: str, evidence: dict) -> None:
    write_json(evidence_path(repo, task), evidence)
    (task_dir(repo, task) / "evidence.md").write_text(render_evidence_md(evidence), encoding="utf-8")


def mark_released(repo: Path, task: str, reason: str, session: str | None) -> dict | None:
    """Record that the Stop hook let the agent stop without a passing verdict."""
    with repo_lock(repo):
        ev = load_evidence(repo, task)
        if not ev:
            return None
        ev["released"] = {"reason": reason, "at": now_iso(), "session": session, "verdict_at_release": ev.get("verdict")}
        ev["hmac"] = sign_evidence(ev)
        write_evidence(repo, task, ev)
        return ev


def render_evidence_md(ev: dict) -> str:
    lines = [f"# done-contract evidence — {ev.get('task')}", "",
             f"- verdict: **{ev.get('verdict')}**" + ("  (in progress / interrupted)" if ev.get("in_progress") else "")
             + ("  (all results reused)" if ev.get("reused") else ""),
             f"- checked_at: {ev.get('checked_at')}  duration: {ev.get('duration_s')}s",
             f"- tree: `{ev.get('tree')}`  baseline: `{ev.get('baseline_tree')}`",
             f"- contract sha256: `{ev.get('contract_sha256')}`"]
    ap = ev.get("approval")
    if ap:
        lines.append(f"- approval: {ap.get('approved_at')} by {ap.get('approver')}"
                     + (f" (pre-approval changes: {len(ap.get('pre_approval_changes') or [])})" if ap.get("pre_approval_changes") else ""))
    else:
        lines.append("- approval: **none**")
    if ev.get("error"):
        lines.append(f"- error: {ev['error']}")
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
            exit_s = "timeout" if it.get("timed_out") else ("not run" if it["status"] == "ERROR" and it.get("exit") is None else str(it.get("exit")))
            flag = f" (reused: {it.get('reuse_reason') or 'cached'})" if it.get("reused") else ""
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
            lines.append(f"- {rc['status']} `{rc['command']}` exit {rc.get('exit')} {rc.get('duration_s')}s" + (f" error: {rc['error']}" if rc.get("error") else ""))
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
    """Close only on a fresh run made under the lock (opt-in cached items may be reused)."""
    with repo_lock(repo):
        marks = load_marks(repo, task)
        if marks.get("closed_at"):
            raise DoneContractError("already closed")
        if contract_state(repo, task) != "approved":
            raise DoneContractError(f"contract is {contract_state(repo, task)}; only an approved contract can be closed")
        ev = _run_check_locked(repo, task, reuse=False, session=None, budget_s=None, verify_cache=True)  # close: no reuse, cross-checked snapshot
        if ev.get("verdict") not in ALLOW_STOP_VERDICTS:
            raise DoneContractError(f"close refused: fresh check verdict is {ev.get('verdict')} "
                                    "(needs PASS, INCOMPLETE or PAUSED); see evidence.md")
        marks = load_marks(repo, task)
        marks["closed_at"] = now_iso()
        marks["closed_verdict"] = ev["verdict"]
        save_marks(repo, task, marks)
        if active_task(repo) == task:
            set_active(repo, None)
    log_event({"event": "close", "repo": str(repo), "task": task, "verdict": ev.get("verdict"), "tree": ev.get("tree")})
    return ev


def verify_evidence(repo: Path, task: str) -> dict:
    """Re-run every command recorded in the evidence (same timeouts, no cache) and compare.

    `agree` is about reproduction. `current` says whether the evidence still describes
    this contract, approval, marks and tree; both must hold to trust it as a receipt."""
    ev = load_evidence(repo, task)
    if not ev or ev.get("verdict") in (VERDICT_UNAPPROVED, None) or ev.get("in_progress"):
        raise DoneContractError("no finished evidence to verify; run `done-contract check` first")
    contract = load_contract(repo, task)
    marks = load_marks(repo, task)
    tree_before = working_tree_hash(repo, verify_cache=True)
    # the evidence must describe THIS contract's checks: a forged evidence that swapped an item's
    # command for a passing one would otherwise re-run its own forged command and "reproduce".
    contract_items = {it["id"]: (it["check"], it.get("expect")) for it in contract.get("items", []) if isinstance(it, dict)}
    contract_repo = _counter(contract.get("repo_checks", []))
    evidence_repo = _counter(rc.get("command") for rc in ev.get("repo_checks", []))
    matches_contract = contract_repo == evidence_repo  # multiset: a swapped-in duplicate is caught
    rows = []
    agree = True
    for it in ev.get("items", []):
        if contract_items.get(it["id"]) != (it.get("command"), it.get("expect")):
            matches_contract = False  # evidence command/expect differs from the approved contract
        if it["status"] == "ERROR":
            rows.append({"id": it["id"], "recorded": "ERROR", "now": "skipped", "agree": False, "exit": None})
            agree = False
            continue
        res = run_command(it["command"], repo, it.get("timeout_s") or DEFAULT_ITEM_TIMEOUT, expect=it.get("expect"))
        passed = res["exit"] == 0 and not res["timed_out"] and (res["expect_matched"] is not False)
        now = "PASS" if passed else "FAIL"
        same = (it["status"] == "PASS") == passed
        agree = agree and same
        rows.append({"id": it["id"], "recorded": it["status"], "now": now, "agree": same, "exit": res["exit"]})
    for rc in ev.get("repo_checks", []):
        res = run_command(rc["command"], repo, rc.get("timeout_s") or REPO_CHECK_TIMEOUT)
        now = "PASS" if res["exit"] == 0 and not res["timed_out"] else "FAIL"
        same = rc["status"] == now
        agree = agree and same
        rows.append({"id": f"repo:{rc['command']}", "recorded": rc["status"], "now": now, "agree": same, "exit": res["exit"]})
    if {it["id"] for it in ev.get("items", [])} != set(contract_items):
        matches_contract = False  # an item was added or dropped
    # currency is judged after the commands ran: the tree, contract, marks and approval must be the same now
    tree_after = working_tree_hash(repo, verify_cache=True)
    stale_paths = changed_paths(repo, tree_before, tree_after) if tree_after != tree_before else []
    hmac_valid = ev.get("hmac") == sign_evidence(ev)
    current = (tree_after == tree_before and matches_contract
               and evidence_is_current(repo, task, ev, load_contract(repo, task), load_marks(repo, task), tree_after))
    return {"task": task, "evidence_tree": ev.get("tree"), "current_tree": tree_after, "same_tree": ev.get("tree") == tree_after,
            "stale_paths": stale_paths, "hmac_valid": hmac_valid, "current": current, "matches_contract": matches_contract,
            "rows": rows, "agree": agree, "ok": agree and hmac_valid and current}


def shell_quote(path: str) -> str:
    return shlex.quote(path)
