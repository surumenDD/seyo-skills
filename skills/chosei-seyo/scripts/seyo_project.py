"""プロジェクトの一覧、登録、場所の追加、引き継ぐプロジェクトの指定と解除。

プロジェクトは SEYO_HOME/projects/<キー>/ で、project.json にキー、作業フォルダの場所の一覧、引き継ぐプロジェクトの一覧を持つ。
作業フォルダからキーを決める順は、--project、環境変数 SEYO_PROJECT、場所の一覧との一致、作業フォルダの最上位
（git の作業ツリーならその最上位）の名前である。名前で決めたキーに別の場所が登録されていれば、決めずに終了する。
引き継ぎは、利用者が元のプロジェクトを名指ししたときにだけ設定する。引き継いだプロジェクトの図鑑は、
共通の図鑑のあと、いまのプロジェクトの図鑑の前に読まれる（seyo_where.py）。引き継ぎは 1 段だけで、
引き継いだプロジェクトがさらに引き継ぐものは読まない。

使い方: python seyo_project.py list
        python seyo_project.py current                       作業フォルダから決まるキーと、その決め方
        python seyo_project.py add <キー> [--path <フォルダ> ...]
        python seyo_project.py add-path <キー> <フォルダ>     場所を加える（git の作業ツリーならその最上位を登録する）
        python seyo_project.py remove-path <キー> <フォルダ>
        python seyo_project.py inherit <元のキー>             いまのプロジェクトが、元のプロジェクトの図鑑を引き継ぐ
        python seyo_project.py uninherit <元のキー>
共通: --home <フォルダ>。current、inherit、uninherit は --project <キー> と --cwd <作業フォルダ> も受け取る
"""
import argparse
import os
import sys

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402


def exists(home, key):
    d = C.project_dir(home, key)
    return os.path.isfile(C.project_json_path(d)) or os.path.isfile(C.entries_path(d))


def owner_of(home, path, but=None):
    for k, pj in C.all_projects(home).items():
        if k != but and any(C.samepath(p, path) for p in pj.get("paths") or []):
            return k
    return None


def place_of(folder):
    return C.top_of(folder) if os.path.isdir(folder) else os.path.normpath(os.path.abspath(folder))


def cmd_list(home):
    projs = C.all_projects(home)
    if not projs:
        print("プロジェクトは無い（%s）" % C.projects_dir(home))
    for k, pj in projs.items():
        d = C.project_dir(home, k)
        g = C.genten_path(d)
        waiting = sum(1 for r in C.load_ledger(d).get("rounds", []) if r.get("genten") == "未登録")
        print("%s  控えの項目 %d  登録を待つ回 %d  図鑑 %s  場所: %s  引き継ぎ: %s" % (
            k, len(C.load_entries(d)), waiting, ("%d 字" % len(C.read_text(g))) if os.path.isfile(g) else "なし",
            "、".join(pj.get("paths") or []) or "なし", "、".join(pj.get("inherits") or []) or "なし"))
    cd = C.common_dir(home)
    g = C.genten_path(cd)
    print("共通  控えの項目 %d  図鑑 %s" % (len(C.load_entries(cd)), ("%d 字" % len(C.read_text(g))) if os.path.isfile(g) else "なし"))


def cmd_add(home, key, paths):
    key = C.check_key(key)
    if exists(home, key):
        raise SystemExit("プロジェクト %s は既にある。場所を加えるなら add-path を使う" % key)
    places = [place_of(p) for p in paths or []]
    for p in places:
        o = owner_of(home, p)
        if o:
            raise SystemExit("場所 %s は、プロジェクト %s に登録されている。場所は 1 つのプロジェクトにだけ登録する" % (p, o))
    C.save_project(home, key, {"key": key, "paths": places, "inherits": []})
    print("登録した: %s（場所: %s）" % (key, "、".join(places) or "なし"))


def cmd_add_path(home, key, folder):
    if not exists(home, key):
        raise SystemExit("プロジェクト %s が無い。新しく登録するなら add を使う" % key)
    p = place_of(folder)
    o = owner_of(home, p, but=key)
    if o:
        raise SystemExit("場所 %s は、プロジェクト %s に登録されている。場所は 1 つのプロジェクトにだけ登録する" % (p, o))
    C.ensure_project(home, key, p)
    print("場所を加えた: %s ← %s" % (key, p))


def cmd_remove_path(home, key, folder):
    pj = C.load_project(home, key)
    if pj is None:
        raise SystemExit("プロジェクト %s が無い" % key)
    p = place_of(folder)
    rest = [x for x in pj.get("paths") or [] if not C.samepath(x, p) and not C.samepath(x, folder)]
    if len(rest) == len(pj.get("paths") or []):
        raise SystemExit("場所 %s は、プロジェクト %s に登録されていない" % (p, key))
    pj["paths"] = rest
    C.save_project(home, key, pj)
    print("場所を外した: %s から %s" % (key, p))


def cmd_inherit(home, a, on):
    src = C.check_key(a.source)
    key, _, how, _ = C.resolve_project(home, a.project, a.cwd, create=True)
    pj = C.ensure_project(home, key)
    inh = list(pj.get("inherits") or [])
    if on:
        if src == key:
            raise SystemExit("自分自身は引き継げない: %s" % key)
        if not exists(home, src):
            raise SystemExit("プロジェクト %s が無い（seyo_project.py list で確かめる）" % src)
        if src in inh:
            print("既に引き継いでいる: %s ← %s" % (key, src))
            return
        inh.append(src)
        msg = "引き継ぐ: %s ← %s" % (key, src)
    else:
        if src not in inh:
            raise SystemExit("プロジェクト %s は %s を引き継いでいない" % (key, src))
        inh.remove(src)
        msg = "引き継ぎを解いた: %s ← %s" % (key, src)
    pj["inherits"] = inh
    C.save_project(home, key, pj)
    print("%s（いまのプロジェクトの決め方: %s。引き継ぎ: %s）" % (msg, how, "、".join(inh) or "なし"))


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--home")
    here = argparse.ArgumentParser(add_help=False)
    C.add_place_args(here)
    sub.add_parser("list", parents=[base])
    sub.add_parser("current", parents=[here])
    p = sub.add_parser("add", parents=[base])
    p.add_argument("key")
    p.add_argument("--path", action="append")
    for name in ("add-path", "remove-path"):
        p = sub.add_parser(name, parents=[base])
        p.add_argument("key")
        p.add_argument("folder")
    for name in ("inherit", "uninherit"):
        sub.add_parser(name, parents=[here]).add_argument("source")
    a = ap.parse_args()
    home = C.seyo_home(a.home)
    if a.cmd == "list":
        cmd_list(home)
    elif a.cmd == "current":
        key, pdir, how, top = C.resolve_project(home, a.project, a.cwd)
        pj = C.load_project(home, key)
        print("プロジェクト %s（決め方: %s。作業フォルダの最上位: %s）%s" % (
            key, how, top, "" if pj else "。まだ登録されていない（取り込みの回を作ると登録される）"))
    elif a.cmd == "add":
        cmd_add(home, a.key, a.path)
    elif a.cmd == "add-path":
        cmd_add_path(home, a.key, a.folder)
    elif a.cmd == "remove-path":
        cmd_remove_path(home, a.key, a.folder)
    else:
        cmd_inherit(home, a, a.cmd == "inherit")


if __name__ == "__main__":
    main()
