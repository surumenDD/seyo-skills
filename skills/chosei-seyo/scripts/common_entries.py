"""共通の置き場（SEYO_HOME/common/）に移す候補を挙げ、利用者が指示した項目だけを写す。

候補にするのは、いまのプロジェクトの図鑑に登録された項目（図鑑に id のある控えの項目）だけで、2 種類に限る
（共通に移すかは利用者が決める。このスクリプトは候補を挙げるだけで、写すのは add だけである）。
1. プロジェクトに限らない言い方の印（general が真）がある項目。理由は general_words。
2. いまのプロジェクトの項目と、別のプロジェクトの項目で、発言の写し（user_words）が近い組。
   近さは写しどうしだけで測り、要点（judgment）などほかの欄は使わない。測り方は次のとおり。
   - 写しから空白と記号を除き、小文字にして、連続する 2 文字の組の集合にする。
   - 組ごとに、全プロジェクトと共通の写しのうち、その組を含む写しの少なさで重みを付ける（idf = log((N+1)/(df+1)) + 1）。
     どの文にも出る組（「して」「です」など）が近さを押し上げないようにするためである。
   - 2 つの写しの近さ = 共有する組の重みの和 ÷ 短い側の写しの組の重みの和（重みつきの重なり係数）。
     長さの違う写しで、短い写しがほぼそのまま長い写しに含まれる場合も近いとするためである。
   - 項目の組の近さは、写しの組のうち最も近いもの。近さが --threshold（既定 0.5）以上で、
     共有する組が --min-shared（既定 5）以上なら候補にする。短い句が偶然重なっただけの組を除くためである。
どちらも、既に共通にあるもの（共通に写した項目と、共通の項目と写しが近い項目）は除く。

使い方: python common_entries.py candidates [--round <回>] [--threshold 0.5] [--min-shared 5]
        python common_entries.py add <id> [<id> ...]
            いまのプロジェクトの控えの項目を共通の控えに写し、プロジェクトの図鑑からその id の引用と注を、
            同じ種類、同じ主題の見出しで共通の図鑑に写す（機械的な写し）。共通の id は「<キー>/<id>」になる。
            共通の図鑑は、プロジェクトの図鑑と同じ照合を通してから置く。元のプロジェクトの項目と引用は残す
        python common_entries.py remove <id> [<id> ...]    共通から外す（共通の id か、いまのプロジェクトの id）
        python common_entries.py list                      共通の項目を並べる
共通: --home <フォルダ>、--project <キー>、--cwd <作業フォルダ>
"""
import argparse
import math
import os
import re
import sys
from collections import Counter

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402
import check_genten as G  # noqa: E402

THRESHOLD = 0.5
MIN_SHARED = 5


def grams(s):
    t = re.sub(r"[\W_]+", "", (s or "").lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


class Near:
    """写しどうしの近さ。重みは、渡した写しの全体で数える。"""

    def __init__(self, words):
        self.n = len(words)
        self.df = Counter(g for w in words for g in grams(w))
        self._g = {}

    def idf(self, g):
        return math.log((self.n + 1) / (self.df.get(g, 0) + 1)) + 1.0

    def g(self, w):
        if w not in self._g:
            self._g[w] = grams(w)
        return self._g[w]

    def words(self, a, b):
        ga, gb = self.g(a), self.g(b)
        if not ga or not gb:
            return 0.0, 0
        shared = ga & gb
        wa = sum(self.idf(x) for x in ga)
        wb = sum(self.idf(x) for x in gb)
        return sum(self.idf(x) for x in shared) / min(wa, wb), len(shared)

    def entries(self, ea, eb):
        """(近さ, 共有する組の数, こちらの写し, 相手の写し)。最も近い写しの組。"""
        best = (0.0, 0, "", "")
        for a in ea.get("user_words") or []:
            for b in eb.get("user_words") or []:
                s, n = self.words(a, b)
                if (s, n) > best[:2]:
                    best = (s, n, a, b)
        return best


def common_ids(common, key):
    """共通に写した、いまのプロジェクトの項目の id の集合。"""
    return {(e.get("origin") or {}).get("id") for e in common if (e.get("origin") or {}).get("project") == key}


def registered(d):
    """d の控えのうち、d の図鑑に id のある項目。"""
    gp = C.genten_path(d)
    ids = G.ids_in(C.read_text(gp)) if os.path.isfile(gp) else set()
    return [e for e in C.load_entries(d) if e.get("id") in ids]


def find_candidates(home, key, rnd=None, threshold=THRESHOLD, min_shared=MIN_SHARED):
    """[(項目, [理由の文, ...])] と、既に共通にあるとして除いた数を返す。図鑑に登録された項目だけを比べる。"""
    mine = registered(C.project_dir(home, key))
    common = C.load_entries(C.common_dir(home))
    others = {k: registered(C.project_dir(home, k)) for k in C.all_projects(home) if k != key}
    near = Near([w for es in [mine, common] + list(others.values()) for e in es for w in e.get("user_words") or []])
    done = common_ids(common, key)
    out, n_skip = [], 0
    for e in mine:
        if rnd and e.get("round") != rnd:
            continue
        if e.get("id") in done:
            n_skip += 1
            continue
        if any(s >= threshold and n >= min_shared for s, n, _, _ in (near.entries(e, c) for c in common)):
            n_skip += 1
            continue
        reasons = []
        if e.get("general") is True and C.norm(e.get("general_words")):
            reasons.append("範囲を述べた言い方: 「%s」" % e["general_words"].strip())
        pairs = []
        for k in sorted(others):
            for o in others[k]:
                s, n, a, b = near.entries(e, o)
                if s >= threshold and n >= min_shared:
                    pairs.append((-s, k, o.get("id"), b))
        for s, k, oid, b in sorted(pairs)[:3]:
            reasons.append("別のプロジェクト %s の %s と写しが近い（近さ %.2f）。相手の写し: 「%s」" % (k, oid, -s, b.strip()))
        if len(pairs) > 3:
            reasons.append("ほかにも近い項目が %d ある" % (len(pairs) - 3))
        if reasons:
            out.append((e, reasons))
    return out, n_skip


def format_candidates(cands, key, n_skip):
    if not cands:
        return ["共通に移す候補: なし（プロジェクト %s。既に共通にあるとして除いた項目 %d）" % (key, n_skip)]
    lines = ["共通に移す候補 %d（プロジェクト %s。既に共通にあるとして除いた項目 %d）。移すのは利用者が指示した id だけにする"
             % (len(cands), key, n_skip)]
    for e, reasons in cands:
        lines.append("- %s（%s）" % (e.get("id"), e.get("doc_kind", "")))
        for w in e.get("user_words") or []:
            lines.append("  写し: 「%s」" % " ".join(w.split()))
        for r in reasons:
            lines.append("  理由: " + r)
    lines.append("移すとき: python scripts/common_entries.py add <id> ...")
    return lines


def cmd_add(home, key, ids):
    pdir, cd = C.project_dir(home, key), C.common_dir(home)
    mine = {e.get("id"): e for e in C.load_entries(pdir)}
    pg = C.genten_path(pdir)
    blocks = G.parse(C.read_text(pg))["blocks"] if os.path.isfile(pg) else []
    miss = [i for i in ids if i not in mine]
    if miss:
        raise SystemExit("プロジェクト %s の控えに無い id: %s（何も写していない）" % (key, "、".join(miss)))
    unreg = [i for i in ids if not any(b["id"] == i for b in blocks)]
    if unreg:
        raise SystemExit("プロジェクト %s の図鑑に登録されていない id: %s（何も写していない。外した候補なら、"
                         "先に check_genten.py --include で登録する）" % (key, "、".join(unreg)))
    common = C.load_entries(cd)
    done = common_ids(common, key)
    added, items = [], []
    for i in ids:
        if i in done or i in [x["origin"]["id"] for x in added]:
            print("既に共通にある: %s" % i)
            continue
        e = dict(mine[i])
        e["id"] = "%s/%s" % (key, i)
        e["origin"] = {"project": key, "id": i}
        e["promoted_at"] = C.now_str()
        added.append(e)
        items += [(b["kind"], b["heading"], b["quote"], e["id"], b["note"]) for b in blocks if b["id"] == i]
    if not added:
        return
    cg = C.genten_path(cd)
    prev = C.read_text(cg) if os.path.isfile(cg) else ""
    text = G.add_blocks(prev or G.skeleton("共通", common=True), items)
    new_common = common + added
    bad, _ = G.check(text, prev, {e["id"]: e for e in new_common})
    if bad:
        print("共通の図鑑の照合で問題 %d 件。何も写していない" % len(bad))
        for x in bad[:40]:
            print("  ", x)
        sys.exit(1)
    C.save_entries(cd, new_common)
    C.write_text(cg, text)
    print("共通に写した: 項目 %d、引用 %d（元のプロジェクトの項目と引用は残した）。共通の図鑑 %s（%d 字）"
          % (len(added), len(items), cg, len(text)))


def cmd_remove(home, key, ids):
    cd = C.common_dir(home)
    common = C.load_entries(cd)
    by_id = {e.get("id"): e for e in common}
    targets = set()
    for i in ids:
        if i in by_id:
            targets.add(i)
        elif "%s/%s" % (key, i) in by_id:
            targets.add("%s/%s" % (key, i))
        else:
            raise SystemExit("共通に無い id: %s（何も外していない）" % i)
    rest = [e for e in common if e.get("id") not in targets]
    C.save_entries(cd, rest)
    cg = C.genten_path(cd)
    n = 0
    if os.path.isfile(cg):
        text, n = G.remove_ids(C.read_text(cg), targets)
        if rest:
            C.write_text(cg, text)
        else:
            os.remove(cg)
    print("共通から外した: 項目 %d、引用 %d（元のプロジェクトの項目と引用はそのまま）" % (len(targets), n))


def cmd_list(home):
    common = C.load_entries(C.common_dir(home))
    print("共通の項目 %d" % len(common))
    for e in common:
        w = (e.get("user_words") or [""])[0]
        print("- %s（%s）「%s%s」" % (e.get("id"), e.get("doc_kind", ""), " ".join(w.split())[:40],
                                   "…" if len(" ".join(w.split())) > 40 else ""))


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    base = argparse.ArgumentParser(add_help=False)
    C.add_place_args(base)
    p = sub.add_parser("candidates", parents=[base])
    p.add_argument("--round")
    p.add_argument("--threshold", type=float, default=THRESHOLD)
    p.add_argument("--min-shared", type=int, default=MIN_SHARED)
    for name in ("add", "remove"):
        sub.add_parser(name, parents=[base]).add_argument("ids", nargs="+")
    sub.add_parser("list", parents=[base])
    a = ap.parse_args()
    if a.cmd == "list":
        cmd_list(C.seyo_home(a.home))
        return
    home, key, _ = C.place(a)
    if a.cmd == "candidates":
        cands, n_skip = find_candidates(home, key, a.round, a.threshold, a.min_shared)
        print("\n".join(format_candidates(cands, key, n_skip)))
    elif a.cmd == "add":
        cmd_add(home, key, a.ids)
    else:
        cmd_remove(home, key, a.ids)


if __name__ == "__main__":
    main()
