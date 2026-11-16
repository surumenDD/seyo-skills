#!/usr/bin/env python3
"""リポジトリの検査を 1 つのコマンドで実行する（標準ライブラリだけで動く）。

使い方: python -X utf8 -B tools/check.py [--skip-selftest]

項目:
  1. frontmatter   skills/*/SKILL.md の name と description
  2. 参照          SKILL.md と README.md のバッククォート内の references/…、scripts/… が存在するか
  3. 自己テスト    各スキルの自己テスト（--skip-selftest で飛ばす）
  4. 入れないもの  __pycache__、.venv、node_modules、*.pyc が git の対象になる場所に無いか
  5. 環境のパス    C:\\Users\\<名前>\\、/Users/<名前>/、/home/<名前>/ の形の絶対パスが無いか
  6. 非公開の語    SEYO_FORBIDDEN か ~/.seyo/forbidden.txt の語が含まれていないか

問題があれば内容を表示し、終了コード 1 で終わる。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
JUNK_DIRS = {"__pycache__", ".venv", "node_modules"}
JUNK_PATTERNS = ["__pycache__/", ".venv/", "node_modules/", "*.pyc"]
WALK_SKIP = {".git"} | JUNK_DIRS

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
REF_RE = re.compile(r"(?<![\w./~\\-])((?:references|scripts)/[A-Za-z0-9_.\-/]+)")
# 名前の部分は英数字で始まるものだけを拾う。<名前>、%USERNAME%、$env:USERNAME、{user}、~ は対象外
_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
USER_PATH_RES = [
    re.compile(r"\b[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)(" + _NAME + r")(?:\\{1,2}|/)", re.I),
    re.compile(r"(?<![\w.~:-])/Users/(" + _NAME + r")/"),
    re.compile(r"(?<![\w.~-])/home/(" + _NAME + r")/"),
]
# OS が用意する共有の名前と、文書の例で使う仮の名前は、利用者の名前ではないので拾わない
SHARED_NAMES = {"public", "shared", "default", "all users",
                "user", "username", "you", "yourname", "name", "me", "example", "runner"}


class Result:
    def __init__(self, label: str):
        self.label = label
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.skipped = False

    def bad(self, msg: str) -> None:
        self.problems.append(msg)

    def summary(self) -> str:
        if self.problems:
            state = f"NG ({len(self.problems)} 件)"
        elif self.skipped:
            state = "飛ばした"
        else:
            state = "OK"
        extra = f"  [{'; '.join(self.notes)}]" if self.notes else ""
        return f"{self.label}: {state}{extra}"


def rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def skill_dirs() -> list[str]:
    if not os.path.isdir(SKILLS):
        return []
    return sorted(
        os.path.join(SKILLS, d) for d in os.listdir(SKILLS)
        if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md"))
    )


# ---------------------------------------------------------------- 1. frontmatter

def parse_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    data: dict[str, str] = {}
    key = None
    block: list[str] = []
    folded = False

    def flush():
        if key is not None and folded:
            data[key] = " ".join(s.strip() for s in block if s.strip())

    for line in lines[1:end]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            flush()
            key, val = m.group(1), m.group(2).strip()
            block = []
            folded = val in ("|", ">", "|-", ">-", "|+", ">+")
            if not folded:
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                data[key] = val
        elif key is not None:
            if folded:
                block.append(line)
            elif line.strip():
                data[key] = (data[key] + " " + line.strip()).strip()
    flush()
    return data


def check_frontmatter(res: Result) -> None:
    dirs = skill_dirs()
    if not dirs:
        res.bad("skills/ の下に SKILL.md を持つフォルダが無い")
    for d in dirs:
        name = os.path.basename(d)
        path = os.path.join(d, "SKILL.md")
        with open(path, encoding="utf-8") as f:
            fm = parse_frontmatter(f.read())
        if fm is None:
            res.bad(f"{rel(path)}: frontmatter が無い")
            continue
        n = fm.get("name", "")
        if n != name:
            res.bad(f"{rel(path)}: name が '{n}' で、フォルダの名前 '{name}' と違う")
        if not NAME_RE.match(n):
            res.bad(f"{rel(path)}: name '{n}' が英小文字・数字・ハイフンの 1〜64 字でない")
        desc = fm.get("description", "")
        if not (1 <= len(desc) <= 1024):
            res.bad(f"{rel(path)}: description が {len(desc)} 字（1〜1,024 字であること）")
    res.notes.append(f"{len(dirs)} スキル")


# ---------------------------------------------------------------- 2. 参照

def check_references(res: Result) -> None:
    count = 0
    for d in skill_dirs():
        for doc in ("SKILL.md", "README.md"):
            path = os.path.join(d, doc)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    for span in BACKTICK_RE.findall(line):
                        for ref in REF_RE.findall(span):
                            ref = ref.rstrip(".")
                            if ref.endswith("/") and ref.count("/") == 1:
                                continue  # references/ だけ（<名前> などの書き方の前半）
                            if any(p in JUNK_DIRS for p in ref.split("/")):
                                continue  # .venv や node_modules は手元で作るもので、リポジトリに入らない
                            count += 1
                            if not os.path.exists(os.path.join(d, *ref.rstrip("/").split("/"))):
                                res.bad(f"{rel(path)}:{lineno}: `{ref}` が {rel(d)}/ に無い")
    res.notes.append(f"{count} 箇所")


# ---------------------------------------------------------------- 3. 自己テスト

def run(cmd: list[str], cwd: str | None = None, stdin: str | None = None,
        timeout: int = 600) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    try:
        p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"{timeout} 秒で終わらなかった"
    except OSError as e:
        return 127, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def tail(text: str, n: int = 15) -> str:
    return "\n".join("      " + s for s in text.strip().splitlines()[-n:])


def check_selftests(res: Result) -> None:
    py = [sys.executable, "-X", "utf8", "-B"]
    ok: list[str] = []

    def expect(label: str, cmd: list[str], **kw) -> None:
        code, out = run(cmd, **kw)
        if code == 0:
            ok.append(label)
        else:
            res.bad(f"{label}: 終了コード {code}\n{tail(out)}")

    s = os.path.join(SKILLS, "chosei-seyo", "scripts", "selftest.py")
    expect("chosei-seyo selftest", py + [s])

    s = os.path.join(SKILLS, "delegate-seyo", "scripts", "scope_check.py")
    expect("delegate-seyo scope_check", py + [s, "--selftest"])

    with tempfile.TemporaryDirectory() as tmp:
        payload = json.dumps({"session_id": "t", "hook_event_name": "PostToolUse", "cwd": tmp})
        for name in ("ctx_notice.py", "post_compact.py"):
            s = os.path.join(SKILLS, "handoff-seyo", "scripts", name)
            expect(f"handoff-seyo {name}", py + ["-I", s], stdin=payload, cwd=tmp, timeout=60)

    sdir = os.path.join(SKILLS, "slide-seyo", "scripts")
    venv_py = None
    for cand in (os.path.join(sdir, ".venv", "Scripts", "python.exe"),
                 os.path.join(sdir, ".venv", "bin", "python")):
        if os.path.isfile(cand):
            venv_py = cand
            break
    pdf_py = venv_py or sys.executable
    code, _ = run([pdf_py, "-c", "import numpy, pypdfium2"], timeout=120)
    if code != 0:
        res.notes.append("slide-seyo check_pdf を飛ばした（numpy か pypdfium2 が無い）")
    else:
        expect("slide-seyo check_pdf", [pdf_py, "-X", "utf8", "-B",
                                        os.path.join(sdir, "check_pdf.py"), "--selftest"])

    node = shutil.which("node")
    if not node:
        res.notes.append("slide-seyo check_html を飛ばした（node が無い）")
    elif not os.path.isdir(os.path.join(sdir, "node_modules")):
        res.notes.append("slide-seyo check_html を飛ばした（scripts/node_modules が無い）")
    else:
        expect("slide-seyo check_html", [node, "check_html.mjs", "--selftest"], cwd=sdir)

    res.notes.insert(0, f"通過 {len(ok)}")


# ---------------------------------------------------------------- 4. 入れないもの

def git_files() -> list[str] | None:
    """git の対象になるファイル（追跡済みと、無視されていない未追跡）。git でなければ None。"""
    if not shutil.which("git") or not os.path.exists(os.path.join(ROOT, ".git")):
        return None
    code, out = run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others",
                     "--exclude-standard"], timeout=120)
    if code != 0:
        return None
    return [p for p in out.split("\0") if p.strip()]


def gitignore_patterns(dirpath: str) -> list[str]:
    p = os.path.join(dirpath, ".gitignore")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [s.strip() for s in f if s.strip() and not s.startswith("#")]


def ignored_by_ancestors(path: str, is_dir: bool) -> bool:
    """git が無いときの代わりの判定。名前だけで合わせる単純な形（JUNK の 4 種で足りる）。"""
    name = os.path.basename(path)
    d = os.path.dirname(path)
    while True:
        for pat in gitignore_patterns(d):
            body = pat.rstrip("/").lstrip("/")
            if pat.endswith("/") and not is_dir:
                continue
            if "/" not in body and fnmatch.fnmatch(name, body):
                return True
        if os.path.normcase(d) == os.path.normcase(ROOT) or os.path.dirname(d) == d:
            return False
        d = os.path.dirname(d)


def is_junk_path(relpath: str) -> bool:
    parts = relpath.replace("\\", "/").split("/")
    return any(p in JUNK_DIRS for p in parts[:-1]) or parts[-1].endswith(".pyc")


def check_junk(res: Result) -> None:
    files = git_files()
    if files is not None:
        res.notes.append("git ls-files で判定")
        for f in files:
            if is_junk_path(f):
                res.bad(f"{f}: git の対象になっている")
    else:
        res.notes.append("git でないので .gitignore の名前で判定")
        for cur, dirs, fnames in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d != ".git"]
            for d in list(dirs):
                if d in JUNK_DIRS:
                    full = os.path.join(cur, d)
                    if not ignored_by_ancestors(full, True):
                        res.bad(f"{rel(full)}/: .gitignore で外れていない")
                    dirs.remove(d)
            for fn in fnames:
                if fn.endswith(".pyc"):
                    full = os.path.join(cur, fn)
                    if not ignored_by_ancestors(full, False):
                        res.bad(f"{rel(full)}: .gitignore で外れていない")
    root_pats = set(gitignore_patterns(ROOT))
    for pat in JUNK_PATTERNS:
        if pat not in root_pats:
            res.bad(f".gitignore に {pat} が無い")


# ---------------------------------------------------------------- 5, 6. テキストの走査

def text_files() -> list[tuple[str, str]]:
    files = git_files()
    if files is None:
        files = []
        for cur, dirs, fnames in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in WALK_SKIP]
            files += [rel(os.path.join(cur, fn)) for fn in fnames]
    out = []
    for f in sorted(files):
        if is_junk_path(f):
            continue
        full = os.path.join(ROOT, *f.split("/"))
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\0" in data:
            continue
        try:
            out.append((f, data.decode("utf-8-sig")))
        except UnicodeDecodeError:
            continue
    return out


def check_user_paths(res: Result, texts: list[tuple[str, str]]) -> None:
    for f, text in texts:
        for lineno, line in enumerate(text.splitlines(), 1):
            for rx in USER_PATH_RES:
                for m in rx.finditer(line):
                    if m.group(1).lower() in SHARED_NAMES:
                        continue
                    res.bad(f"{f}:{lineno}: 利用者の環境のパス '{m.group(0)}'")
    res.notes.append(f"{len(texts)} ファイル")


def forbidden_list() -> tuple[str | None, list[str]]:
    p = os.environ.get("SEYO_FORBIDDEN") or os.path.join(os.path.expanduser("~"), ".seyo", "forbidden.txt")
    if not os.path.isfile(p):
        return None, []
    with open(p, encoding="utf-8-sig") as f:
        return p, [s.strip() for s in f if s.strip()]


def check_forbidden(res: Result, texts: list[tuple[str, str]]) -> None:
    path, words = forbidden_list()
    if path is None:
        res.skipped = True
        res.notes.append("語の一覧が無い（SEYO_FORBIDDEN も ~/.seyo/forbidden.txt も無い）")
        return
    lowered = [(i, w.lower()) for i, w in enumerate(words, 1)]
    for f, text in texts:
        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            for i, w in lowered:
                if w in low:
                    # 語そのものは表示しない（CI の記録などに残さないため）。一覧の行番号で示す
                    res.bad(f"{f}:{lineno}: 一覧の {i} 行目の語を含む")
    res.notes.append(f"{len(words)} 語")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="seyo-skills リポジトリの検査")
    ap.add_argument("--skip-selftest", action="store_true", help="自己テスト（項目 3）を飛ばす")
    args = ap.parse_args()

    results = []
    r = Result("1 frontmatter"); check_frontmatter(r); results.append(r)
    r = Result("2 参照"); check_references(r); results.append(r)
    r = Result("3 自己テスト")
    if args.skip_selftest:
        r.skipped = True
        r.notes.append("--skip-selftest")
    else:
        check_selftests(r)
    results.append(r)
    r = Result("4 入れないもの"); check_junk(r); results.append(r)
    texts = text_files()
    r = Result("5 環境のパス"); check_user_paths(r, texts); results.append(r)
    r = Result("6 非公開の語"); check_forbidden(r, texts); results.append(r)

    failed = False
    for r in results:
        if r.problems:
            failed = True
            print(f"--- {r.label}")
            for p in r.problems:
                print("  " + p)
    print("=== 結果")
    for r in results:
        print(r.summary())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
