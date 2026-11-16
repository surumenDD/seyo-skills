"""判断の候補（chosei/rounds/<名前>/cand_NN.jsonl）を、候補の担当が読んだ入力と照合する。

確認すること（形式は references/data.md の 6 節）
- 欄と値。id が「c<入力の番号>-<連番>」の形か（回の中で一意にするため）。
- user_words が 1〜4 個で、1 つ 300 字以内か。入力のうち利用者の発言の部分（発言の全文、反応、それより前の指示）に
  一字一句あるか。AI の文や、記録の担当が書いた説明の欄にしか無い文は、利用者の語として通さない。
- doc_kind が、層の種類の名前（論文、発表、報告、査読への回答、メール、AI 向けの文書、付属文書、一般の読み手への説明）か、
  進め方か、その他か。
- general が真なら、general_words（プロジェクトに限らない言い方と読める部分の写し）が user_words の 1 つの中にあるか。
  general が偽なら general_words は空か。
- evidence の id（review-…#p1 の形は指摘の番号まで）が、入力にあるか。
- judgment が規則の文（〜すること、〜してはならない）で終わっていないか。
最後に、どの候補の根拠にも使われなかった記録の id を並べる。落ちた判断が無いかを読むときの手がかりにする。

使い方: python check_candidates.py --round <名前> [--home <フォルダ>] [--project <キー>] [番号 ...]
        番号を省くと、入力（in_NN.md）のすべてを照合する。
        python check_candidates.py --round <名前> --from-dialogs
        作業の途中の取り込み（記録の取り出しを省き、範囲の対話から直接に候補を取り出した場合）。
        cand_01.jsonl を、回の範囲の対話と照合する。evidence は「<ID8> <発言の番号>」の形で書く。
"""
import argparse
import glob
import os
import re
import sys

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402

STRENGTH = {"強い", "ふつう", "弱い"}
KEYS = ("id", "judgment", "user_words", "ai_pattern", "applies_to", "doc_kind", "general", "general_words",
        "evidence", "n_utterances", "limits", "strength")
MAX_WORDS = 4
MAX_WORD_CHARS = 300
BLOCK = re.compile(r"^## (原稿|進め方) (\S+?)｜", re.M)
RULE_END = re.compile(r"(すること|してはならない|しないこと|すべきである|べきだ)。?$")


def parse_input(text):
    """{記録の id: (指摘の番号の集合, 利用者の発言の部分)}"""
    out = {}
    heads = list(BLOCK.finditer(text))
    for i, m in enumerate(heads):
        body = text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        lines = body.split("\n")
        user, pts = [], set()
        if m.group(1) == "原稿":
            on = False
            for ln in lines:
                if ln.startswith("利用者の発言（全文）:"):
                    on = True
                    continue
                pm = re.match(r"- 指摘 p(\d+)（", ln)
                if pm:
                    on = False
                    pts.add(pm.group(1))
                    continue
                if ln.startswith("  - そのあとの利用者の反応:"):
                    user.append(ln.split(":", 1)[1])
                elif on:
                    user.append(ln)
        else:
            on = False
            for ln in lines:
                if ln.startswith("- それより前の利用者の指示:"):
                    user.append(ln.split(":", 1)[1])
                elif ln.startswith("- 利用者の発言:"):
                    on = True
                    user.append(ln.split(":", 1)[1])
                elif on:
                    user.append(ln)
        out[m.group(2)] = (pts, "\n".join(user))
    return out


def check_rows(rows, num, bad, warn, ev_ok, user_hay, all_hay):
    seen = set()
    for r in rows:
        rid = r.get("id")
        if rid in seen:
            bad.append("%s: id が重複" % rid)
        seen.add(rid)
        if not re.fullmatch(r"c%s-\d+" % num, str(rid)):
            bad.append("%s: id は「c%s-<連番>」の形にする" % (rid, num))
        for k in KEYS:
            if k not in r:
                bad.append("%s: 欄 %s が無い" % (rid, k))
        if r.get("strength") not in STRENGTH:
            bad.append("%s: strength の値 %r" % (rid, r.get("strength")))
        if "doc_kind" in r and r.get("doc_kind") not in C.KINDS:
            bad.append("%s: doc_kind の値 %r（%s のどれか）" % (rid, r.get("doc_kind"), "、".join(C.KINDS)))
        if not isinstance(r.get("n_utterances"), int) or r.get("n_utterances") < 1:
            bad.append("%s: n_utterances は 1 以上の整数" % rid)
        if not isinstance(r.get("limits", ""), str):
            bad.append("%s: limits は文字列（無ければ空）" % rid)
        uw = r.get("user_words") or []
        if not isinstance(uw, list) or not (1 <= len(uw) <= MAX_WORDS):
            bad.append("%s: user_words は 1〜%d 個の配列" % (rid, MAX_WORDS))
            uw = [w for w in uw if isinstance(w, str)] if isinstance(uw, list) else []
        for w in uw:
            if len(C.norm(w.replace("（中略）", ""))) > MAX_WORD_CHARS:
                bad.append("%s: user_words の 1 つが %d 字を超える（「（中略）」でつなぐか、分ける）: %s…"
                           % (rid, MAX_WORD_CHARS, w.strip()[:30]))
            for piece in re.split(r"（中略）", w):
                p = C.norm(piece)
                if len(p) <= 3 or p in user_hay:
                    continue
                if p in all_hay:
                    bad.append("%s: user_words が利用者の発言ではなく、AI の文か説明の欄にある: %s" % (rid, piece.strip()[:40]))
                else:
                    bad.append("%s: user_words が入力に無い: %s" % (rid, piece.strip()[:40]))
        gen, gw = r.get("general"), r.get("general_words")
        if "general" in r and not isinstance(gen, bool):
            bad.append("%s: general は true か false" % rid)
        if "general_words" in r and not isinstance(gw, str):
            bad.append("%s: general_words は文字列（general が false なら空）" % rid)
        elif gen is True:
            if not C.norm(gw):
                bad.append("%s: general が true なら、範囲を述べた部分を general_words に写す" % rid)
            elif not any(C.norm(gw) in C.norm(w) for w in uw):
                bad.append("%s: general_words が user_words のどれの中にも無い: %s" % (rid, gw.strip()[:40]))
        elif gen is False and C.norm(gw):
            bad.append("%s: general が false なら general_words は空にする" % rid)
        ev = r.get("evidence") or []
        if not ev:
            bad.append("%s: evidence が空" % rid)
        for e in ev:
            msg = ev_ok(e)
            if msg:
                bad.append("%s: %s" % (rid, msg))
        if RULE_END.search((r.get("judgment") or "").strip()):
            bad.append("%s: judgment が規則の形で終わっている" % rid)


def is_direct(pdir, rnd):
    """入力（in_NN.md）の無い回は、対話から直接に候補を取り出した回とみなす。"""
    return not glob.glob(os.path.join(C.round_dir(pdir, rnd), "in_*.md"))


def check(pdir, rnd, nums=None, from_dialogs=False):
    """回の候補を照合する。{"bad", "warn", "rows", "lines"} を返す。lines は表示する要約の行。"""
    rdir = C.round_dir(pdir, rnd)
    bad, warn, cited, stats, lines, all_rows = [], [], set(), {}, [], []
    if from_dialogs:
        scope = C.load_scope(pdir, rnd)
        idx = C.scope_index(scope)
        tz = C.tz_of(scope.get("tz")) if scope.get("tz") else None
        dlg = C.Dialogs(C.dialogs_dir(pdir), tz)
        user_parts, all_parts, turns_ok = [], [], set()
        for s in scope.get("sessions", []):
            d = dlg.get(s["sid8"])
            if d is None:
                bad.append("対話が無い: %s" % s["sid8"])
                continue
            for num, t in d[3].items():
                if C.in_scope(idx, s["sid8"], t["ts"]) and not t["dup"] and num[0] != "E":
                    turns_ok.add("%s %s" % (s["sid8"], num))
                    user_parts.append(t["user"])
                    all_parts += [t["user"], t["prev"]]
        user_hay, all_hay = C.norm("\n".join(user_parts)), C.norm("\n".join(all_parts))

        def ev_ok(e):
            e2 = re.sub(r"\s+", " ", (e or "").strip())
            cited.add(e2)
            return "" if e2 in turns_ok else "evidence の発言が回の範囲に無い（「<ID8> <番号>」の形）: %s" % e
        p = os.path.join(rdir, "cand_01.jsonl")
        if not os.path.isfile(p):
            bad.append("候補が無い: %s" % p)
            return {"bad": bad, "warn": warn, "rows": [], "lines": lines}
        rows = C.read_jsonl(p, bad)
        check_rows(rows, "01", bad, warn, ev_ok, user_hay, all_hay)
        all_rows += rows
        for r in rows:
            stats[r.get("strength")] = stats.get(r.get("strength"), 0) + 1
        lines.append("範囲の発言 %d、候補 %d（%s）、根拠に使われた発言 %d" % (
            len(turns_ok), len(rows), "、".join("%s %d" % kv for kv in stats.items()), len(cited & turns_ok)))
    else:
        nums = ["%02d" % int(n) for n in (nums or [])] or sorted(
            re.search(r"in_(\d+)\.md$", p).group(1) for p in glob.glob(os.path.join(rdir, "in_*.md")))
        if not nums:
            bad.append("入力が無い: %s" % rdir)
            return {"bad": bad, "warn": warn, "rows": [], "lines": lines}
        all_ids = set()
        for num in nums:
            src = C.read_text(os.path.join(rdir, "in_%s.md" % num))
            blocks = parse_input(src)
            all_ids |= set(blocks)
            user_hay = C.norm("\n".join(v[1] for v in blocks.values()))
            all_hay = C.norm(src)

            def ev_ok(e, blocks=blocks, num=num):
                m = re.fullmatch(r"(\S+?)(?:#p(\d+))?", (e or "").strip())
                base, pt = (m.group(1), m.group(2)) if m else (e, None)
                if base not in blocks:
                    return "evidence の id が in_%s.md に無い: %s" % (num, e)
                cited.add(base)
                if pt and pt not in blocks[base][0]:
                    return "evidence の指摘の番号が無い: %s" % e
                return ""
            p = os.path.join(rdir, "cand_%s.jsonl" % num)
            if not os.path.isfile(p):
                bad.append("候補が無い: cand_%s.jsonl" % num)
                continue
            rows = C.read_jsonl(p, bad)
            all_rows += rows
            check_rows(rows, num, bad, warn, ev_ok, user_hay, all_hay)
            for r in rows:
                stats[r.get("strength")] = stats.get(r.get("strength"), 0) + 1
        lines.append("入力の記録 %d、候補 %d（%s）、候補の根拠に使われた記録 %d" % (
            len(all_ids), len(all_rows), "、".join("%s %d" % kv for kv in stats.items()), len(cited & all_ids)))
        unused = sorted(all_ids - cited)
        if unused:
            lines.append("どの候補の根拠にも使われなかった記録 %d: %s%s" % (
                len(unused), "、".join(unused[:30]), " ほか" if len(unused) > 30 else ""))
    kinds = {}
    for r in all_rows:
        kinds[r.get("doc_kind")] = kinds.get(r.get("doc_kind"), 0) + 1
    if all_rows:
        lines.append("文書の種類: %s。プロジェクトに限らない言い方の印 %d" % (
            "、".join("%s %d" % (k, kinds[k]) for k in C.KINDS if k in kinds),
            sum(1 for r in all_rows if r.get("general") is True)))
    return {"bad": bad, "warn": warn, "rows": all_rows, "lines": lines}


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--round", required=True)
    C.add_place_args(ap)
    ap.add_argument("--from-dialogs", action="store_true")
    ap.add_argument("nums", nargs="*")
    a = ap.parse_args()
    _, _, pdir = C.place(a)
    res = check(pdir, a.round, a.nums, a.from_dialogs)
    for ln in res["lines"]:
        print(ln)
    if res["warn"]:
        print("注意 %d 件" % len(res["warn"]))
        for x in res["warn"][:30]:
            print("  ", x)
    print("問題 %d 件" % len(res["bad"]))
    for x in res["bad"][:60]:
        print("  ", x)
    sys.exit(1 if res["bad"] else 0)


if __name__ == "__main__":
    main()
