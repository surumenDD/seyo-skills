"""古い形式の候補（cand_*.jsonl。doc_kind、general、general_words の欄が無い）を、プロジェクトの控えに足す。

図鑑への登録はこのスクリプトでは行わない。元のファイルごとに回（<--round の値>-<ファイル名の幹>）を作り、
台帳に「登録を待つ回」として記録する。回のフォルダには、そのプロジェクトに振り分けた候補を、欄を補った形で
cand_01.jsonl に書き、scope.json に用途 migrate と元のファイルを書く。登録の担当は、この候補を読んで図鑑に足す
（references/brief_register.md。回を 1 つずつ、check_genten.py --round <回> --start から始める）。
- 文書の種類は、--kind を渡せばそれに、無ければ applies_to から決め（applies_to の中で最も前に現れた手がかりの語の種類）、
  決められなければ「その他」にする。候補に doc_kind があれば、それを使う。
- general は偽、general_words は空にする。古い候補の担当は、範囲を述べた語に印を付けていないからである。
- 振り分け: --route <ID の先頭>=<キー> を順に試し、根拠（evidence）の語（- と空白と # で区切った語）のどれかが
  その先頭で始まれば、そのキーのプロジェクトに入れる。どの --route にも当たらない候補は、既定のプロジェクト
  （--project、環境変数 SEYO_PROJECT、作業フォルダの順で決まる）に入れる。
  --mixed all を付けると、根拠ごとに振り分け、根拠が複数のプロジェクトにまたがる候補を、そのすべてに入れる
  （既定の first は、最初に当たった --route のプロジェクトだけに入れる）。
- 項目の id は「<回>:<候補の id>」にする。同じ回は、同じプロジェクトに 2 度足さない。
  取り消すには、プロジェクトごとに install_round.py --remove <回> を実行する。
- --dialogs <フォルダ> を付けると、根拠の発言の時刻を対話の抜き出しで引いて控えに書く。

使い方: python migrate_legacy.py --round <名前> [--route <ID の先頭>=<キー> ...] [--project <既定のキー>]
                                 [--kind <種類>] [--mixed first|all] [--dialogs <フォルダ>] [--dry-run]
                                 [--home <フォルダ>] [--cwd <作業フォルダ>] <cand_*.jsonl ...>
"""
import argparse
import os
import re
import sys
from collections import Counter, OrderedDict

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402
import install_round  # noqa: E402


def parse_routes(items):
    out = []
    for x in items or []:
        if "=" not in x:
            raise SystemExit("--route は <ID の先頭>=<キー> の形: %s" % x)
        pre, key = x.split("=", 1)
        if not pre.strip():
            raise SystemExit("--route の ID の先頭が空: %s" % x)
        out.append((pre.strip(), C.check_key(key)))
    return out


def route_of(e, routes):
    toks = C.evidence_tokens(e)
    for pre, key in routes:
        if any(t.startswith(pre) for t in toks):
            return key
    return None


def round_name(prefix, path):
    stem = re.sub(r"[^0-9A-Za-z._-]", "_", os.path.splitext(os.path.basename(path))[0])
    return "%s-%s" % (prefix, stem)


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round", required=True)
    ap.add_argument("--route", action="append")
    ap.add_argument("--kind")
    ap.add_argument("--mixed", choices=("first", "all"), default="first")
    ap.add_argument("--dialogs")
    ap.add_argument("--dry-run", action="store_true")
    C.add_place_args(ap)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    if not re.fullmatch(r"[0-9A-Za-z._-]+", a.round):
        raise SystemExit("回の名前は英数字と . _ - だけにする: %s" % a.round)
    if a.kind and a.kind not in C.KINDS:
        raise SystemExit("--kind は %s のどれか: %s" % ("、".join(C.KINDS), a.kind))
    home = C.seyo_home(a.home)
    routes = parse_routes(a.route)
    bad, rows = [], []
    names = Counter(round_name(a.round, f) for f in a.files)
    bad += ["元のファイルの名前が重なり、回の名前が同じになる: %s" % k for k, v in names.items() if v > 1]
    for f in a.files:
        for r in C.read_jsonl(f, bad):
            rows.append((f, r))
    for f in a.files:
        ids = Counter(r.get("id") for g, r in rows if g == f)
        bad += ["%s: id %s が重複している" % (os.path.basename(f), k) for k, v in ids.items() if v > 1]
    for f, r in rows:
        rid = r.get("id")
        if not rid:
            bad.append("%s: id の無い候補がある" % os.path.basename(f))
        if not isinstance(r.get("user_words"), list) or not r.get("user_words"):
            bad.append("%s: %s の user_words が空か、配列でない" % (os.path.basename(f), rid))
        if not isinstance(r.get("evidence"), list):
            bad.append("%s: %s の evidence が配列でない" % (os.path.basename(f), rid))
        if r.get("doc_kind") and r["doc_kind"] not in C.KINDS:
            bad.append("%s: %s の doc_kind の値 %r" % (os.path.basename(f), rid, r["doc_kind"]))
    if bad:
        print("移さない。問題 %d 件" % len(bad))
        for x in sorted(set(bad))[:40]:
            print("  ", x)
        sys.exit(1)
    dlg = C.Dialogs(a.dialogs) if a.dialogs else None
    plan = OrderedDict()  # (キー, 回) → [(候補, 項目)]
    how_n, n_mixed, n_default = Counter(), 0, 0
    default_key = [None]

    def default():
        if default_key[0] is None:
            default_key[0] = C.resolve_project(home, a.project, a.cwd)[0]
        return default_key[0]
    for f, r in rows:
        rnd = round_name(a.round, f)
        up, how = C.upgrade_candidate(r, a.kind)
        how_n[how] += 1
        per = [route_of(e, routes) for e in up.get("evidence") or []]
        if len(set(k or "" for k in per)) > 1:
            n_mixed += 1
        if not any(per):
            n_default += 1
        if a.mixed == "all":
            targets = list(OrderedDict.fromkeys(k or default() for k in (per or [None])))
        else:
            first = next((key for pre, key in routes
                          if any(t.startswith(pre) for e in up.get("evidence") or [] for t in C.evidence_tokens(e))), None)
            targets = [first or default()]
        t, d = C.evidence_time(dlg, up.get("evidence")) if dlg else ("", "")
        for k in targets:
            e = install_round.to_entry(rnd, up, t, d)
            e["migrated_from"] = os.path.basename(f)
            plan.setdefault((k, rnd), []).append((up, e))
    stop = ["%s の %s" % (rnd, k) for k, rnd in plan if install_round.installed_already(C.project_dir(home, k), rnd)]
    if stop:
        print("移し済みの回がある: %s。同じ回を 2 度足さない（入れ直すなら install_round.py --remove <回> --project <キー>）"
              % "、".join(stop))
        sys.exit(1)
    print("候補 %d（%s）。種類の決め方: %s。根拠が複数のプロジェクトにまたがる候補 %d、どの --route にも当たらない候補 %d"
          % (len(rows), "、".join(os.path.basename(f) for f in a.files),
             "、".join("%s %d" % kv for kv in how_n.most_common()), n_mixed, n_default))
    per_key = OrderedDict()
    for (k, rnd), items in plan.items():
        per_key.setdefault(k, []).append((rnd, items))
    for k, rounds in per_key.items():
        pdir = C.project_dir(home, k)
        es = [e for _, items in rounds for _, e in items]
        kinds = Counter(e["doc_kind"] for e in es)
        line = "プロジェクト %s: 控えの項目 %d（%s）、登録を待つ回 %d（%s）" % (
            k, len(es), "、".join("%s %d" % (x, kinds[x]) for x in C.KINDS if x in kinds), len(rounds),
            "、".join(rnd for rnd, _ in rounds))
        if a.dry_run:
            print(line + "（--dry-run なので書き込まない）")
            continue
        rows_now = C.load_entries(pdir)
        have = {x.get("id") for x in rows_now}
        clash = [e["id"] for e in es if e["id"] in have]
        if clash:
            raise SystemExit("プロジェクト %s の控えに同じ id がある: %s" % (k, "、".join(clash[:10])))
        C.ensure_project(home, k)
        C.save_entries(pdir, rows_now + es)
        for rnd, items in rounds:
            rdir = C.round_dir(pdir, rnd)
            C.write_jsonl(os.path.join(rdir, "cand_01.jsonl"), [up for up, _ in items])
            src = sorted({e["migrated_from"] for _, e in items})
            C.write_json(os.path.join(rdir, "scope.json"), {"round": rnd, "project": k, "purpose": "migrate",
                                                             "created_at": C.now_str(), "sources": src, "sessions": []})
            install_round.record_round(pdir, {"name": rnd, "kind": "移行", "installed_at": C.now_str(),
                                              "entries": len(items), "genten": "未登録", "sources": src, "sessions": {}})
        print(line)
    if not a.dry_run:
        print("図鑑への登録は回ごとに行う: python scripts/check_genten.py --round <回> --project <キー> --start のあと、"
              "登録の担当に references/brief_register.md で案に足させ、--install で照合して置く")


if __name__ == "__main__":
    main()
