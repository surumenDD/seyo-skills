"""回の記録から、候補の取り出しの入力（chosei/rounds/<名前>/in_NN.md）を作る。

入れるもの
- 原稿の記録: 読んでいたもの、利用者の発言の全文、指摘ごとの対象と規模、対象の本文の冒頭、
  そのあと AI がしたこと、直したあとの本文の冒頭、そのあとの利用者の反応
- 進め方の記録: それより前の利用者の指示、AI の応答の抜き出し、起きたこと、利用者の発言
入れないもの: 取り出した担当の注記（note）と確信度。候補の担当が、記録の担当の解釈を判断の材料にしないようにするためである。

記録を時刻の順に並べ、1 ファイルが --limit 字を超えないように分ける。
回の範囲（scope.json）の終わりより後の発言と、範囲を作るときに --until で区切った時点より後の発言（反応）は、
「（取り込みに使う範囲の外の発言なので省いた）」に置き換え、その反応を受けた直したあとの本文も入れない。
検証のために範囲を区切った場合に、採点に使う時期の利用者の判断が入力に入らないようにするためである。
範囲を区切っていなければ、省かれる発言は無い。

使い方: python build_input.py --round <名前> [--home <フォルダ>] [--project <キー>] [--limit 70000] [--force]
        候補（cand_*.jsonl）がすでにある回では、入力の番号がずれるので、--force が無ければ作り直さない。
"""
import argparse
import datetime
import glob
import os
import re
import sys

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402

OUTSIDE = "（取り込みに使う範囲の外の発言なので省いた）"


def one(s, n=None):
    s = (s or "").replace("\r", "").strip()
    return s if n is None or len(s) <= n else s[:n] + "…（略）"


def flat(s, n):
    return one(s, n).replace("\n", " ⏎ ")


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round", required=True)
    C.add_place_args(ap)
    ap.add_argument("--limit", type=int, default=70000)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    _, _, pdir = C.place(a)
    rdir = C.round_dir(pdir, a.round)
    scope = C.load_scope(pdir, a.round)
    if glob.glob(os.path.join(rdir, "cand_*.jsonl")) and not a.force:
        raise SystemExit("この回には候補がある。入力を作り直すなら --force を付ける（候補は作り直しになる）")
    tz = C.tz_of(scope.get("tz")) if scope.get("tz") else None
    until = C.parse_until(scope.get("until"))
    window = bool(scope.get("until") or scope.get("until_turn"))
    idx = C.scope_index(scope)
    dlg = C.Dialogs(C.dialogs_dir(pdir), tz)
    texts_dir = os.path.join(rdir, "records", "texts")
    hidden = [0]

    def visible(sid, ref):
        """反応の発言が、取り込みに使う範囲の中にあるか。"""
        if not (ref or "").strip():
            return True
        rsid, rturn, t = dlg.resolve(sid, ref)
        if t is None or t["ts"] is None:
            return not window
        if rsid in idx and idx[rsid][1] is not None and t["ts"] > idx[rsid][1]:
            return False
        return C.within_until(t["ts"], until, tz)

    def body_of(v):
        v = (v or "").strip()
        if "\n" not in v and re.fullmatch(r"texts[\\/][^\s]+\.md", v):
            p = os.path.join(texts_dir, os.path.basename(v))
            return C.read_text(p) if os.path.isfile(p) else ""
        return v

    bad = []
    rows = []
    for f in sorted(glob.glob(os.path.join(rdir, "records", "*.jsonl"))):
        rows += C.read_jsonl(f, bad)
    if bad:
        raise SystemExit("記録を読めない: " + bad[0])
    blocks = []
    n_rev = n_pt = n_sc = 0
    for o in rows:
        rid = str(o.get("id", ""))
        sid = (o.get("session") or "")[:8]
        t = dlg.turn(sid, o.get("turn"))
        ts = t["ts"] if t and t["ts"] else None
        key = (ts or datetime.datetime.max.replace(tzinfo=C.UTC), sid, 0 if rid.startswith("review-") else 1, rid)
        if rid.startswith("review-"):
            n_rev += 1
            b = ["## 原稿 %s｜%s %s %s｜%s" % (rid, sid, o.get("turn"), o.get("date"), o.get("era", "")),
                 "読んでいたもの: " + one((o.get("target") or {}).get("what", ""), 300),
                 "", "利用者の発言（全文）:", "", one(o.get("review_text", ""), 6000), ""]
            for p in o.get("points") or []:
                n_pt += 1
                fu = p.get("user_followup") or {}
                af = p.get("after") or {}
                b.append("- 指摘 p%s（%s）対象: %s" % (p.get("n"), p.get("scale", ""), one(p.get("where", ""), 200)))
                tt = body_of(p.get("target_text"))
                if C.norm(tt):
                    b.append("  - 対象の本文（冒頭）: " + flat(tt, 300))
                b.append("  - そのあと AI がしたこと: " + one(af.get("how", ""), 400))
                if visible(sid, fu.get("turn")):
                    at = body_of(af.get("text_or_path"))
                    if C.norm(at):
                        b.append("  - 直したあとの本文（冒頭）: " + flat(at, 300))
                    b.append("  - そのあとの利用者の反応: %s %s「%s」" % (fu.get("type", ""), fu.get("turn", ""),
                                                             one(fu.get("evidence", ""), 300)))
                else:
                    hidden[0] += 1
                    b.append("  - そのあとの利用者の反応: " + OUTSIDE)
            b.append("")
        elif rid.startswith("scene-"):
            n_sc += 1
            ar = o.get("ai_response") or {}
            pis = o.get("prior_instructions") or []
            b = ["## 進め方 %s｜%s %s %s｜%s" % (rid, sid, o.get("turn"), o.get("date"), o.get("kind")),
                 "- それより前の利用者の指示: " + ("／".join("「%s」" % one(x.get("quote", ""), 300) for x in pis)
                                         if pis else "なし"),
                 "- AI の応答（抜き出し）: " + flat(ar.get("excerpt", ""), 800),
                 "- 起きたこと: " + one(o.get("what_happened", ""), 400),
                 "- 利用者の発言: " + one(o.get("critique", ""), 1500), ""]
        else:
            continue
        blocks.append((key, rid, "\n".join(b)))
    blocks.sort(key=lambda x: x[0])
    chunks, cur, size = [], [], 0
    for key, rid, text in blocks:
        if cur and size + len(text) > a.limit:
            chunks.append(cur)
            cur, size = [], 0
        cur.append((rid, text))
        size += len(text)
    if cur:
        chunks.append(cur)
    for old in glob.glob(os.path.join(rdir, "in_*.md")):
        os.remove(old)
    files = {}
    for i, ch in enumerate(chunks, 1):
        name = "in_%02d.md" % i
        text = "\n".join(x[1] for x in ch)
        C.write_text(os.path.join(rdir, name), text)
        files[name] = {"chars": len(text), "records": [x[0] for x in ch]}
    C.write_json(os.path.join(rdir, "manifest.json"), {
        "round": a.round, "created_at": C.ts_str(datetime.datetime.now(C.UTC)), "limit": a.limit,
        "records": {"review": n_rev, "points": n_pt, "scene": n_sc}, "hidden_reactions": hidden[0], "files": files})
    print("原稿 %d（指摘 %d）、進め方 %d。%d ファイル、合計 %d 字。範囲の外として省いた反応 %d → %s"
          % (n_rev, n_pt, n_sc, len(chunks), sum(v["chars"] for v in files.values()), hidden[0], rdir))


if __name__ == "__main__":
    main()
