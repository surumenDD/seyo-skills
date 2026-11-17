#!/usr/bin/env python3
"""コンテキストの使用率が閾値を超えたとき、継続書を検討するよう 1 回だけ知らせるフック。

Claude Code の PostToolUse と UserPromptSubmit に登録して使う。フックの入力には
使用率が含まれないので、次の順で使用率を得る。

1. statusLine のスクリプトが書いた値（<一時フォルダ>/claude-ctx/<session_id>.txt）。
   statusLine の入力の context_window.used_percentage をそのまま書いたもの。
2. 1 が無いか古く、環境変数 HANDOFF_CTX_WINDOW（窓のトークン数）が設定されていれば、
   transcript の最後の usage の合計をその値で割る。
3. どちらも無ければ、何も出さない。transcript には窓の大きさが記録されないので、
   推測した窓で割ると、誤った使用率を知らせてしまう。

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


def window_from_env():
    try:
        w = int(os.environ.get("HANDOFF_CTX_WINDOW") or 0)
    except ValueError:
        return None
    return w if w > 0 else None


def pct_from_transcript(path, window):
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
        return 100.0 * total / window
    return None


def main():
    data = json.load(sys.stdin)
    sid = data.get("session_id") or ""
    if not sid or data.get("agent_id"):       # サブエージェントの中では知らせない
        return
    threshold = float(os.environ.get("HANDOFF_CTX_THRESHOLD") or THRESHOLD)
    pct = pct_from_statusline(sid)
    source = "ステータス行の値"
    if pct is None:
        window = window_from_env()
        if window is None:
            return
        pct = pct_from_transcript(data.get("transcript_path"), window)
        source = "記録のトークン数と HANDOFF_CTX_WINDOW からの計算"
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
    text = (f"コンテキストの使用率が {pct:.0f}% に達した（{source}）。"
            "長い作業の途中なら、handoff-seyo の手順で継続書を書くかを検討する。"
            "書くかどうかは、プロジェクトの規則と利用者の指示に従う。")
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
