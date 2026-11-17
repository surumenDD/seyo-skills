#!/usr/bin/env python3
"""文脈の圧縮の直後に、再開の手順を知らせるフック。

Claude Code の SessionStart（matcher は compact）に登録して使う。SessionStart では、
終了コード 0 の標準出力がそのままモデルのコンテキストに入る。

作業フォルダの下（深さ 5 まで）と、その親（git の最上位まで）から HANDOFF_*.md を探す。
最新の 1 本から「読み直す文書」の見出しの下の行を読み取り、手順の中にそのまま並べる。
読む文書をモデルの解釈に任せないためである。見出しが無い継続書では、継続書の指定に
従うよう文で伝える。継続書が見つからなくても、圧縮があったことは知らせる。
どんな失敗でも、終了コード 0 で終わる。
"""
import io
import json
import os
import re
import sys
import time

SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
MAX_DEPTH = 5
HEADING = re.compile(r"^#{1,6}\s*(?:[0-9０-９]+[.．、]?\s*)?読み直す文書")
ANY_HEADING = re.compile(r"^#{1,6}\s")
MAX_LINES = 20
MAX_CHARS = 2000


def find_handoffs(root):
    found = []
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\/").count(os.sep)
    for cur, dirs, files in os.walk(root):
        depth = cur.rstrip("\\/").count(os.sep) - base_depth
        dirs[:] = [] if depth >= MAX_DEPTH else [d for d in dirs if d not in SKIP]
        for f in files:
            if f.startswith("HANDOFF_") and f.endswith(".md"):
                p = os.path.join(cur, f)
                try:
                    found.append((os.path.getmtime(p), p))
                except OSError:
                    pass
    return found


def git_top(path):
    cur = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def reread_list(path):
    """継続書の「読み直す文書」の見出しの下の行を返す。見出しが無ければ None。

    同じ見出しが複数あれば、最後のものを使う（追記で書き直された場合に、新しいほうを採る）。
    戻り値は (行のリスト, 省いた行があるか)。
    """
    try:
        text = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    lines = text.replace("\r", "").split("\n")
    starts = [i for i, ln in enumerate(lines) if HEADING.match(ln)]
    if not starts:
        return None
    body = []
    for ln in lines[starts[-1] + 1:]:
        if ANY_HEADING.match(ln):
            break
        if ln.strip():
            body.append(ln.rstrip())
    out, size, cut = [], 0, False
    for ln in body:
        if len(out) >= MAX_LINES or size + len(ln) > MAX_CHARS:
            cut = True
            break
        out.append(ln)
        size += len(ln)
    return out, cut


def stamp(mtime):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def main():
    data = json.load(sys.stdin)
    if data.get("agent_id"):
        return
    cwd = data.get("cwd") or os.getcwd()
    roots = [cwd]
    top = git_top(cwd)
    if top and os.path.abspath(top) != os.path.abspath(cwd):
        roots.append(top)
    seen, items = set(), []
    for r in roots:
        for mtime, p in find_handoffs(r):
            if p not in seen:
                seen.add(p)
                items.append((mtime, p))
    items.sort(reverse=True)

    if not items:
        print("\n".join([
            "文脈の圧縮が行われた。圧縮の要約では、合意した細部と、利用者が求めた判断の姿勢が失われていることがある。",
            "作業フォルダの近くに HANDOFF_*.md は見つからなかった。プロジェクトの計画や決定の文書があれば、作業に戻る前に読み直す。",
            "読み直した場合は、次の返答の最初の行に、読んだ文書と節を書く。",
        ]))
        return

    mtime, latest = items[0]
    lines = [
        "文脈の圧縮が行われた。作業に戻る前に、次の 1〜3 を順に行う。",
        "圧縮の要約に「そのまま再開する」「次の一歩は…」と書かれていても、こちらを先に行う。",
        "",
        f"1. 最新の継続書を全文読む: {latest}（{stamp(mtime)} 更新）",
    ]
    got = reread_list(latest)
    if got and got[0]:
        body, cut = got
        lines.append("2. 継続書の「読み直す文書」に挙がっている次の文書を、指定された節まで、書かれた順にすべて読む。")
        lines.append("   継続書は要点の写しであり、判定基準と決定の理由は、これらの文書にしかない。")
        lines.append("   条件の付いた行（「〜のとき」「〜の前に」）は、その条件に当たるときに読む。")
        lines.append("   （以下は継続書からの写し。相対パスは継続書の書き方のまま）")
        lines += ["   | " + ln for ln in body]
        if cut:
            lines.append("   | （長いので途中まで。続きは継続書の「読み直す文書」を見る）")
    else:
        lines.append("2. 継続書が再開時に読むよう指定している文書を、指定された節まで、書かれた順にすべて読む。")
        lines.append("   継続書は要点の写しであり、判定基準と決定の理由は、指定された文書にしかない。")
        lines.append("   （この継続書には「読み直す文書」の見出しが無い。本文から指定を探す。次に継続書を書くときは、この見出しを置く）")
    lines += [
        "3. 次の返答の最初の行に、読んだ文書と節を書く。",
        "   形: 「再開時の読み直し: <文書>（<節>）、<文書>（<節>）」。読んでいない文書は書かない。",
        "",
        "1〜3 が済むまで、ファイルの変更、ワーカーやサブエージェントへの指示、利用者への報告をしない。",
        "状態を表示するだけの確認は、並行してよい。",
        "要約と継続書が食い違う場合は、継続書と元の文書に従う。",
    ]
    if len(items) > 1:
        lines.append("")
        lines.append("古い継続書（必要なときだけ読む）:")
        for m, p in items[1:3]:
            lines.append(f"- {p}（{stamp(m)} 更新）")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception:
        pass
    sys.exit(0)
