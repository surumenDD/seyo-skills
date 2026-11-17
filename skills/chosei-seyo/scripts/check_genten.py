"""図鑑（genten.md）の案を照合し、通れば設置する。図鑑は登録の担当が書き、このスクリプトは中身を作らない。

図鑑の形（references/data.md の 8 節）
- 第 2 階層の見出しは文書の種類（候補の doc_kind の値）、第 3 階層の見出しは主題（短い名詞句。分類は担当の整理）。
- 見出しの下に、控え（entries.jsonl）の項目の user_words を 1 つずつ引用（> ）で置き、引用の直後の行に
  その項目の id を <!-- <id> --> の形で書く。当てはめすぎを防ぐのに要る場合だけ、その次の行に 1 行の注（（注: …））を置く。
- 既にある引用、id、注は、変更も削除もしない。足すだけである。

確認すること
- 引用がすべて、直後のコメントの id が指す控えの項目の user_words のどれかと一字も違わないこと（行末の空白は問わない）。
- id が控えにあること。引用の直後に id があること。引用が文書の種類と主題の見出しの下にあり、種類が項目の doc_kind と同じこと。
- 前の版（いまの genten.md）にあった引用、id、注が、同じ見出しの下に残っていること。
- この回の候補が、登録されたか、外した一覧（excluded.jsonl）にあるかのどちらか一方であること。外した理由が 4 つのどれかであること。
  足した引用の id がこの回の候補であること。
- 注は、項目の limits が空でないときだけ置けること。主題の見出しが規則の形の文でないこと。読み方の決まった文面があること。

使い方: python check_genten.py --round <回> --start [--force]
            回のフォルダに案（genten.draft.md）を、いまの図鑑の写し（無ければ読み方だけのひな形）で作る
        python check_genten.py --round <回> [--install]
            案と外した一覧を照合する。--install は、問題が 0 件なら案を genten.md として置き、前の版を回のフォルダに残し、
            登録した件、外した件、この回の共通に移す候補を表示する。--install を付けない照合は、問題が 0 件なら
            登録する件と外す件を案として示す。--install は、利用者が案を了承してから実行する
        python check_genten.py --round <回> --include <id> [<id> ...] --heading <主題>
            外した一覧の候補を名指しで登録する。写しを機械的に主題の見出しの下に置き、照合して設置する
        python check_genten.py --pending          登録を待つ回（控えに足したが図鑑に登録していない回）を並べる
        python check_genten.py --common           共通の図鑑を、共通の控えと照合する
共通: --home <フォルダ>、--project <キー>、--cwd <作業フォルダ>
"""
import argparse
import os
import re
import sys
from collections import Counter

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402

READING = [
    "読み方",
    "",
    "- 利用者が AI の文章と進め方に出した指摘の原文を、文書の種類（第 2 階層の見出し）と主題（第 3 階層の見出し）の下に、"
    "改変せずに引いた。書く文書の種類の節と「進め方」の節を読む。",
    "- 引用は、その位置で何が問題だったかの例であり、規則ではない。引用のあとに注があれば、その境界と経過を合わせて読み、"
    "境界の外へ当てはめない。",
    "- 見出しの分類は AI の整理であり、利用者の言葉ではない。同じ主題の下に引用が並ぶのは、利用者が指摘を繰り返したからである。",
    "- 引用の直後のコメントは、控え（entries.jsonl）の項目の id である。引用と注は足すだけで、書き換えない。",
]
COMMON_LINE = "- この図鑑は共通の置き場で、利用者がプロジェクトをまたいで使うと指示した引用だけが入る。種類の節を問わず全体を読む。"
REASONS = ("返事だけの発言", "その文書だけの決定", "取り下げられた指摘", "既に同じ原文が図鑑にある")
CMT = re.compile(r"^<!--\s*(\S+?)\s*-->$")
NOTE = re.compile(r"^（注[:：].+）$")
RULE_END = re.compile(r"(こと|する|しない|すべき|べきだ|ない|せよ|。)$")
MAX_HEADING = 40
DRAFT, EXCLUDED = "genten.draft.md", "excluded.jsonl"


def skeleton(label, common=False):
    return "# 原文の図鑑（%s）\n\n%s\n" % (label, "\n".join(READING + ([COMMON_LINE] if common else [])))


def canon(s):
    lines = (s or "").replace("\r\n", "\n").split("\n")
    return "\n".join(x.rstrip() for x in lines).strip("\n")


def parse(text):
    """{"blocks": [...], "heads": [...], "bad": [...]}。

    block: kind、heading、id、quote、note、start と end（行の番号。0 始まり、end は含まない）。
    head: (階層, 名前, 行の番号, 属する種類)。
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    blocks, heads, bad = [], [], []
    kind = heading = None
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if ln.startswith("## "):
            kind, heading = ln[3:].strip(), None
            heads.append((2, kind, i, kind))
        elif ln.startswith("### "):
            heading = ln[4:].strip()
            heads.append((3, heading, i, kind))
        elif ln.startswith(">"):
            j = i
            while j < n and lines[j].startswith(">"):
                j += 1
            quote = canon("\n".join(x[2:] if x.startswith("> ") else x[1:] for x in lines[i:j]))
            m = CMT.match(lines[j].strip()) if j < n else None
            bid, note = None, ""
            if m:
                bid = m.group(1)
                j += 1
                if j < n and NOTE.match(lines[j].strip()):
                    note = lines[j].strip()
                    j += 1
            else:
                bad.append("%d 行目: 引用の直後に id のコメント（<!-- <id> -->）が無い" % (i + 1))
            if kind is None or heading is None:
                bad.append("%d 行目: 引用が、文書の種類（##）と主題（###）の見出しの下に無い" % (i + 1))
            blocks.append({"kind": kind, "heading": heading, "id": bid, "quote": quote, "note": note, "start": i, "end": j})
            i = j
            continue
        elif CMT.match(ln.strip()):
            bad.append("%d 行目: 引用の無い id のコメント" % (i + 1))
        elif NOTE.match(ln.strip()):
            bad.append("%d 行目: 注が、引用と id のコメントの直後に無い" % (i + 1))
        i += 1
    return {"blocks": blocks, "heads": heads, "bad": bad, "lines": lines}


def key_of(b):
    return (b["kind"], b["heading"], b["id"], b["quote"], b["note"])


def ids_in(text):
    return {b["id"] for b in parse(text)["blocks"] if b["id"]}


def check(draft, prev, entries, round_name=None, round_ids=None, excluded=None):
    """問題の一覧と、足した引用の一覧を返す。entries は {id: 控えの項目}。"""
    P = parse(draft)
    bad = list(P["bad"])
    body = draft.replace("\r\n", "\n").split("\n")
    for ln in READING:
        if ln and ln not in body:
            bad.append("読み方の決まった文面が無い: %s…" % ln[:24])
    seen2, seen3 = Counter(), Counter()
    for lvl, name, i, kind in P["heads"]:
        if lvl == 2:
            seen2[name] += 1
            if name not in C.KINDS:
                bad.append("%d 行目: 第 2 階層の見出しは文書の種類（%s）にする: %s" % (i + 1, "、".join(C.KINDS), name))
        else:
            seen3[(kind, name)] += 1
            if kind is None:
                bad.append("%d 行目: 主題の見出しが、文書の種類の見出しの下に無い: %s" % (i + 1, name))
            if not name or len(name) > MAX_HEADING or RULE_END.search(name):
                bad.append("%d 行目: 主題の見出しは %d 字以内の名詞句にし、規則や文の形にしない: %s" % (i + 1, MAX_HEADING, name))
    bad += ["同じ種類の見出しが 2 つある: %s" % k for k, v in seen2.items() if v > 1]
    bad += ["同じ主題の見出しが 2 つある: %s / %s" % k for k, v in seen3.items() if v > 1]
    for b in P["blocks"]:
        if not b["id"]:
            continue
        e = entries.get(b["id"])
        where = "%d 行目（%s）" % (b["start"] + 1, b["id"])
        if e is None:
            bad.append("%s: id が控えに無い" % where)
            continue
        if b["quote"] not in [canon(w) for w in e.get("user_words") or []]:
            bad.append("%s: 引用が、控えの項目の写しのどれとも一字以上違う: %s…" % (where, b["quote"][:30]))
        if b["kind"] and b["kind"] != e.get("doc_kind"):
            bad.append("%s: 種類の見出し %s が、項目の doc_kind %s と違う" % (where, b["kind"], e.get("doc_kind")))
        if b["note"] and not (e.get("limits") or "").strip():
            bad.append("%s: 項目の limits が空なのに注がある（注に書けるのは limits の境界と経過だけ）" % where)
    before = Counter(key_of(b) for b in parse(prev)["blocks"]) if prev else Counter()
    now = Counter(key_of(b) for b in P["blocks"])
    for k, v in (before - now).items():
        bad.append("前の版の引用、id、注が、変わったか消えた: %s（%s / %s）「%s…」" % (k[2], k[0], k[1], (k[3] or "")[:20]))
    added = [b for b in P["blocks"] if b["id"]]
    left = Counter(before)
    fresh = []
    for b in added:
        if left[key_of(b)] > 0:
            left[key_of(b)] -= 1
        else:
            fresh.append(b)
    if round_ids is not None:
        reg = {b["id"] for b in P["blocks"] if b["id"]}
        exc = {}
        for r in excluded or []:
            rid = r.get("id")
            if rid in exc:
                bad.append("外した一覧に同じ id が 2 度ある: %s" % rid)
            exc[rid] = r
            if rid not in round_ids:
                bad.append("外した一覧の id が、この回の候補に無い: %s" % rid)
            if r.get("reason") not in REASONS:
                bad.append("外した理由は %s のどれか: %s %r" % ("、".join(REASONS), rid, r.get("reason")))
        for rid in sorted(round_ids):
            if rid not in reg and rid not in exc:
                bad.append("この回の候補が、登録にも外した一覧にも無い: %s" % rid)
            if rid in reg and rid in exc:
                bad.append("この回の候補が、登録と外した一覧の両方にある: %s" % rid)
        for b in fresh:
            if b["id"] not in round_ids:
                bad.append("%d 行目: この回の候補でない id の引用が足されている: %s" % (b["start"] + 1, b["id"]))
    return bad, fresh


def block_lines(quote, bid, note=""):
    out = ["> " + x if x.strip() else ">" for x in quote.split("\n")]
    out.append("<!-- %s -->" % bid)
    if note:
        out.append(note)
    return out


def tidy(lines):
    out = []
    for ln in lines:
        if ln.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(ln)
    return "\n".join(out).strip("\n") + "\n"


def add_blocks(text, items):
    """items: [(種類, 主題, 引用, id, 注)]。見出しが無ければ作り、その見出しの末尾に足す。機械的な写しにだけ使う。"""
    for kind, heading, quote, bid, note in items:
        P = parse(text)
        lines = P["lines"]
        heads = P["heads"]
        k2 = [h for h in heads if h[0] == 2]
        new = block_lines(quote, bid, note)
        sec = next((h for h in k2 if h[1] == kind), None)
        if sec is None:
            later = [h for h in k2 if h[1] in C.KINDS and kind in C.KINDS and C.KINDS.index(h[1]) > C.KINDS.index(kind)]
            at = later[0][2] if later else len(lines)
            lines = lines[:at] + ["", "## " + kind, "", "### " + heading, ""] + new + [""] + lines[at:]
        else:
            nxt = [h[2] for h in k2 if h[2] > sec[2]]
            end = nxt[0] if nxt else len(lines)
            sub = next((h for h in heads if h[0] == 3 and h[3] == kind and h[1] == heading and sec[2] < h[2] < end), None)
            if sub is None:
                at = end
                while at > sec[2] + 1 and lines[at - 1].strip() == "":
                    at -= 1
                lines = lines[:at] + ["", "### " + heading, ""] + new + [""] + lines[at:]
            else:
                nxt3 = [h[2] for h in heads if sub[2] < h[2] < end]
                at = nxt3[0] if nxt3 else end
                while at > sub[2] + 1 and lines[at - 1].strip() == "":
                    at -= 1
                lines = lines[:at] + [""] + new + [""] + lines[at:]
        text = tidy(lines)
    return text


def remove_ids(text, ids):
    """id の引用と注を外し、空になった見出しを外す。取り消しと、共通から外すときにだけ使う。"""
    P = parse(text)
    drop = set()
    for b in P["blocks"]:
        if b["id"] in ids:
            drop |= set(range(b["start"], b["end"]))
    lines = [x for i, x in enumerate(P["lines"]) if i not in drop]
    changed = True
    while changed:
        changed = False
        for lvl in (3, 2):
            for i, x in enumerate(lines):
                if not x.startswith("#" * lvl + " "):
                    continue
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                stop = j >= len(lines) or lines[j].startswith("## ") or (lvl == 3 and lines[j].startswith("### "))
                if stop:
                    del lines[i:j]
                    changed = True
                    break
            if changed:
                break
    return tidy(lines), len([b for b in P["blocks"] if b["id"] in ids])


def next_free(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.exists("%s.%d%s" % (base, k, ext)):
        k += 1
    return "%s.%d%s" % (base, k, ext)


def round_ids(pdir, rnd):
    return {e["id"] for e in C.load_entries(pdir) if e.get("round") == rnd}


def read_excluded(rdir):
    p = os.path.join(rdir, EXCLUDED)
    if not os.path.isfile(p):
        return []
    bad = []
    rows = C.read_jsonl(p, bad)
    if bad:
        raise SystemExit("外した一覧を読めない: " + bad[0])
    return rows


def first_line(s, n=40):
    t = (s or "").strip().split("\n")[0]
    return t if len(t) <= n else t[:n] + "…"


def set_ledger(pdir, rnd, **kw):
    ledger = C.load_ledger(pdir)
    for r in ledger.get("rounds", []):
        if r.get("name") == rnd:
            r.update(kw)
    C.write_json(C.ledger_path(pdir), ledger)


def pending(pdir):
    return [r for r in C.load_ledger(pdir).get("rounds", []) if r.get("genten") == "未登録"]


def print_items(entries, fresh, exc, rnd, proposal):
    """登録する件（した件）と外す件（した件）を表示する。proposal なら案として示す。"""
    if fresh:
        print("登録する件（案）:" if proposal else "登録した件:")
        for b in fresh:
            print("- %s / %s: 「%s」（%s）" % (b["kind"], b["heading"], first_line(b["quote"]), b["id"]))
    if exc:
        if proposal:
            print("外す件（案）:")
        else:
            print("外した件（名指しで登録するなら: python scripts/check_genten.py --round %s --include <id> --heading <主題>）:" % rnd)
        for r in exc:
            e = entries.get(r.get("id")) or {}
            print("- %s（%s）: 「%s」" % (r.get("id"), r.get("reason"), first_line((e.get("user_words") or [""])[0])))


def install(home, key, pdir, rnd, draft, exc, label):
    """照合して置く。問題があれば表示して 1 を返す。"""
    entries = {e["id"]: e for e in C.load_entries(pdir)}
    gp = C.genten_path(pdir)
    prev = C.read_text(gp) if os.path.isfile(gp) else ""
    rids = round_ids(pdir, rnd)
    bad, fresh = check(draft, prev, entries, rnd, rids, exc)
    if bad:
        print("図鑑の照合で問題 %d 件。設置しない" % len(bad))
        for x in bad[:60]:
            print("  ", x)
        return 1
    rdir = C.round_dir(pdir, rnd)
    before = None
    if prev:
        before = next_free(os.path.join(rdir, "genten.before.md"))
        C.write_text(before, prev)
    C.write_text(gp, draft)
    set_ledger(pdir, rnd, genten="登録済み", registered=len({b["id"] for b in parse(draft)["blocks"] if b["id"] in rids}),
               excluded=len(exc))
    print("図鑑を置いた: %s（%d 字。前の版: %s）。%s: 足した引用 %d、外した候補 %d" % (
        gp, len(draft), before or "なし", label, len(fresh), len(exc)))
    print_items(entries, fresh, exc, rnd, proposal=False)
    import common_entries
    cands, n_skip = common_entries.find_candidates(home, key, rnd)
    print("\n".join(common_entries.format_candidates(cands, key, n_skip)))
    return 0


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_place_args(ap)
    ap.add_argument("--round")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--include", nargs="+")
    ap.add_argument("--heading")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--common", action="store_true")
    a = ap.parse_args()
    if a.common:
        home = C.seyo_home(a.home)
        cd = C.common_dir(home)
        gp = C.genten_path(cd)
        entries = {e["id"]: e for e in C.load_entries(cd)}
        text = C.read_text(gp) if os.path.isfile(gp) else ""
        bad = check(text, "", entries)[0] if text else []
        bad += ["共通の控えの項目が、共通の図鑑に無い: %s" % x for x in sorted(set(entries) - ids_in(text))]
        print("共通の図鑑: 問題 %d 件" % len(bad))
        for x in bad[:60]:
            print("  ", x)
        sys.exit(1 if bad else 0)
    home, key, pdir = C.place(a)
    if a.pending:
        ps = pending(pdir)
        print("登録を待つ回 %d（プロジェクト %s）%s" % (len(ps), key, "".join(
            "\n- %s（%s、項目 %d）" % (r["name"], r.get("kind", ""), r.get("entries", 0)) for r in ps)))
        return
    if not a.round:
        ap.error("--round、--pending、--common のどれかを指定する")
    rdir = C.round_dir(pdir, a.round)
    dp = os.path.join(rdir, DRAFT)
    gp = C.genten_path(pdir)
    cur = C.read_text(gp) if os.path.isfile(gp) else skeleton("プロジェクト " + key)
    if not round_ids(pdir, a.round):
        print("回 %s の項目が控えに無い（先に install_round.py --round %s か migrate_legacy.py で控えに足す）" % (a.round, a.round))
        sys.exit(1)
    if a.start:
        if os.path.isfile(dp) and C.read_text(dp) != cur and not a.force:
            print("案が既にあり、いまの図鑑と違う。作り直すなら --force を付ける: %s" % dp)
            sys.exit(1)
        C.write_text(dp, cur)
        print("案を作った: %s（いまの図鑑の写し）。登録の担当は、この案に足し、外した候補を %s に書く"
              % (dp, os.path.join(rdir, EXCLUDED)))
        return
    exc = read_excluded(rdir)
    if a.include:
        if not a.heading:
            ap.error("--include には --heading <主題> が要る")
        entries = {e["id"]: e for e in C.load_entries(pdir)}
        ex_ids = {r.get("id") for r in exc}
        miss = [i for i in a.include if i not in ex_ids]
        if miss:
            print("外した一覧に無い id: %s（何もしていない）" % "、".join(miss))
            sys.exit(1)
        items = [(entries[i]["doc_kind"], a.heading.strip(), canon(w), i, "")
                 for i in a.include for w in entries[i].get("user_words") or []]
        draft = add_blocks(cur, items)
        rest = [r for r in exc if r.get("id") not in a.include]
        code = install(home, key, pdir, a.round, draft, rest, "名指しの登録")
        if code == 0:
            C.write_jsonl(os.path.join(rdir, EXCLUDED), rest)
        sys.exit(code)
    if not os.path.isfile(dp):
        print("案が無い: %s（先に --start で作る）" % dp)
        sys.exit(1)
    draft = C.read_text(dp)
    if a.install:
        sys.exit(install(home, key, pdir, a.round, draft, exc, "回 " + a.round))
    entries = {e["id"]: e for e in C.load_entries(pdir)}
    prev = C.read_text(gp) if os.path.isfile(gp) else ""
    bad, fresh = check(draft, prev, entries, a.round, round_ids(pdir, a.round), exc)
    print("案 %s: 足した引用 %d、外した候補 %d、この回の候補 %d" % (dp, len(fresh), len(exc), len(round_ids(pdir, a.round))))
    print("問題 %d 件" % len(bad))
    for x in bad[:60]:
        print("  ", x)
    if not bad:
        print_items(entries, fresh, exc, a.round, proposal=True)
        print("利用者の了承を得てから、--install で設置する。")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
