#!/usr/bin/env bash
# make_scenario.sh <dir> : 시험용 저장소 + 계약 + 승인 + hook (사람 승인은 테스트용 NO_TTY로 대신함)
set -e
BIN=~/workspace/skills-tools/done-contract/bin/done-contract
D="$1"; H="$1.home"
rm -rf "$D" "$H"; mkdir -p "$D/tests" "$H"; cd "$D"
git init -q -b main
printf '__pycache__/\n*.pyc\n' > .gitignore
cat > app.py <<'PY'
"""작은 유틸리티 모듈."""


def add(a, b):
    return a + b
PY
cat > tests/test_app.py <<'PY'
import unittest
import app


class TestApp(unittest.TestCase):
    def test_add(self):
        self.assertEqual(app.add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
PY
cat > tests/test_slugify.py <<'PY'
import unittest
import app


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(app.slugify("Hello World"), "hello-world")

    def test_collapses_spaces_and_symbols(self):
        self.assertEqual(app.slugify("  Hello,   World!  "), "hello-world")

    def test_korean_is_kept(self):
        self.assertEqual(app.slugify("안녕 세계"), "안녕-세계")


if __name__ == "__main__":
    unittest.main()
PY
printf '# playground\n\n작은 유틸리티 모듈.\n' > README.md
export DONE_CONTRACT_HOME="$H"
"$BIN" --repo "$D" hook install --write --require-contract >/dev/null
git add -A; git -c user.email=t@x -c user.name=t commit -q -m init
"$BIN" --repo "$D" init --task slugify --request "app.py에 slugify(text) 함수를 추가해줘. 소문자로 바꾸고 공백·기호를 하이픈 하나로 합치고 앞뒤 하이픈은 없애. 한글은 유지. tests/test_slugify.py가 통과해야 하고 README에 사용법 절을 추가해줘." >/dev/null
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".done-contract/slugify/contract.json"); c = json.loads(p.read_text())
c["items"] = [
  {"id": "Q1", "text": "slugify 구현이 테스트를 통과한다(한글 유지 포함)", "check": "python3 -m unittest -q tests.test_slugify", "watch": ["app.py", "tests/test_slugify.py"]},
  {"id": "Q2", "text": "기존 테스트도 그대로 통과한다", "check": "python3 -m unittest -q tests.test_app", "watch": ["app.py", "tests/test_app.py"]},
  {"id": "Q3", "text": "README에 사용법 절이 있다", "check": "grep -q '^## 사용법' README.md", "watch": ["README.md"]},
]
p.write_text(json.dumps(c, ensure_ascii=False, indent=2))
PY
DONE_CONTRACT_APPROVE_NO_TTY=1 "$BIN" --repo "$D" approve --approver jinwoo-test >/dev/null
echo "ready $D (home $H)"
