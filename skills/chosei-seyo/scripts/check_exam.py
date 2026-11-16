"""検証の試験問題（SEYO_HOME/projects/<キー>/chosei/exam/units/<単位の id>/）を、もとの記録と対話の抜き出しと照合する。

確認すること（形式は references/brief_exam.md）
- exhibit.md、context.md、gold.json があるか。gold.json の欄と値。
- 単位の id が、gold.json の round の原稿の記録にあり、その回が試験の回（extract_dialog.py --exam で作った回）か。
- 単位の発言が、設置した回の取り込み済みの範囲の外にあるか（未見か）。取り込み済みなら、未見の測定には使えない。
- exhibit.md と fixed.md の本文の行（出どころの行と省略の印を除き、記号と空白を除いて 8 字を超える行）が、
  対話の抜き出しか、exhibit_sources に書いた git の版にあるか。
- gold.json の points が、もとの記録の points と 1 対 1 で、quote と where が同じか。
- in_exhibit と keep_span が exhibit.md にあるか。確定で本文から言える指摘に in_exhibit があり、
  反論が通った・残すの指摘（測れないを除く）に keep_span があるか。固定の知識が要る指摘があるなら fixed.md があるか。
- chain の id が、同じ試験の回の原稿の記録にあるか。
- context.md が 300 字以内で、この単位の利用者の発言と同じ 12 字以上の並びを含まないか（指摘の漏れの目安）。

使い方: python check_exam.py [--home <フォルダ>] [--project <キー>] [--allow-seen] [単位の id ...]
        id を省くと units/ のすべてを照合する。--allow-seen は、取り込み済みの単位を問題にしない
        （取り込んだ指摘の再現を測る場合）。問題が 1 件でもあれば終了コード 1。
"""
import argparse
import glob
import os
import re
import sys
from collections import Counter

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402
from check_records import git_show  # noqa: E402

STATUS = ("確定", "反論が通った", "残す", "保留")
DERIV = ("本文から言える", "固定の知識が要る", "測れない")
GOLD_KEYS = ("unit", "round", "session", "turn", "date", "exhibit_sources", "exhibit_chars", "exhibit_trimmed",
             "rewrite_ok", "chain", "points")
POINT_KEYS = ("id", "quote", "where", "status", "status_evidence", "derivable", "derivable_reason",
              "in_exhibit", "keep_span")
MAX_CONTEXT = 300
LEAK = 12


def body_lines(text):
    out = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if not s or re.match(r"^出どころ[:：]", s) or re.fullmatch(r"（この間.*省略）", s):
            continue
        if len(C.loose(s)) > 8:
            out.append(s)
    return out


class Exam:
    """試験の回の記録、対話の抜き出し、取り込み済みの範囲を読み、単位を照合する。"""

    def __init__(self, pdir):
        self.home = pdir  # プロジェクトのフォルダ。回、対話、台帳、検証はこの下の chosei/ にある
        self.units_dir = os.path.join(C.exam_dir(pdir), "units")
        self.ledger = C.load_ledger(pdir)
        self.dlg = C.Dialogs(C.dialogs_dir(pdir))
        self._records, self._scopes, self._all = {}, {}, None

    def records(self, rnd):
        if rnd not in self._records:
            rows, bad = {}, []
            for p in sorted(glob.glob(os.path.join(C.round_dir(self.home, rnd), "records", "*.jsonl"))):
                for r in C.read_jsonl(p, bad):
                    if str(r.get("id", "")).startswith("review-"):
                        rows[r["id"]] = r
            self._records[rnd] = rows
        return self._records[rnd]

    def scope(self, rnd):
        if rnd not in self._scopes:
            self._scopes[rnd] = C.read_json(os.path.join(C.round_dir(self.home, rnd), "scope.json"))
        return self._scopes[rnd]

    def all_dialogs(self):
        if self._all is None:
            paths = sorted(glob.glob(os.path.join(C.dialogs_dir(self.home), "*.md")))
            self._all = C.loose("\n".join(C.read_text(p) for p in paths))
        return self._all

    def check(self, uid):
        """(問題の一覧, 注意の一覧, 取り込み済みか, gold.json か None)"""
        bad, warn, seen = [], [], False
        d = os.path.join(self.units_dir, uid)
        for f in ("exhibit.md", "context.md", "gold.json"):
            if not os.path.isfile(os.path.join(d, f)):
                bad.append("%s: %s が無い" % (uid, f))
        if bad:
            return bad, warn, seen, None
        exhibit = C.read_text(os.path.join(d, "exhibit.md"))
        context = C.read_text(os.path.join(d, "context.md"))
        fp = os.path.join(d, "fixed.md")
        fixed = C.read_text(fp) if os.path.isfile(fp) else ""
        try:
            gold = C.read_json(os.path.join(d, "gold.json"))
        except Exception as e:
            return ["%s: gold.json が読めない: %s" % (uid, str(e)[:80])], warn, seen, None
        for k in GOLD_KEYS:
            if k not in gold:
                bad.append("%s: gold.json に欄 %s が無い" % (uid, k))
        if gold.get("unit") != uid:
            bad.append("%s: gold.json の unit が %r" % (uid, gold.get("unit")))
        for k in ("rewrite_ok", "exhibit_trimmed"):
            if k in gold and not isinstance(gold[k], bool):
                bad.append("%s: %s は true か false" % (uid, k))
        if not isinstance(gold.get("chain", []), list):
            bad.append("%s: chain は配列" % uid)
        rnd = gold.get("round") or ""
        sc = self.scope(rnd) if rnd else None
        if sc is None:
            bad.append("%s: 試験の回 %r の scope.json が無い" % (uid, rnd))
        elif sc.get("purpose") != "exam":
            bad.append("%s: 回 %s は試験の回ではない（extract_dialog.py --exam で作った回の記録から組み立てる）" % (uid, rnd))
        src = self.records(rnd).get(uid) if rnd else None
        if src is None:
            bad.append("%s: もとの原稿の記録が、回 %s の records に無い" % (uid, rnd))
            return bad, warn, seen, gold
        sid8, turn = (src.get("session") or "")[:8], src.get("turn")
        if gold.get("session") != sid8 or gold.get("turn") != turn:
            bad.append("%s: gold.json の session と turn が、もとの記録（%s %s）と違う" % (uid, sid8, turn))
        t = self.dlg.turn(sid8, turn)
        cov = C.covered_until_sid8(self.ledger, sid8)
        if t is None or t["ts"] is None:
            bad.append("%s: 発言 %s %s が対話の抜き出しに無い" % (uid, sid8, turn))
        elif cov is not None and t["ts"] <= cov:
            seen = True
        # 見せる本文が、記録から一字も変えずに写されているか
        gits = []
        for s in gold.get("exhibit_sources") or []:
            if not (s.get("commit") or s.get("repo")):
                continue
            repo = s.get("repo") or (src.get("target") or {}).get("repo") or ""
            if not s.get("commit") or not s.get("file"):
                bad.append("%s: リポジトリの版を出どころにするなら commit と file を書く: %s" % (uid, s.get("what", "")))
                continue
            txt = git_show(repo, s["commit"], s["file"]) if repo and os.path.isdir(repo) else None
            if txt is None:
                bad.append("%s: git の版を読めない: %s %s" % (uid, s["commit"], s["file"]))
            else:
                gits.append(C.loose(txt))
        for name, text in (("exhibit.md", exhibit), ("fixed.md", fixed)):
            miss = [ln for ln in body_lines(text)
                    if C.loose(ln) not in self.all_dialogs() and not any(C.loose(ln) in g for g in gits)]
            if miss:
                bad.append("%s: %s の %d 行が、対話の抜き出しにも exhibit_sources の git の版にも無い。例: %s"
                           % (uid, name, len(miss), miss[0][:50]))
        if not body_lines(exhibit):
            bad.append("%s: exhibit.md に本文が無い" % uid)
        # 正解の記録が、もとの記録の指摘と 1 対 1 か
        sp = {"%s#p%s" % (uid, p.get("n")): p for p in src.get("points") or []}
        gp = {p.get("id"): p for p in gold.get("points") or []}
        for k in sp:
            if k not in gp:
                bad.append("%s: gold.json に %s が無い" % (uid, k))
        ex_n, ex_l = C.norm(exhibit), C.loose(exhibit)
        for k, p in gp.items():
            if k not in sp:
                bad.append("%s: %s はもとの記録に無い" % (uid, k))
                continue
            for f in POINT_KEYS:
                if f not in p:
                    bad.append("%s: 欄 %s が無い" % (k, f))
            if C.norm(p.get("quote")) != C.norm(sp[k].get("quote")):
                bad.append("%s: quote がもとの記録と違う" % k)
            if C.norm(p.get("where")) != C.norm(sp[k].get("where")):
                bad.append("%s: where がもとの記録と違う" % k)
            if p.get("status") not in STATUS:
                bad.append("%s: status の値 %r" % (k, p.get("status")))
            if p.get("derivable") not in DERIV:
                bad.append("%s: derivable の値 %r" % (k, p.get("derivable")))
            if not p.get("status_evidence"):
                bad.append("%s: status_evidence が空" % k)
            for f in ("in_exhibit", "keep_span"):
                v = p.get(f) or ""
                if v and C.norm(v) not in ex_n and C.loose(v) not in ex_l:
                    bad.append("%s: %s が exhibit.md に無い: %s" % (k, f, v[:40]))
            if p.get("status") in ("反論が通った", "残す") and p.get("derivable") != "測れない" and not p.get("keep_span"):
                bad.append("%s: status が「%s」なら keep_span を書く（exhibit に該当の箇所が無ければ、"
                           "derivable を「測れない」にして note に理由を書く）" % (k, p.get("status")))
            if p.get("derivable") == "本文から言える" and p.get("status") == "確定" and not p.get("in_exhibit"):
                bad.append("%s: 確定で本文から言える指摘は in_exhibit を書く" % k)
            if p.get("derivable") == "固定の知識が要る" and not fixed:
                bad.append("%s: 固定の知識が要るのに fixed.md が無い" % k)
        for c in gold.get("chain") or []:
            if c == uid or c not in self.records(rnd):
                bad.append("%s: chain の %s が、回 %s の原稿の記録に無い" % (uid, c, rnd))
        # 文脈から指摘が漏れていないか
        if len(context.strip()) > MAX_CONTEXT:
            bad.append("%s: context.md が %d 字（%d 字以内）" % (uid, len(context.strip()), MAX_CONTEXT))
        rt, cn = C.norm(src.get("review_text")), C.norm(context)
        for i in range(0, max(0, len(cn) - LEAK + 1)):
            if cn[i:i + LEAK] in rt:
                bad.append("%s: context.md に、利用者の発言と同じ並びがある: %s" % (uid, cn[i:i + LEAK]))
                break
        return bad, warn, seen, gold


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_place_args(ap)
    ap.add_argument("--allow-seen", action="store_true")
    ap.add_argument("units", nargs="*")
    a = ap.parse_args()
    ex = Exam(C.place(a)[2])
    ids = a.units or (sorted(x for x in os.listdir(ex.units_dir) if os.path.isdir(os.path.join(ex.units_dir, x)))
                      if os.path.isdir(ex.units_dir) else [])
    if not ids:
        print("単位が無い:", ex.units_dir)
        sys.exit(1)
    allbad, allwarn, n = [], [], Counter()
    for uid in ids:
        bad, warn, seen, gold = ex.check(uid)
        if seen and not a.allow_seen:
            bad.append("%s: 発言が取り込み済みの範囲にある（未見でない）。未見の測定には使えない" % uid)
        allbad += bad
        allwarn += warn
        for p in (gold or {}).get("points") or []:
            st, dv = p.get("status"), p.get("derivable")
            if st == "確定" and dv != "測れない":
                n["正解の指摘"] += 1
            elif st in ("反論が通った", "残す") and p.get("keep_span"):
                n["直さないのが正解の箇所"] += 1
            n["status " + str(st)] += 1
            n["derivable " + str(dv)] += 1
    print("単位 %d。%s" % (len(ids), "、".join("%s %d" % kv for kv in sorted(n.items()))))
    if allwarn:
        print("注意 %d 件" % len(allwarn))
        for x in allwarn[:40]:
            print("  ", x)
    print("問題 %d 件" % len(allbad))
    for x in allbad[:80]:
        print("  ", x)
    sys.exit(1 if allbad else 0)


if __name__ == "__main__":
    main()
