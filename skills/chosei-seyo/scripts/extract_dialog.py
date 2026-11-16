"""Claude Code のセッションの記録（jsonl）から、利用者の発言と、その直前の AI の応答の本文を抜き出す。
ツールの入出力、思考、システムの通知、スキルの本文は含めない。

発言の種類: U は通常の発言、Q は AI の作業中に届いた発言（記録では queue-operation の enqueue）、
B は /btw で届いた発言（記録では system の local_command。引数に利用者の言葉が入る）、
E は最後の発言のあとに残った応答（利用者の発言は無い）。U と Q の番号は、B と E を加えても変わらない。
Q と同じ文が、そのあと U として届いた場合は、Q を出さない。ただし、作業の途中で受け取られた印
（queued_command の添付か、absorbed_mid_turn の remove）が先にある Q と、Q より前にしか同じ文の U が無い Q は残す。
あとから同じ文の U が加わっても、それより前の Q が消えて番号がずれないようにするためである。

見出しは「## U001（2026-05-20T01:05:00｜2026-05-20）」の形で、括弧の中は時刻（UTC）と現地の日付。
別のセッションに同じ時刻と本文の発言があれば、見出しの末尾に「（重複: <ID8> <番号> と同じ発言）」と書く。
ほかの形式（Codex の記録、貼り付けた対話）は、この形の md に直せば、--round に md のまま渡せる。

使い方:
  python extract_dialog.py --list <jsonl かフォルダ ...>
      セッションごとに、最初と最後の時刻、U・Q・B の数、取り込み済みの範囲を表示する
  python extract_dialog.py --round <名前> [--since <日付か時刻>] [--until <日付か時刻>]
                           [--until-turn <ID8>:<番号>[,...]] [--exam] [--from-start]
                           [--part-chars 100000] [--force] <jsonl か md ...>
      対話を SEYO_HOME/projects/<キー>/chosei/dialogs/ に書き、回の範囲（chosei/rounds/<名前>/scope.json）を作る。
      範囲の始まりは、このプロジェクトで取り込み済みの最後の発言の次（--from-start で最初から）と --since の遅いほう。
      記録が 10 分以内に更新されたセッションは進行中の会話とみなし、範囲を最後の U までにする。
      そのあとの Q と B は、まだ U として届くか決まっていないので、次の回に回す。
      --exam は、検証の採点に使う試験の回にする（install_round.py が設置を断る）
  python extract_dialog.py --out <フォルダ> <jsonl ...>
      対話を <フォルダ>/<セッション ID>.md に書くだけにする
共通: --home <フォルダ>（既定は環境変数 SEYO_HOME、無ければ ~/.seyo）、--project <キー>、--cwd <作業フォルダ>
      （プロジェクトの決め方は references/data.md の 1 節）、--tz +09:00（既定はこの PC の時間帯）
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402

NL = C.NL
SKIP = ("This session is being continued", "<local-command", "<command-name>", "<task-notification",
        "[SYSTEM NOTIFICATION", "Another Claude session sent", "Base directory for this skill",
        "[Request interrupted", "Caveat: The messages below")
WS = re.compile("[ \t\r\n　]+")
REMINDER = re.compile("<system-reminder>.*?</system-reminder>", re.S)
BTW = re.compile("<command-name>/btw</command-name>")
CMD = re.compile("<command-name>(.*?)</command-name>", re.S)
ARGS = re.compile("<command-args>(.*?)</command-args>", re.S)
# 設定や操作のコマンド。引数は利用者の判断を含まないので、発言として抜き出さない
BUILTIN = {"/model", "/effort", "/clear", "/compact", "/config", "/context", "/usage", "/resume", "/exit", "/login",
           "/logout", "/permissions", "/hooks", "/mcp", "/status", "/cost", "/memory", "/init", "/doctor", "/help",
           "/btw", "/rewind", "/add-dir", "/agents", "/plugin", "/ide", "/vim", "/theme", "/statusline", "/fast",
           "/output-style", "/export", "/release-notes", "/terminal-setup"}
LIVE_SEC = 600  # 記録がこの秒数以内に更新されていれば、進行中の会話とみなす


def text_of(c):
    if isinstance(c, str):
        return c
    return NL.join(b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")


def raw_text(o):
    """記録の 1 行から、種類を問わず本文の文字列を取り出す（/btw の検出用）。"""
    c = o.get("content")
    if isinstance(c, str):
        return c
    m = o.get("message")
    if isinstance(m, dict):
        mc = m.get("content")
        if isinstance(mc, str):
            return mc
        if isinstance(mc, list):
            return text_of(mc)
    return ""


def has_tool_result(c):
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def clean(tx):
    # システムの注記を先に除く。注記が発言の前に付いた場合に、発言ごと落とさないためである
    tx = REMINDER.sub("", tx or "").strip()
    if not tx:
        return ""
    m = CMD.search(tx[:300])
    if m and not tx.startswith("<local-command"):
        # スキルなどを引数つきで呼んだ発言は、引数が利用者の言葉なので「/名前 引数」の形で残す
        name = m.group(1).strip()
        a = ARGS.search(tx)
        args = a.group(1).strip() if a else ""
        return "%s %s" % (name, args) if args and name not in BUILTIN else ""
    if any(tx.startswith(k) or k in tx[:200] for k in SKIP):
        return ""
    if tx.startswith("You have ") and "orchestration" in tx[:80]:
        return ""
    if tx.startswith("<agent-message"):
        return ""
    return tx


def turns_of(path):
    """[(種類, 時刻の文字列, 利用者の発言, 直前の応答)] を返す。"""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict) and not o.get("isSidechain"):
                rows.append(o)
    # 同じ文の U と、作業の途中で受け取られた印の、記録の中の位置
    u_at, mark_at = {}, {}

    def key_of(c):
        return WS.sub("", clean(c if isinstance(c, str) else text_of(c or [])))
    for i, o in enumerate(rows):
        t = o.get("type")
        if t == "user" and not o.get("isMeta") and not o.get("isCompactSummary"):
            c = (o.get("message") or {}).get("content")
            if not has_tool_result(c) and key_of(c):
                u_at.setdefault(key_of(c), []).append(i)
        elif t == "attachment" and (o.get("attachment") or {}).get("type") == "queued_command":
            k = key_of((o.get("attachment") or {}).get("prompt"))
            if k:
                mark_at.setdefault(k, []).append(i)
        elif t == "queue-operation" and o.get("operation") == "remove" and o.get("reason") == "absorbed_mid_turn":
            k = key_of(o.get("content"))
            if k:
                mark_at.setdefault(k, []).append(i)

    def delivered_as_u(i, key):
        j = next((x for x in u_at.get(key, []) if x > i), None)
        m = next((x for x in mark_at.get(key, []) if x > i), None)
        return j is not None and (m is None or j < m)
    turns, buf, seen_q, seen_b, last_ts = [], [], set(), set(), ""
    for i, o in enumerate(rows):
        t = o.get("type")
        ts = (o.get("timestamp") or "")[:19]
        if t == "assistant":
            tx = text_of((o.get("message") or {}).get("content", [])).strip()
            if tx:
                buf.append(tx)
                last_ts = ts
        elif t == "user":
            if o.get("isMeta") or o.get("isCompactSummary"):
                continue
            c = (o.get("message") or {}).get("content")
            if has_tool_result(c):
                continue
            tx = clean(text_of(c))
            if not tx:
                continue
            turns.append(("U", ts, tx, (NL + NL).join(buf)))
            buf = []
        elif t == "system" and BTW.search(raw_text(o)):
            m = ARGS.search(raw_text(o))
            tx = m.group(1).strip() if m else ""
            key = WS.sub("", tx)
            if not tx or key in seen_b:
                continue
            seen_b.add(key)
            turns.append(("B", ts, tx, (NL + NL).join(buf)))
            buf = []
        elif t == "queue-operation" and o.get("operation") == "enqueue":
            c = o.get("content")
            tx = clean(c if isinstance(c, str) else text_of(c or []))
            key = WS.sub("", tx)
            if not tx or delivered_as_u(i, key) or key in seen_q:
                continue
            seen_q.add(key)
            turns.append(("Q", ts, tx, (NL + NL).join(buf)))
            buf = []
    if buf:
        # 最後の発言のあとに残った応答。落とすと、最後の指摘への対応が記録から消える
        turns.append(("E", last_ts, "", (NL + NL).join(buf)))
    return turns


def numbered(turns):
    """[(番号, 種類, 時刻, 利用者の発言, 直前の応答)]"""
    out, n = [], {"U": 0, "Q": 0, "B": 0, "E": 0}
    for kind, ts, u, a in turns:
        n[kind] += 1
        out.append(("%s%03d" % (kind, n[kind]), kind, ts, u, a))
    return out


def dup_key(ts, user):
    return (ts[:19], C.norm(user)[:300])


def render(sid, items, tz, dups):
    sample = C.parse_ts(items[0][2]) if items else None
    lines = ["# 対話 " + sid + "（利用者の発言と、その直前の応答）", "",
             "機械的に抜き出したもの。ツールの入出力、思考、システムの通知は含まない。",
             "U は通常の発言、Q は AI の作業中に届いた発言、B は /btw で届いた発言、E は最後の発言のあとに残った応答。"
             "U と Q の番号は B と E の有無で変わらない。",
             "見出しの括弧の中は、時刻（UTC）と、現地の日付（%s）。「重複」と書いた発言は、別のセッションに同じ時刻と本文の発言がある。"
             % C.tz_label(tz, sample), ""]
    for num, kind, ts, u, a in items:
        date = C.local_date(C.parse_ts(ts), tz)
        tail = {"Q": "作業中に届いた発言", "B": "/btw で届いた発言",
                "E": "末尾の応答（このあとにユーザーの発言は無い）"}.get(kind, "")
        if num in dups:
            tail += "（重複: %s と同じ発言）" % dups[num]
        lines += ["## %s（%s｜%s）%s" % (num, ts, date, tail), "", "### 直前の応答", "", a or "（なし）", "",
                  "### ユーザーの発言", "", u if kind != "E" else "（なし）", ""]
    return NL.join(lines)


def load_md_dialog(path, tz):
    """変換済みの md を、numbered と同じ形にする。"""
    turns = C.parse_dialog(C.read_text(path), tz)
    if not turns:
        raise SystemExit("対話の見出し（## U001（時刻｜日付））が無い: %s" % path)
    items = []
    for num, t in turns.items():
        if t["ts"] is None:
            raise SystemExit("見出しの時刻を読めない: %s の %s" % (path, num))
        items.append((num, num[0], t["ts_raw"][:19], t["user"], t["prev"]))
    return items


def session_id(path):
    return re.sub(r"\.(jsonl|md)$", "", os.path.basename(path))


def expand(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "*.jsonl")))
        else:
            out.append(p)
    return out


def known_keys(folder, exclude, tz):
    """dialogs フォルダのほかのセッションの発言の鍵 → 「ID8 番号」"""
    keys = {}
    if not os.path.isdir(folder):
        return keys
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".md") or session_id(fn) in exclude:
            continue
        for num, t in C.parse_dialog(C.read_text(os.path.join(folder, fn)), tz).items():
            # 重複の印が付いた発言は鍵にしない。元のセッションを抜き出し直したときに、元の発言に印が付かないようにする
            if num[0] != "E" and t["user"] and not t["dup"]:
                keys.setdefault(dup_key(t["ts_raw"], t["user"]), "%s %s" % (fn[:8], num))
    return keys


def load_items(path, tz):
    if path.endswith(".md"):
        return load_md_dialog(path, tz)
    return numbered(turns_of(path))


def is_live(path):
    """記録が LIVE_SEC 秒以内に更新された jsonl。変換済みの md は、作業中に届く発言を持たないので対象にしない。"""
    return path.endswith(".jsonl") and time.time() - os.path.getmtime(path) < LIVE_SEC


def cmd_list(args, tz):
    _, key, pdir = C.place(args)
    print("プロジェクト %s で取り込み済みかを示す" % key)
    ledger = C.load_ledger(pdir)
    for p in expand(args.paths):
        items = load_items(p, tz)
        sid = session_id(p)
        n = {k: sum(1 for x in items if x[1] == k) for k in "UQB"}
        ts = [C.parse_ts(x[2]) for x in items if x[2]]
        first = min(ts) if ts else None
        last = max(ts) if ts else None
        uq = [C.parse_ts(x[2]) for x in items if x[1] in "UQB" and x[2]]
        last_user = max(uq) if uq else None
        cov = C.covered_until(ledger, sid)
        if cov is None:
            state = "未取り込み"
        elif last_user is None or cov >= last_user:
            state = "取り込み済み"
        else:
            rest = sum(1 for x in items if x[1] in "UQB" and C.parse_ts(x[2]) and C.parse_ts(x[2]) > cov)
            state = "一部取り込み済み（新しい発言 %d）" % rest
        if is_live(p):
            # このスキルを動かしている会話も、ここに並ぶ。進行中の会話の範囲は、最後の U までになる
            state += "（10 分以内に更新された。進行中の会話なら、範囲は最後の U までになる）"
        fmt = lambda d: (d.astimezone(tz) if tz else d.astimezone()).strftime("%Y-%m-%d %H:%M") if d else "-"
        print("%s  %s 〜 %s  U %d  Q %d  B %d  %s" % (sid[:8], fmt(first), fmt(last), n["U"], n["Q"], n["B"], state))


def cmd_out(args, tz):
    os.makedirs(args.out, exist_ok=True)
    for p in expand(args.paths):
        items = load_items(p, tz)
        sid = session_id(p)
        C.write_text(os.path.join(args.out, sid + ".md"), render(sid, items, tz, {}))
        n = {k: sum(1 for x in items if x[1] == k) for k in "UQBE"}
        print("%s U %d Q %d B %d E %d" % (sid[:8], n["U"], n["Q"], n["B"], n["E"]))


def cmd_round(args, tz):
    if not re.fullmatch(r"[0-9A-Za-z._-]+", args.round):
        raise SystemExit("回の名前は英数字と . _ - だけにする: %s" % args.round)
    _, key, pdir = C.place(args, create=True)
    rdir = C.round_dir(pdir, args.round)
    if os.path.isdir(os.path.join(rdir, "records")) and os.listdir(os.path.join(rdir, "records")) and not args.force:
        raise SystemExit("この回には記録がある。範囲を作り直すなら --force を付ける: %s" % rdir)
    until = C.parse_until(args.until)
    since = C.parse_until(args.since, "--since")
    cut = {}
    for x in (args.until_turn or "").split(","):
        if x.strip():
            sid8, turn = x.strip().split(":")
            cut[sid8.strip()[:8]] = turn.strip()
    ledger = C.load_ledger(pdir)
    ddir = C.dialogs_dir(pdir)
    os.makedirs(ddir, exist_ok=True)
    loaded = []
    for p in expand(args.paths):
        if not re.fullmatch(C.SID8, session_id(p)[:8]):
            raise SystemExit("ファイル名の先頭 8 文字は英数字と . _ - にする（セッション ID として使う）: %s" % p)
        items = load_items(p, tz)
        ts = [C.parse_ts(x[2]) for x in items if x[2]]
        loaded.append((min(ts) if ts else None, session_id(p), items, os.path.abspath(p)))
    loaded.sort(key=lambda x: (x[0] is None, x[0] or datetime.datetime.min.replace(tzinfo=C.UTC)))
    names = {x[1] for x in loaded}
    keys = known_keys(ddir, names, tz)
    sessions = []
    for _, sid, items, source in loaded:
        dups = {}
        for num, kind, ts, u, a in items:
            if kind == "E" or not u:
                continue
            k = dup_key(ts, u)
            if k in keys:
                dups[num] = keys[k]
            else:
                keys[k] = "%s %s" % (sid[:8], num)
        C.write_text(os.path.join(ddir, sid + ".md"), render(sid, items, tz, dups))
        start = None if args.from_start else C.covered_until(ledger, sid)
        lim = None
        if sid[:8] in cut:
            hit = [x for x in items if x[0] == cut[sid[:8]]]
            if not hit:
                raise SystemExit("--until-turn の発言が無い: %s %s" % (sid[:8], cut[sid[:8]]))
            lim = C.parse_ts(hit[0][2])
        # 進行中の会話では、最後の U より後の Q は、あとで U として届くと消えて番号がずれ、
        # 届いた U は時刻が遅いので次の回にも入る。最後の U までを範囲にすれば、範囲の中の番号は変わらない
        live = is_live(source)
        last_u = max((k for k, x in enumerate(items) if x[1] == "U"), default=-1) if live else len(items)
        inside, n_since, n_deferred = [], 0, 0
        for k, (num, kind, ts, u, a) in enumerate(items):
            d = C.parse_ts(ts)
            if kind == "E" or d is None or num in dups:
                continue
            if start is not None and d <= start:
                continue
            if not C.within_until(d, until, tz) or (lim is not None and d > lim):
                continue
            if not C.within_since(d, since, tz):
                n_since += 1
                continue
            if k > last_u:
                n_deferred += 1
                continue
            inside.append((num, d, len(u) + len(a)))
        ndup = len(dups)
        if live:
            print("%s: 記録が 10 分以内に更新されているので進行中の会話とみなし、範囲を最後の U（%s）までにした。"
                  "そのあとの発言 %d は次の回に回す" % (sid[:8], items[last_u][0] if last_u >= 0 else "なし", n_deferred))
        if n_since:
            print("%s: --since より前の、まだ取り込んでいない発言 %d を範囲から外した。"
                  "この回を設置すると、これらも取り込み済みとして扱われる" % (sid[:8], n_since))
        if not inside:
            print("%s: 範囲に新しい発言が無い（重複 %d）" % (sid[:8], ndup))
            continue
        parts, cur, size = [], [], 0
        for num, d, n in inside:
            if cur and size + n > args.part_chars:
                parts.append({"from": cur[0], "to": cur[-1], "chars": size})
                cur, size = [], 0
            cur.append(num)
            size += n
        parts.append({"from": cur[0], "to": cur[-1], "chars": size})
        sessions.append({"session": sid, "sid8": sid[:8], "dialog": "dialogs/%s.md" % sid, "source": source,
                         "start_after": C.ts_str(start), "since_from": C.ts_str(C.since_bound(since, tz)),
                         "end": C.ts_str(inside[-1][1]),
                         "first_turn": inside[0][0], "last_turn": inside[-1][0], "turns": len(inside),
                         "chars": sum(x[2] for x in inside), "duplicates": ndup, "parts": parts,
                         "live": live, "deferred": n_deferred, "before_since": n_since})
        print("%s: %s〜%s（%d 発言、%d 字、重複 %d）%s" % (
            sid[:8], inside[0][0], inside[-1][0], len(inside), sessions[-1]["chars"], ndup,
            "" if len(parts) == 1 else " 分割の目安: " + "、".join("%s〜%s" % (x["from"], x["to"]) for x in parts)))
    scope = {"round": args.round, "project": key, "purpose": "exam" if args.exam else "intake",
             "created_at": C.ts_str(datetime.datetime.now(C.UTC)),
             "tz": args.tz or "", "tz_label": C.tz_label(tz), "since": args.since or "", "until": args.until or "",
             "until_turn": args.until_turn or "", "sessions": sessions}
    C.write_json(os.path.join(rdir, "scope.json"), scope)
    os.makedirs(os.path.join(rdir, "records"), exist_ok=True)
    print("範囲: %s（プロジェクト %s、セッション %d、発言 %d）%s" % (
        os.path.join(rdir, "scope.json"), key, len(sessions), sum(s["turns"] for s in sessions),
        "。試験の回なので、この回は設置できない" if args.exam else ""))


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--round")
    g.add_argument("--out")
    C.add_place_args(ap)
    ap.add_argument("--tz")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--until-turn")
    ap.add_argument("--exam", action="store_true")
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--part-chars", type=int, default=100000)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()
    tz = C.tz_of(args.tz)
    if args.list:
        cmd_list(args, tz)
    elif args.round:
        cmd_round(args, tz)
    else:
        cmd_out(args, tz)


if __name__ == "__main__":
    main()
