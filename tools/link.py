#!/usr/bin/env python3
"""このリポジトリの skills/<名前> を、エージェントのスキルの置き場に結ぶ（標準ライブラリだけで動く）。

使い方:
  python tools/link.py                       ~/.claude/skills に全スキルを結ぶ
  python tools/link.py --to ~/.agents/skills 置き場を変える
  python tools/link.py --only slide-seyo     一部だけを結ぶ
  python tools/link.py --unlink              このリポジトリへのリンクだけを外す
  python tools/link.py --dry-run             行うことだけを表示する

Windows ではジャンクション（管理者の権限が要らない）、ほかの OS ではシンボリックリンクを作る。
置き場に同じ名前のものがあり、このリポジトリへのリンクでなければ、上書きせずに終わる。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "skills")
WINDOWS = os.name == "nt"


def all_skills() -> list[str]:
    return sorted(d for d in os.listdir(SKILLS)
                  if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md")))


def is_link(path: str) -> bool:
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12 以上
    if isjunction is not None:
        return isjunction(path)
    if WINDOWS:
        try:
            os.readlink(path)  # 3.8 以上ではジャンクションも読める
            return True
        except (OSError, ValueError):
            return False
    return False


def same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def points_here(path: str, name: str) -> bool:
    return is_link(path) and same(path, os.path.join(SKILLS, name))


def make_link(dest: str, target: str) -> None:
    if WINDOWS:
        try:
            import _winapi
            _winapi.CreateJunction(target, dest)
            return
        except (ImportError, AttributeError):
            pass
        subprocess.run(["cmd", "/c", "mklink", "/J", dest, target], check=True,
                       capture_output=True)
    else:
        os.symlink(target, dest, target_is_directory=True)


def remove_link(path: str) -> None:
    if WINDOWS and not os.path.islink(path):
        os.rmdir(path)  # ジャンクションだけを外し、リンク先には触れない
    else:
        os.unlink(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="skills/<名前> をスキルの置き場に結ぶ")
    ap.add_argument("--to", default=os.path.join("~", ".claude", "skills"),
                    help="スキルの置き場（既定: ~/.claude/skills）")
    ap.add_argument("--only", nargs="+", metavar="名前", help="結ぶスキルを限る")
    ap.add_argument("--unlink", action="store_true", help="このリポジトリへのリンクだけを外す")
    ap.add_argument("--dry-run", action="store_true", help="行うことだけを表示する")
    args = ap.parse_args()

    dest_root = os.path.abspath(os.path.expanduser(args.to))
    names = all_skills()
    if args.only:
        unknown = [n for n in args.only if n not in names]
        if unknown:
            print(f"このリポジトリに無いスキル: {', '.join(unknown)}（あるもの: {', '.join(names)}）")
            return 2
        names = [n for n in names if n in args.only]
    prefix = "[dry-run] " if args.dry_run else ""
    kind = "ジャンクション" if WINDOWS else "シンボリックリンク"

    if args.unlink:
        for n in names:
            dest = os.path.join(dest_root, n)
            if points_here(dest, n):
                print(f"{prefix}外す: {dest}")
                if not args.dry_run:
                    remove_link(dest)
            elif os.path.lexists(dest):
                print(f"そのまま: {dest}（このリポジトリへのリンクではない）")
        return 0

    conflicts = [os.path.join(dest_root, n) for n in names
                 if os.path.lexists(os.path.join(dest_root, n))
                 and not points_here(os.path.join(dest_root, n), n)]
    if conflicts:
        print("置き場に同じ名前のものがあり、このリポジトリへのリンクではないので、何もせずに終わる:")
        for c in conflicts:
            print(f"  {c}")
        print("それを別の場所へ移すか消してから実行し直すか、--only で残りだけを結ぶ。")
        return 1

    if not os.path.isdir(dest_root):
        print(f"{prefix}作る: {dest_root}")
        if not args.dry_run:
            os.makedirs(dest_root)
    for n in names:
        dest = os.path.join(dest_root, n)
        target = os.path.join(SKILLS, n)
        if points_here(dest, n):
            print(f"結び済み: {dest}")
            continue
        print(f"{prefix}結ぶ（{kind}）: {dest} -> {target}")
        if not args.dry_run:
            make_link(dest, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
