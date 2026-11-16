#!/usr/bin/env python3
"""コンテキストの使用率が閾値を超えたとき、継続書を書くよう 1 回だけ知らせるフック。

Claude Code の PostToolUse と UserPromptSubmit に登録して使う。フックの入力には
使用率が含まれないので、次の順で使用率を得る。

1. statusLine のスクリプトが書いた値（<一時フォルダ>/claude-ctx/<session_id>.txt）。
   statusLine の入力の context_window.used_percentage をそのまま書いたもの。
2. 1 が無いか古い場合は、transcript の最後の usage から概算する
   （input_tokens + cache_creation_input_tokens + cache_read_input_tokens）。
   窓の大きさは分からないので、合計が 20 万を超えていれば 100 万、そうでなければ
   20 万として割る。概算なので、知らせの文にその旨を書く。

知らせは 1 回の上昇につき 1 回だけ出す。使用率が再び下がったら（圧縮のあとなど）、
次の上昇でもう一度出す。どんな失敗でも、何も出さずに終了コード 0 で終わる。

閾値は環境変数 HANDOFF_CTX_THRESHOLD で変えられる（既定 90）。
"""
import json
import os
import sys
import tempfile
import time

THRESHOLD = 90.0
REARM_MARGIN = 25.0      # 閾値よりこれだけ下がったら、次の上昇でまた知らせる
STALE_SECONDS = 1800     # statusLine の値がこれより古ければ使わない


def state_dir():
    d = os.path.join(tempfile.gettempdir(), "claude-ctx")
    os.makedirs(d, exist_ok=True)
    return d


def pct_from_statusline(sid):
    p = os.path.join(state_dir(), sid + ".txt")
    if not os.path.isfile(p) or time.time() - os.path.getmtime(p) > STALE_SECONDS:
        return None
    with open(p, encoding="utf-8") as f:
        return float(f.read().strip())


def pct_from_transcript(path):
    if not path or not os.path.isfile(path):
        return None
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 400_000))
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(tail):
        if '"usage"' not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("isSidechain"):
            continue
        u = (o.get("message") or {}).get("usage") or {}
        total = sum(int(u.get(k) or 0) for k in
                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        if total <= 0:
            continue
        window = 1_000_000 if total > 200_000 else 200_000
        return 100.0 * total / window
    return None


def main():
    data = json.load(sys.stdin)
    sid = data.get("session_id") or ""
    if not sid or data.get("agent_id"):       # サブエージェントの中では知らせない
        return
    threshold = float(os.environ.get("HANDOFF_CTX_THRESHOLD") or THRESHOLD)
    approx = False
    pct = pct_from_statusline(sid)
    if pct is None:
        pct = pct_from_transcript(data.get("transcript_path"))
        approx = True
    if pct is None:
        return
    marker = os.path.join(state_dir(), sid + ".notified")
    if pct < threshold - REARM_MARGIN:
        if os.path.exists(marker):
            os.remove(marker)
        return
    if pct < threshold or os.path.exists(marker):
        return
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"{pct:.0f}")
    text = (f"コンテキストの使用率が{'約' if approx else ''} {pct:.0f}% に達した"
            f"{'（transcript からの概算）' if approx else ''}。"
            "このセッションでは、使用率が高くなった時点で、handoff-seyo の手順で継続書を書くことにしている。"
            "進行中の一手を切りのよい所まで終えたら、最新の 1 本で再開できる継続書を書き、"
            "ユーザーの直近の注意は原文のまま引用する。文脈の圧縮は近い。")
    event = data.get("hook_event_name") or "PostToolUse"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception:
        pass
    sys.exit(0)
