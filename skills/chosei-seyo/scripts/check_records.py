"""取り出した記録（原稿の記録 review-… と、進め方の記録 scene-…）を、対話の抜き出しと照合する。

確認すること（形式は references/data.md の 3 節と 4 節）
- 欄がそろっているか。値が決められた範囲か。id が「review-<ID8>-<番号>-<連番>」の形で、session と turn に合うか。
- turn が対話にあり、回の範囲（scope.json）の中にあり、重複の印が無いか。date が見出しの現地の日付と同じか。
- 写しの欄が、もとの文に一字一句あるか（空白を除いて比べる。「（中略）」と改行で区切った断片ごと）。
  review_text と critique はその発言の中、quote は review_text の中、evidence は記した発言の中、
  excerpt は記した発言の直前の応答の中、prior_instructions の quote はその発言の中。
- target_text と after.text_or_path は、8 字を超える行ごとに、対話の中か、target.repo（この PC の作業ツリーのパス）の
  files（作業ツリーからの相対パス）にあるか。commit があればその版（git show）、空ならいまの作業ツリーのファイルと比べる。
  「texts/<ファイル>.md」と書いた場合は、records/texts/ のファイルを読んで照合する。
- 反応（user_followup）の発言が、指摘より後にあるか。

使い方: python check_records.py --round <名前> [--home <フォルダ>] [--project <キー>] [records のファイル ...]
        ファイルを省くと、chosei/rounds/<名前>/records/ の *.jsonl をすべて照合する。問題が 1 件でもあれば終了コード 1。
"""
import argparse
import glob
import os
import re
import subprocess
import sys
from collections import Counter

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402

SCALES = {"語句", "文", "段落", "章・節", "文書全体", "図・デザイン"}
FOLLOW = {"approved", "approved_blanket", "re_criticized", "withdrawn", "discarded_later", "unknown"}
ERAS = {"通常", "退けられた期間"}
KINDS = {"進め方への指摘", "返答・報告への指摘", "内部文書への指摘", "進め方・返答への肯定", "取り下げ・留保"}
ALONE = {"はい", "一部", "いいえ"}
CONF = {"高", "中", "低"}
REVIEW_KEYS = ("id", "session", "turn", "date", "target", "review_text", "points", "era", "note")
POINT_KEYS = ("n", "quote", "scale", "where", "target_text", "after", "user_followup")
SCENE_KEYS = ("id", "session", "turn", "date", "kind", "critique", "ai_response", "what_happened",
              "prior_instructions", "from_response_alone", "confidence", "note")

_git = {}


def git_show(repo, commit, path):
    key = (repo, commit, path)
    if key not in _git:
        try:
            out = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (commit, path)], capture_output=True, timeout=60)
            _git[key] = out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None
        except Exception:
            _git[key] = None
    return _git[key]


def load_version(v, texts_dir):
    """(本文, 問題の文か空)。「texts/<ファイル>.md」の 1 行なら、そのファイルを読む。"""
    v = (v or "").strip()
    if "\n" not in v and re.fullmatch(r"texts[\\/][^\s]+\.md", v):
        p = os.path.join(texts_dir, os.path.basename(v))
        if not os.path.isfile(p):
            return "", "ファイルが無い: " + v
        return C.read_text(p), ""
    return v, ""


class Checker:
    def __init__(self, pdir, name):
        self.rdir = C.round_dir(pdir, name)
        self.scope = C.load_scope(pdir, name)
        self.idx = C.scope_index(self.scope)
        tz = C.tz_of(self.scope.get("tz")) if self.scope.get("tz") else None
        self.dlg = C.Dialogs(C.dialogs_dir(pdir), tz)
        self.bad, self.warn = [], []
        self.n = {k: [0, 0, 0] for k in ("review_text", "quote", "evidence", "critique", "excerpt")}
        self.c = {k: Counter() for k in ("scale", "follow", "kind", "target_text", "after", "era")}
        self.n_points = 0

    def count(self, key, res):
        ok, lo, _ = res
        self.n[key][0] += ok
        self.n[key][1] += lo
        self.n[key][2] += 1

    def base(self, r, keys, prefix):
        rid = r.get("id", "?")
        for k in keys:
            if k not in r:
                self.bad.append((rid, "欄", "%s が無い" % k))
        sid, turn = (r.get("session") or "")[:8], r.get("turn") or ""
        if not str(rid).startswith("%s-%s-%s-" % (prefix, sid, turn)):
            self.bad.append((rid, "id", "「%s-%s-%s-<連番>」の形にする" % (prefix, sid, turn)))
        t = self.dlg.turn(sid, turn)
        if self.dlg.get(sid) is None:
            self.bad.append((rid, "session", "対話が dialogs に無い: %s" % sid))
            return None
        if t is None:
            self.bad.append((rid, "turn", "%s が対話に無い" % turn))
            return None
        if t["dup"]:
            self.bad.append((rid, "turn", "重複の印がある発言（%s と同じ）。元の発言で記録する" % t["dup"]))
        if not C.in_scope(self.idx, sid, t["ts"]):
            self.bad.append((rid, "turn", "%s は回の範囲の外" % turn))
        if r.get("date") != t["date"]:
            self.bad.append((rid, "date", "%s ≠ 見出しの日付 %s" % (r.get("date"), t["date"])))
        return t

    def version(self, label, text, sid, repo, commit, files, pid):
        body, err = load_version(text, os.path.join(self.rdir, "records", "texts"))
        if err:
            self.bad.append((pid, label, err))
            self.c[label]["無"] += 1
            return
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        if not C.norm(body):
            self.c[label]["空"] += 1
            return
        d = self.dlg.get(sid)
        dn, dl = (d[1], d[2]) if d else ("", "")
        gn = gl = ""
        gname = "git"
        if repo and files and os.path.isdir(repo):
            if commit:
                srcs = [s for s in (git_show(repo, commit, f) for f in files) if s]
            else:
                # 版を特定できなかった場合は、いまの作業ツリーのファイルと照合する。版が違えば一致しないことがある
                gname = "作業ツリー"
                srcs = [C.read_text(os.path.join(repo, f)) for f in files if os.path.isfile(os.path.join(repo, f))]
            gn, gl = C.norm(NLJ(srcs)), C.loose(NLJ(srcs))
        miss, src = [], set()
        for p in C.pieces(body, 8):
            if C.norm(p) in dn or (C.loose(p) and C.loose(p) in dl):
                src.add("対話")
            elif gn and (C.norm(p) in gn or (C.loose(p) and C.loose(p) in gl)):
                src.add(gname)
            else:
                miss.append(p.strip())
        if gname == "作業ツリー" and gname in src:
            self.warn.append((pid, label, "commit が空なので、いまの作業ツリーのファイルと照合した"))
        if miss:
            self.c[label]["不一致"] += 1
            self.bad.append((pid, label, "%d 行が対話にも git にも無い。例: %s" % (len(miss), miss[0][:60])))
        else:
            self.c[label]["+".join(sorted(src)) or "短い行のみ"] += 1

    def review(self, r):
        rid = r.get("id", "?")
        t = self.base(r, REVIEW_KEYS, "review")
        sid = (r.get("session") or "")[:8]
        self.c["era"][r.get("era")] += 1
        if r.get("era") not in ERAS:
            self.bad.append((rid, "era", str(r.get("era"))))
        if t is not None:
            res = C.find_in(C.norm(t["user"]), C.loose(t["user"]), r.get("review_text", ""))
            self.count("review_text", res)
            if res[2]:
                self.bad.append((rid, "review_text", "その発言に無い: " + res[2][0][:60]))
        rn, rl = C.norm(r.get("review_text", "")), C.loose(r.get("review_text", ""))
        tg = r.get("target") or {}
        for k in ("what", "repo", "commit", "files"):
            if k not in tg:
                self.bad.append((rid, "target", "%s が無い" % k))
        points = r.get("points") or []
        if not points:
            self.bad.append((rid, "points", "指摘が無い"))
        for p in points:
            self.n_points += 1
            pid = "%s#p%s" % (rid, p.get("n"))
            for k in POINT_KEYS:
                if k not in p:
                    self.bad.append((pid, "欄", "%s が無い" % k))
            for s in [x.strip() for x in re.split(r"[／/、,]", p.get("scale", "")) if x.strip()]:
                self.c["scale"][s] += 1
                if s not in SCALES:
                    self.bad.append((pid, "scale", s))
            res = C.find_in(rn, rl, p.get("quote", ""))
            self.count("quote", res)
            if res[2] or not C.norm(p.get("quote")):
                self.bad.append((pid, "quote", "review_text に無い: " + (res[2][0][:60] if res[2] else "（空）")))
            self.version("target_text", p.get("target_text", ""), sid, tg.get("repo"), tg.get("commit"),
                         tg.get("files") or [], pid)
            af = p.get("after") or {}
            self.version("after", af.get("text_or_path", ""), sid, tg.get("repo"), af.get("commit"),
                         af.get("files") or tg.get("files") or [], pid)
            uf = p.get("user_followup") or {}
            self.c["follow"][uf.get("type")] += 1
            if uf.get("type") not in FOLLOW:
                self.bad.append((pid, "user_followup.type", str(uf.get("type"))))
            ev = uf.get("evidence") or ""
            ref = uf.get("turn") or ""
            if ev:
                fsid, fturn, ft = self.dlg.resolve(sid, ref)
                if ref and ft is None:
                    self.bad.append((pid, "user_followup.turn", "%s が対話に無い" % ref))
                d = self.dlg.get(fsid)
                hay = (C.norm(ft["user"]), C.loose(ft["user"])) if ft else ((d[1], d[2]) if d else ("", ""))
                res = C.find_in(hay[0], hay[1], ev)
                self.count("evidence", res)
                if res[2]:
                    self.bad.append((pid, "evidence", "%s に無い: %s" % (ref or "対話", res[2][0][:60])))
                if ft and t is not None and ft["ts"] and t["ts"] and ft["ts"] < t["ts"]:
                    self.bad.append((pid, "user_followup.turn", "%s は指摘より前の発言" % ref))
            elif uf.get("type") not in ("unknown", None):
                self.bad.append((pid, "evidence", "type=%s なのに evidence が空" % uf.get("type")))

    def scene(self, r):
        rid = r.get("id", "?")
        t = self.base(r, SCENE_KEYS, "scene")
        sid = (r.get("session") or "")[:8]
        self.c["kind"][r.get("kind")] += 1
        if r.get("kind") not in KINDS:
            self.bad.append((rid, "kind", str(r.get("kind"))))
        if r.get("from_response_alone") not in ALONE:
            self.bad.append((rid, "from_response_alone", str(r.get("from_response_alone"))))
        if r.get("confidence") not in CONF:
            self.bad.append((rid, "confidence", str(r.get("confidence"))))
        if t is not None:
            res = C.find_in(C.norm(t["user"]), C.loose(t["user"]), r.get("critique", ""))
            self.count("critique", res)
            if res[2] or not C.norm(r.get("critique")):
                self.bad.append((rid, "critique", "その発言に無い: " + (res[2][0][:60] if res[2] else "（空）")))
        ar = r.get("ai_response") or {}
        asid = (ar.get("session") or sid)[:8]
        if not C.pieces(ar.get("excerpt")):
            self.bad.append((rid, "ai_response.excerpt", "空"))
        else:
            at = self.dlg.turn(asid, ar.get("turn"))
            d = self.dlg.get(asid)
            if at is None:
                self.bad.append((rid, "ai_response.turn", "%s %s が対話に無い" % (asid, ar.get("turn"))))
            res = C.find_in(C.norm(at["prev"]), C.loose(at["prev"]), ar.get("excerpt")) if at else (False, False, ["-"])
            self.count("excerpt", res)
            if res[2]:
                whole = C.find_in(d[1], d[2], ar.get("excerpt")) if d else (False, False, ["-"])
                if whole[2]:
                    self.bad.append((rid, "ai_response.excerpt", "対話に無い: " + whole[2][0][:60]))
                else:
                    self.warn.append((rid, "ai_response.excerpt", "%s の直前の応答ではなく、対話の別の箇所にある"
                                      % ar.get("turn")))
        pis = r.get("prior_instructions")
        if not isinstance(pis, list):
            self.bad.append((rid, "prior_instructions", "配列にする"))
            pis = []
        for pi in pis:
            psid, pturn, pt = self.dlg.resolve((pi.get("session") or sid)[:8], pi.get("turn"))
            d = self.dlg.get(psid)
            hay = (C.norm(pt["user"]), C.loose(pt["user"])) if pt else ((d[1], d[2]) if d else ("", ""))
            res = C.find_in(hay[0], hay[1], pi.get("quote"))
            if res[2]:
                self.bad.append((rid, "prior_instructions", "%s %s に無い: %s" % (psid, pturn, res[2][0][:40])))

    def report(self, n_rev, n_sc):
        print("記録 %d 件（原稿 %d、進め方 %d）、原稿の指摘 %d 件" % (n_rev + n_sc, n_rev, n_sc, self.n_points))
        for k, (a, lo, b) in self.n.items():
            if b:
                print("%s: 一致 %d/%d%s" % (k, a, b, ("、書式の記号を除けば一致 %d" % lo) if lo else ""))
        for k, label in (("target_text", "target_text"), ("after", "after"), ("scale", "scale"),
                         ("follow", "user_followup.type"), ("era", "era"), ("kind", "kind")):
            if self.c[k]:
                print("%s: %s" % (label, "、".join("%s %d" % kv for kv in self.c[k].most_common())))
        if self.warn:
            print("注意 %d 件" % len(self.warn))
            for x in self.warn[:40]:
                print("  ", x)
        print("問題 %d 件" % len(self.bad))
        for x in self.bad[:80]:
            print("  ", x)


def NLJ(xs):
    return "\n".join(xs)


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round", required=True)
    C.add_place_args(ap)
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    _, _, pdir = C.place(a)
    ck = Checker(pdir, a.round)
    files = a.files or sorted(glob.glob(os.path.join(ck.rdir, "records", "*.jsonl")))
    if not files:
        print("記録が無い: %s" % os.path.join(ck.rdir, "records"))
        sys.exit(1)
    rows = []
    for f in files:
        rows += C.read_jsonl(f, ck.bad)
    ids = Counter(r.get("id") for r in rows)
    for x, n in ids.items():
        if n > 1:
            ck.bad.append((x, "id", "重複している（%d 件）" % n))
    n_rev = n_sc = 0
    for r in rows:
        rid = str(r.get("id", ""))
        if rid.startswith("review-"):
            n_rev += 1
            ck.review(r)
        elif rid.startswith("scene-"):
            n_sc += 1
            ck.scene(r)
        else:
            ck.bad.append((rid, "id", "review- か scene- で始める"))
    ck.report(n_rev, n_sc)
    sys.exit(1 if ck.bad else 0)


if __name__ == "__main__":
    main()
