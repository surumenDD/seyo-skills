"""文章を書くエージェントが読む図鑑の場所を、読む順に表示する。

読む順は、共通、引き継ぐプロジェクト（project.json の inherits の順）、いまのプロジェクトである。存在する図鑑ごとに、
パス、字数、読み方の行の範囲、文書の種類の節（第 2 階層の見出し）ごとの引用の数と字数と行の範囲を 1 行で出す。
行の範囲は、節だけを読むときに使う。
--kind を付けると、引き継ぐプロジェクトといまのプロジェクトの図鑑は、その種類の節と「進め方」の節だけを出す。
共通の図鑑は節を問わず全体を読むので、節ごとの内訳の代わりに、全体を読むことと引用の数を出す。共通には利用者が
指示した指摘だけが元の種類の節のまま写されるので、種類の節だけを読むと、別の種類の文書を書くときに読まれないからである。
図鑑が 1 つも無ければ、そのことを 1 行で出して正常に終わる。
このスクリプトは何も書き込まない。プロジェクトを決められない場合（同じ名前で場所の違う作業フォルダ）は、
共通の図鑑だけを出し、理由を示して終了コード 2 で終わる。

使い方: python seyo_where.py [--kind <文書の種類>] [--home <フォルダ>] [--project <キー>] [--cwd <作業フォルダ>]
"""
import argparse
import os
import sys

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402
from check_genten import CMT  # noqa: E402


def sections(path):
    """[(名前, 引用の数, 字数, 最初の行, 最後の行)]。最初は読み方（題から最初の節の前まで）。"""
    lines = C.read_text(path).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out, cur = [], ["読み方", 0, 0, 1, 0]
    for i, ln in enumerate(lines, 1):
        if ln.startswith("## "):
            cur[4] = i - 1
            out.append(tuple(cur))
            cur = [ln[3:].strip(), 0, 0, i, 0]
        elif CMT.match(ln.strip()):
            cur[1] += 1
        cur[2] += len(ln) + 1
    cur[4] = len(lines)
    out.append(tuple(cur))
    return out


def describe(label, path, kind=None, whole=False):
    secs = sections(path)
    total = len(C.read_text(path))
    if kind and whole:
        return "%s: %s 計 %s 字｜全体を読む 引用 %d" % (label, path, format(total, ","), sum(s[1] for s in secs))
    parts = ["読み方 行 %d〜%d" % (secs[0][3], secs[0][4])]
    names = [s[0] for s in secs[1:]]
    for name, n, chars, a, b in secs[1:]:
        if kind and name not in (kind, "進め方"):
            continue
        parts.append("%s 引用 %d %s 字 行 %d〜%d" % (name, n, format(chars, ","), a, b))
    if kind:
        for want in sorted({kind, "進め方"}, key=C.KINDS.index):
            if want not in names:
                parts.append("%s の節なし" % want)
    return "%s: %s 計 %s 字｜%s" % (label, path, format(total, ","), "｜".join(parts))


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_place_args(ap)
    ap.add_argument("--kind", help="文書の種類（%s）。共通の図鑑は種類によらず全体を読む対象として示す" % "、".join(C.KINDS))
    a = ap.parse_args()
    if a.kind and a.kind not in C.KINDS:
        raise SystemExit("--kind は %s のどれか: %s" % ("、".join(C.KINDS), a.kind))
    home = C.seyo_home(a.home)
    found = []
    cg = C.genten_path(C.common_dir(home))
    if os.path.isfile(cg):
        found.append(("共通", cg))
    try:
        key, pdir, how, _ = C.resolve_project(home, a.project, a.cwd)
    except SystemExit as e:
        for i, (label, path) in enumerate(found, 1):
            print("%d. %s" % (i, describe(label, path, a.kind, label == "共通")))
        print("プロジェクトを決められない: %s" % e)
        sys.exit(2)
    pj = C.load_project(home, key) or {}
    missing = []
    for src in pj.get("inherits") or []:
        g = C.genten_path(C.project_dir(home, src))
        if os.path.isfile(g):
            found.append(("引き継ぎ %s" % src, g))
        else:
            missing.append(src)
    g = C.genten_path(pdir)
    if os.path.isfile(g):
        found.append(("いまのプロジェクト %s" % key, g))
    if not found:
        print("図鑑は無い（共通、プロジェクト %s、引き継ぎ %s のどれにも無い）。層だけで進める"
              % (key, "、".join(pj.get("inherits") or []) or "なし"))
        return
    print("図鑑を読む順（プロジェクト %s、決め方: %s）。各図鑑の読み方と、書く文書の種類の節と「進め方」の節を読む。"
          "共通の図鑑は節を問わず全体を読む" % (key, how))
    for i, (label, path) in enumerate(found, 1):
        print("%d. %s" % (i, describe(label, path, a.kind, label == "共通")))
    if missing:
        print("引き継ぎに指定した %s には図鑑が無い" % "、".join(missing))


if __name__ == "__main__":
    main()
