# 설치 후보 도구 3종 소스 감사 — isitdone / protect-tests / config-guard (2026-10-05)

워커(Claude sonnet, 읽기 전용 탐색) 보고 원문을 코디네이터가 저장한 것이다. 감사 대상 코드는 실행하지 않았고(INSPECTED), 해시·tarball·attestation 대조·git 이력·regex 재현만 VERIFIED다. 증거물은 세션 scratchpad `tool_audit/` 아래에 있었고 세션 종료 후 사라질 수 있다.

## 코디네이터 요약과 설치 판단

| 도구 | 네트워크 | 저장소 밖 읽기 | 쓰기 | 실행 | 판단 |
|---|---|---|---|---|---|
| isitdone 0.8.2 (MIT, star 1, 1인, 20일 16릴리스, SLSA provenance 있음) | 코드에 없음. `npx -y` hook 명령행·`update`·`doctor`/`init`만 npm registry | hook 경로는 없음. `history`만 `~/.claude/projects`·`~/.codex` 등 읽어 stdout | repo마다 `.isitdone/`(key는 repo 안), `init`은 settings JSON 백업 없이 재작성 | **repo 정의 명령(npm test 등)을 Stop마다 전체 환경으로 실행**, 플러그인 설치는 전역 | 파일럿 프로젝트에 **project 범위·버전 고정**으로만 설치. done-contract의 `repo_checks`는 비워 중복 실행 회피. `init --no-latest`, `--user` 금지 |
| protect-tests 1.0.0 (MIT, repo star 530, 2026-08-18 이후 불변) | 없음(fs·path만) | stdin만 | `~/.claude/hooks-logs/*.jsonl` 메타데이터 | 없음 | 설치 권장. 복합 명령 오탐(`rm -rf dist && pytest tests/` → deny) 감수. 감사 시점 파일을 복사해 classic hook으로 고정 |
| config-guard 1.0.0 (MIT) | 없음 | stdin + symlink 해석 | `~/.claude/hooks-logs/*.jsonl`(차단 명령 앞 100자 포함) | 없음 | **기본 설치 보류**. `~/.claude/settings.json`·모든 `hooks.json`·`.mcp.json` 편집과 `claude mcp add`/`claude plugin …`을 deny해 이 워크스페이스의 hook·MCP·스킬 작업 흐름을 막는다. 쓰려면 `HOOK_ASK_HIGH=true`·`HOOK_ASK_CRITICAL=true`와 함께. 제거는 사람만 가능 |

고정 해시(워커 VERIFIED): `protect-tests.js` `7e9300e8dd651749a0bed68f57bf669a84ebe3f0225e31577dfd4f19fd5f869b`, `config-guard.js` `df7b27d414918fdeb32a6a54c5ee701585e51ddcf1fd59ea1f8abecd0195c685`, `@aivolution/isitdone@0.8.2` tarball sha256 `e9ba7bd42ecf687be54b6ac34561cfdb29db5453089e01af841e360fd6d77a1d`.

---

## 워커 보고 원문

### 0. 방법과 증거 강도

- 한 것: `git clone` 2개 + 전체 이력 fetch, `npm pack @aivolution/isitdone@0.8.2` / `npm pack isitdone@0.8.2`(`--ignore-scripts`, 다운로드만), `npm view`·`curl`로 레지스트리·GitHub 공개 메타데이터 읽기, grep/sed로 소스 정독, `shasum`, python으로 attestation 디코드, `git log/diff`.
- 안 한 것: 감사 대상 코드 실행, `npm install`, `npx`, repo 스크립트 실행, 전역 설치, scratch 밖 수정. `node`는 손으로 옮겨 적은 regex 리터럴로 만든 `regex_semantics_check.js`(감사 대상 파일을 require하지 않음)에만 썼다.
- 강도: 표기 없는 코드 동작 주장은 INSPECTED. VERIFIED는 실행해 결과를 본 것(해시 대조, tarball 목록, attestation 디코드, git 이력, regex 재현). `(추정)`은 코드 밖(Claude Code·npx 동작)에 대한 추론. 런타임 동작은 어느 것도 VERIFIED가 아니다.
- 환경 사건: 사용자의 PreToolUse Bash 비밀 가드가 검색 패턴 문자열이 든 grep 3건을 차단했다(오탐). 동치 패턴으로 재실행했다.

### 1. isitdone

| 항목 | 값 |
|---|---|
| name | isitdone. npm `@aivolution/isitdone`(본체), `isitdone`(별칭, 코드 없음, 의존성 `"@aivolution/isitdone": "*"`) |
| repo | https://github.com/raimondasl/isitdone |
| version / commit | 0.8.2 / `0051655756ff5b15f21ede78d1727c4a3184dfbb` (태그 `v0.8.2`, `v0`) |
| license | MIT |
| last commit | 2026-09-27. 첫 커밋 2026-09-07, 총 61개, 작성자 1명 |
| 채택 신호 | GitHub star 1 / fork 1 / open issue 0. npm 릴리스 16개가 20일에 나옴, maintainer 1명 |
| 배포물 무결성 | tarball sha512 = registry integrity = attestation subject digest (VERIFIED). SLSA provenance: GitHub-hosted runner, `release.yml` @ `refs/tags/v0.8.2`, commit = 감사한 HEAD (Sigstore 서명 자체는 미검증) |
| 설치 시 실행물 | preinstall/install/postinstall/prepare 없음, `dependencies` 없음, tarball 6파일 |

**Stop hook 실행 사슬**: `hooks/hooks.json` Stop → `npx -y @aivolution/isitdone@0.8.2 hook --host claude`(timeout 600), PostToolUse `Edit|Write|MultiEdit` → `--event edit`(timeout 30). `cmdHook` → stdin(3초 제한) → `runHook` → `decideStop`: `ISITDONE=1`이면 allow, 입력이 JSON 객체가 아니면 allow, plan 모드 allow, config 오류 allow, 그 외 `verify`. 출력은 `{"decision":"block",…}` 또는 빈 출력/`{"systemMessage":…}`, 종료 코드 항상 0. `verify`: gitInfo → detectChecks → 영수증 캐시 → check마다 `spawn(check.cmd, {cwd, shell: true, env})`. 기본 profile `claim-gated`: lite(typecheck·lint, 60s)는 매 Stop, full(test·build, 120s)은 `last_assistant_message`에 완료 주장 문장이 있을 때만.

**(a) 네트워크**: 코드에 네트워크 능력 없음(비상대 import는 `node:path`, `fs`, `os`, `child_process`, `readline`, `crypto`, `url`, `zlib`, `perf_hooks`, `module`; `node:sqlite` 동적 로드). `fetch(`·http(s)·net·dns·WebSocket·telemetry·eval 0건, 배포 번들도 동일(spawn 10곳 src와 1:1, 난독화 징후 없음). 네트워크 경로는 하위 프로세스뿐: hook 명령행 `npx -y`(캐시 없으면 registry), `isitdone update`(`npm view`, `npx -y … --version`), `isitdone doctor`(`init`이 기본으로 이어 실행; `--no-doctor`, `--no-latest`로 끔), repo의 check 명령.

**(b) 읽는 것**: hook 경로는 stdin 필드와 환경변수(`CLAUDE_PROJECT_DIR` 등), repo의 설정·탐지 파일(`package.json`, `tsconfig.json`, `pyproject.toml`, `.github/workflows/*.yml`의 `run:` 줄, `Makefile` 등), git(`.git/index` tmp 복사, diff, ls-files, 추적되지 않은 2 MiB 이하 파일 스캔). `transcript_path`를 읽지 않는다. `history`(명시 호출만): `~/.claude/projects/*/*.jsonl`, `$CODEX_HOME|~/.codex`, `~/.gemini`, `~/.qwen`, `~/.cursor`와 Cursor `state.vscdb`(읽기 전용). 쓰기 가능한 fs 호출 0건, 출력은 stdout만. `init/doctor/uninstall`은 각 에이전트의 프로젝트·사용자 settings 파일을 읽는다.

**(c) 쓰는 것**: hook 경로는 `<root>/.isitdone/{.gitignore(*), key(0600), receipt.json(check 출력 마지막 30줄 포함), sessions/*, decisions.jsonl(512 KiB), run.lock}`, tmp `isitdone-index-*`. 첫 Stop 또는 첫 Edit에 `.isitdone/` 생성. `init`은 settings JSON 전체를 재직렬화(비원자적, 백업 없음), `.gitignore`에 `.isitdone/` 추가, 래퍼·플러그인 파일. `update`는 `_npx/<hash>` 삭제. HMAC 키는 repo 작업 트리 안.

**(d) repo 명령 실행**: `spawn(check.cmd, {cwd, shell: true, env})`, env는 전체 환경 + `ISITDONE=1`, `NO_COLOR=1`, `CI=true`. 자동 탐지: `${pm} test`, `npx tsc --noEmit`, lint, build, `pytest -q`, `ruff check .`, `flake8`, `mypy`, `pyright`, `go vet/test`, `cargo check/test`, `dotnet build/test`, `./gradlew test -q`, `./mvnw -q -B test`, `make test|lint|typecheck`; `.isitdone.json`의 임의 `cmd`. 신뢰 경계는 repo다. 플러그인 방식은 사용자 전역이라 열어 보는 모든 repo에서 Stop마다 실행된다.

**(e) 끄기/제거**: `npx isitdone uninstall`(프로젝트·사용자 범위 Stop+edit 항목 제거). 남는 것: `.isitdone/`, `.gitignore` 줄, `_npx` 캐시. 플러그인은 `/plugin uninstall isitdone@isitdone`(추정). 임시 끄기 `ISITDONE=1`. fail-open.

**risk notes**: (1) repo 정의 명령을 전체 환경으로 Stop마다 실행, 플러그인 설치는 전역 → 신뢰하는 repo에 project 범위로만. (2) 고정 없는 경로(`init` 기본 명령, `SKILL.md`의 `npx isitdone`) → 정확한 버전 고정. (3) `init` 부작용: 전체 check 1회 실행 + `npm view` + hook probe; `--user`는 `~/.claude/settings.json` 백업 없이 재작성. (4) `.isitdone/`이 모든 repo에 생김. (5) HMAC은 적대적 방어 아님. (6) 성능 미측정: Stop마다 비추적 파일 재해시. (7) 차단 시 실패 출력 약 20줄이 에이전트 컨텍스트로. (8) `history`는 프로젝트 경로와 완료 주장 문장을 출력.

### 2. protect-tests

repo https://github.com/karanb192/claude-code-hooks (마켓플레이스 22개 플러그인), 플러그인 1.0.0, repo HEAD `3c9c90a6…`, MIT, 플러그인 디렉터리 2026-08-18 이후 변경 없음. 원본은 외부 기여자 PR #20(2026-07-18). hook: PreToolUse `Bash|Edit|MultiEdit|Write`, `node "${CLAUDE_PLUGIN_ROOT}/protect-tests.js"`.

- 네트워크 없음(`fs`, `path`만). stdin만 읽음(`HOOK_SAFETY_LEVEL`, `HOME`). `~/.claude/hooks-logs/YYYY-MM-DD.jsonl`에 메타데이터 append(ERROR 기록은 payload 앞부분 인용 가능). 셸 실행 없음.
- 논리: Bash에 테스트 토큰 + `rm|unlink|shred|trash|git rm` → deny critical; `mv` + 비활성 접미사 → deny high. Edit/MultiEdit: 테스트 경로에 새 skip/xfail/ignore 마커 → deny high. Write: 마커 든 새 내용은 strict에서만. fail-open.
- 제거 `/plugin uninstall protect-tests@claude-code-hooks`. 끄기 스위치 없음, `HOOK_SAFETY_LEVEL`로 범위 조절.
- risk: 복합 명령 오탐(`rm -rf dist && pytest tests/` → DENY, `git rm old.js && … && ls tests/` → DENY). 우회 가능(설계상: 기본 수준의 Write 덮어쓰기, `sed -i`, 인터프리터). 마켓플레이스가 `main`을 따라감 → 감사 시점 파일을 복사해 classic hook으로 고정.

### 3. config-guard

같은 repo, 플러그인 1.0.0, MIT, 2026-08-18 도입(maintainer 본인). hook: PreToolUse `Bash|Edit|MultiEdit|Write`.

- 네트워크 없음. stdin + `realpathSync`(symlink 해석)만. `~/.claude/hooks-logs/*.jsonl`에 `target`(파일 경로 또는 Bash 명령 앞 100자) 포함 기록. 셸 실행 없음.
- 논리(기본 high): critical = `.claude/settings.json`·`settings.local.json`·`managed-settings.json`·`.claude/hooks/`·**이름이 `hooks.json`인 모든 파일**; high = `.mcp.json`·`.claude-plugin/`; strict(옵트인) = `CLAUDE.md`·`.claude/{rules,agents,commands}/`. Bash: `claude config set…`, `claude mcp add…`, `claude plugin …` deny; 보호 토큰 + 리다이렉트·`sed -i`·rm·mv·cp·tee 등 → deny. 읽기(cat, jq, grep, ls, diff)는 통과. `HOOK_ASK_<LEVEL>=true`면 ask. 탈출구 `CONFIG_GUARD_ALLOW=true`는 hook 프로세스 환경에서만 읽음. fail-open.
- 제거 `/plugin uninstall config-guard@claude-code-hooks`는 사람만(에이전트의 `claude plugin uninstall`은 자신이 차단).
- risk: (1) 운영 영향 큼 — `~/.claude/settings.json`, 모든 `hooks.json`(예 `~/.codex/hooks.json`), `.mcp.json`, `claude mcp add`/`claude plugin …`이 deny되어 update-config 스킬·Codex hook 신뢰 편집·MCP 설정이 걸림. (2) 복합 명령 오탐(`cat .mcp.json && npm install` → DENY). (3) 인터프리터 한 줄·`git checkout`·변수 경로는 못 막음. (4) 주석의 CVE-2026-25725·CHAINDROP 언급 미검증. (5) 마켓플레이스 `main` 추종.

### 4. 종합 5줄

1. 네트워크: protect-tests·config-guard는 `fs`/`path`만, isitdone도 src·배포 번들에 네트워크 모듈 없음. isitdone의 네트워크는 npm/npx 하위 프로세스뿐.
2. 읽기: 두 플러그인은 hook stdin만. isitdone hook 경로는 repo 파일·git만 읽고 transcript를 읽지 않으며, `history`만 각 CLI의 세션 디렉터리를 읽어 stdout으로 낸다.
3. 쓰기: 두 플러그인은 `~/.claude/hooks-logs/*.jsonl`; isitdone은 repo마다 `.isitdone/`, `init`은 settings JSON을 백업 없이 재작성.
4. 실행: isitdone은 repo 정의 명령을 전체 환경과 함께 Stop마다 셸로 실행하고 플러그인 설치는 전역. 나머지 둘은 아무것도 실행하지 않음.
5. 신뢰: 셋 다 MIT. isitdone은 4주·star 1·1인이지만 npm 0.8.2는 감사한 커밋에 묶인 SLSA provenance가 있고(서명 미검증), claude-code-hooks는 star 530이며 두 플러그인은 2026-08-18 이후 불변. 버전·파일 고정과 config-guard의 `HOOK_ASK_*` 설정을 권함.

### 5. 확인하지 못한 것

1. 런타임 동작 전부(실행 금지). 확인: 샘플 payload로 hook 실행 + `sandbox-exec`/`nettop`.
2. isitdone `dist`와 `src`의 바이트 일치(간접 근거만). 확인: `v0.8.2` 빌드 후 diff.
3. attestation의 Sigstore 서명 체인. 확인: `npm audit signatures`/`gh attestation verify`.
4. `npx -y`가 hook마다 registry를 조회하는지. 확인: `npm_config_offline=true`.
5. Claude Code의 마켓플레이스·플러그인 갱신·캐시 방식.
6. isitdone이 `~/workspace`에서 Stop마다 쓰는 시간.
7. 정독하지 않은 부분: isitdone `integrity.ts`(1786줄), `mcp.ts`, `codex.ts`, `gemini.ts`, `cursor.ts` SQLite, `turns.ts`, `sarif.ts`, `docs/`, `bench/`, `test/`; 나머지 20개 플러그인.
8. config-guard 주석의 CVE·웜 언급.
9. 계정 보안·maintainer 신원.
10. 사용자의 기존 hook과의 상호작용.
