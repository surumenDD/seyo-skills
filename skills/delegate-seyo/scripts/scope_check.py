#!/usr/bin/env python3
"""委譲した作業が、許可した範囲の外のファイルを変更していないかを一覧にする。

使い方:
  python scope_check.py --allow "docs/report/**" --allow "scripts/check_*.py"
  python scope_check.py --allow "src/**" --base main      # main から分かれたあとのコミットも見る
  python scope_check.py --selftest

見るもの:
  - 作業ツリーの変更（変更、追加、削除、名前の変更、未追跡のファイル）
  - --base を渡したときは、その参照から現在までにコミットされた変更も

--allow の書き方（パスの区切りは / で書く）:
  *   は、区切りを含まない任意の文字列
  **  は、区切りを含む任意の文字列
  ?   は、区切り以外の 1 文字

終了コード: 0 = 範囲の外の変更なし、1 = 範囲の外の変更あり、2 = 実行できなかった

標準ライブラリだけで動く。git で管理しているフォルダの中で実行する。
この検査が見るのは、どのファイルが変わったかだけである。変更の中身は差分で確認する。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

STATUS_WORDS = {
    "M": "変更",
    "A": "追加",
    "D": "削除",
    "R": "名前の変更",
    "C": "複製",
    "T": "種類の変更",
    "U": "競合",
    "?": "未追跡",
}


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """** と * と ? を使う書き方を、正規表現にする。"""
    pattern = pattern.replace("\\", "/").lstrip("./")
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def run_git(args: list[str], cwd: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=cwd,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout.decode("utf-8", "replace")


def working_tree_changes(cwd: str) -> list[tuple[str, str]]:
    """作業ツリーの変更を (状態の文字, パス) の並びで返す。"""
    raw = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd)
    entries = raw.split("\0")
    changes: list[tuple[str, str]] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        letter = "?" if code == "??" else (code[0] if code[0] != " " else code[1])
        changes.append((letter, path))
        if letter in ("R", "C") and i < len(entries):
            # 名前の変更と複製では、次の項目が元のパス
            changes.append((letter, entries[i]))
            i += 1
    return changes


def committed_changes(cwd: str, base: str) -> list[tuple[str, str]]:
    """base から現在までにコミットされた変更を返す。"""
    raw = run_git(["diff", "--name-status", "-z", f"{base}...HEAD"], cwd)
    parts = [p for p in raw.split("\0") if p != ""]
    changes: list[tuple[str, str]] = []
    i = 0
    while i < len(parts):
        letter = parts[i][0]
        if letter in ("R", "C") and i + 2 < len(parts) + 1:
            changes.append((letter, parts[i + 1]))
            changes.append((letter, parts[i + 2]))
            i += 3
        else:
            changes.append((letter, parts[i + 1]))
            i += 2
    return changes


def check(cwd: str, allow: list[str], base: str | None) -> tuple[list, list]:
    top = run_git(["rev-parse", "--show-toplevel"], cwd).strip()
    changes = working_tree_changes(top)
    if base:
        changes += committed_changes(top, base)
    seen: dict[str, str] = {}
    for letter, path in changes:
        seen.setdefault(path.replace("\\", "/"), letter)
    patterns = [glob_to_regex(p) for p in allow]
    inside, outside = [], []
    for path in sorted(seen):
        target = inside if any(p.match(path) for p in patterns) else outside
        target.append((seen[path], path))
    return inside, outside


def report(inside: list, outside: list, allow: list[str], base: str | None) -> None:
    total = len(inside) + len(outside)
    scope = "作業ツリー" + (f"と、{base} からのコミット" if base else "")
    print(f"変わったファイル: {total} 件（見た範囲: {scope}）")
    print(f"許した範囲: {', '.join(allow)}")
    print(f"範囲の中: {len(inside)} 件、範囲の外: {len(outside)} 件")
    if outside:
        print("\n範囲の外で変わったファイル:")
        for letter, path in outside:
            print(f"  {STATUS_WORDS.get(letter, letter)}  {path}")
        print("\n次の対応: 上のファイルの差分を読み、依頼した作業に必要な変更かを確認する。")
        print("不要な変更なら、委譲先に元に戻させるか、自分で元に戻す。")
    else:
        print("\n範囲の外の変更はない。範囲の中の変更の中身は、差分で確認する。")


def selftest() -> int:
    results = []

    def expect(name: str, ok: bool) -> None:
        results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}")

    rx = glob_to_regex
    expect("* は区切りをまたがない", bool(rx("src/*.py").match("src/a.py")) and not rx("src/*.py").match("src/x/a.py"))
    expect("** は区切りをまたぐ", bool(rx("docs/**").match("docs/a/b.md")))
    expect("**/ は 0 階層にも合う", bool(rx("**/check_*.py").match("check_a.py")) and bool(rx("**/check_*.py").match("x/y/check_a.py")))
    expect("? は 1 文字", bool(rx("a?.txt").match("ab.txt")) and not rx("a?.txt").match("a/.txt"))

    with tempfile.TemporaryDirectory() as tmp:
        def git(*args: str) -> None:
            run_git(list(args), tmp)

        try:
            git("init", "-q")
            git("config", "user.email", "selftest@example.invalid")
            git("config", "user.name", "selftest")
            os.makedirs(os.path.join(tmp, "allowed"))
            os.makedirs(os.path.join(tmp, "other"))
            for rel in ("allowed/a.txt", "other/b.txt"):
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
                    f.write("1\n")
            git("add", "-A")
            git("commit", "-q", "-m", "init")

            with open(os.path.join(tmp, "allowed/a.txt"), "a", encoding="utf-8") as f:
                f.write("2\n")
            inside, outside = check(tmp, ["allowed/**"], None)
            expect("範囲の中だけの変更は、範囲の外が 0 件", len(inside) == 1 and len(outside) == 0)

            with open(os.path.join(tmp, "other/b.txt"), "a", encoding="utf-8") as f:
                f.write("2\n")
            with open(os.path.join(tmp, "other/新しい.txt"), "w", encoding="utf-8") as f:
                f.write("1\n")
            inside, outside = check(tmp, ["allowed/**"], None)
            paths = {p for _, p in outside}
            expect("範囲の外の変更を検出する", "other/b.txt" in paths)
            expect("未追跡のファイル（日本語の名前）を検出する", "other/新しい.txt" in paths)

            git("add", "-A")
            git("commit", "-q", "-m", "second")
            git("mv", "other/b.txt", "other/c.txt")
            git("commit", "-q", "-m", "rename")
            inside, outside = check(tmp, ["allowed/**"], "HEAD~2")
            paths = {p for _, p in outside}
            expect(
                "--base で、コミット済みの変更と名前の変更を検出する",
                {"other/c.txt", "other/新しい.txt"} <= paths and len(inside) == 1,
            )
        except (RuntimeError, OSError) as exc:
            expect(f"git を使う試験を実行できた（{exc}）", False)

    failed = [name for name, ok in results if not ok]
    print(f"\n自己テスト: {len(results) - len(failed)}/{len(results)} PASS")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="許した範囲の外で変わったファイルを一覧にする")
    parser.add_argument("--allow", action="append", default=[], help="変えてよいパスの書き方（何度でも指定できる）")
    parser.add_argument("--base", help="この参照から現在までのコミットも見る（例: main）")
    parser.add_argument("--repo", default=".", help="git で管理しているフォルダ（既定は現在のフォルダ）")
    parser.add_argument("--selftest", action="store_true", help="自己テストを実行する")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.selftest:
        return selftest()
    if not args.allow:
        print("--allow で、変えてよいパスを 1 つ以上指定してください。例: --allow \"docs/**\"", file=sys.stderr)
        return 2
    try:
        inside, outside = check(args.repo, args.allow, args.base)
    except FileNotFoundError:
        print("git が見つかりません。git をインストールするか、差分を手動で確認してください。", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"git の実行に失敗しました: {exc}", file=sys.stderr)
        print("git で管理しているフォルダの中で実行するか、--repo で指定してください。", file=sys.stderr)
        return 2
    report(inside, outside, args.allow, args.base)
    return 1 if outside else 0


if __name__ == "__main__":
    sys.exit(main())
