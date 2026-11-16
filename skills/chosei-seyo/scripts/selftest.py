"""chosei-seyo のスクリプトを、作り物の小さな対話で一通り動かして確認する。

対話は公開できる題材で作った架空のもの（町内会の清掃と防災訓練のお知らせを AI に書かせ、利用者が直させる）で、
実在の人と記録を含まない。スクリプトはコマンドとして呼び出し、終了コードと出力を確認する。
取り込み（1〜7）、控えへの設置（8）、図鑑への登録と照合、名指しの登録、取り消し（9。登録の担当の案は作り物）、
プロジェクトのキー（10）、引き継ぎと読む場所、共通に移す候補（11）、古い形式の候補の移行（12）、進行中の会話の範囲（13）、
検証の試験の組み立てから集計まで（14。回答と採点も作り物）を確かめる。

使い方: python selftest.py [--keep <フォルダ>]
        --keep を付けると、そのフォルダの下に試験のデータを残す（既にあれば削除して作り直す）。
        付けなければ一時フォルダを使い、終わったら削除する。
"""
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []
ROOT = [""]

SID_A = "aaaa0001-0000-4000-8000-000000000001"
SID_B = "bbbb0002-0000-4000-8000-000000000002"
DRAFT = ("皆さま、いつも町内の美化にご協力いただき、誠にありがとうございます。さて、このたび清掃活動を実施する運びとなりました。"
         "日時は 6 月 1 日 9 時、集合場所は公民館前です。ご協力のほどよろしくお願い申し上げます。")
REVISED = "6 月 1 日 9 時から、公民館前に集合して町内の清掃を行います。皆さまのご参加をお待ちしています。"
FIRST = "町内会の清掃のお知らせを書いてください。日時は 6 月 1 日 9 時、集合は公民館前です。"


def say(text, ts, **kw):
    o = {"type": "user", "message": {"role": "user", "content": text}, "timestamp": ts}
    o.update(kw)
    return o


def ai(text, ts, extra=()):
    blocks = [{"type": "thinking", "thinking": "思考の本文（抜き出さない）"}, {"type": "text", "text": text}]
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks + list(extra)}, "timestamp": ts}


def session_a():
    return [
        say("This session is being continued from a previous conversation. 要約", "2026-05-20T00:59:00.000Z"),
        say(FIRST, "2026-05-20T01:00:00.000Z"),
        ai("案です。\n\n" + DRAFT, "2026-05-20T01:01:00.000Z", [{"type": "tool_use", "name": "Write", "input": {}}]),
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "道具の出力（抜き出さない）"}]},
         "timestamp": "2026-05-20T01:01:30.000Z"},
        say("サブエージェントへの指示（抜き出さない）", "2026-05-20T01:02:00.000Z", isSidechain=True),
        say("冒頭のあいさつが長くて、いつ何をするのかが最後まで分からない。日時と場所を最初に書いてほしい。\n"
            "<system-reminder>システムの注記（抜き出さない）</system-reminder>", "2026-05-20T01:05:00.000Z"),
        ai("直しました。\n\n" + REVISED, "2026-05-20T01:06:00.000Z"),
        {"type": "queue-operation", "operation": "enqueue", "content": "軍手は町内会で用意すると書いておいて",
         "timestamp": "2026-05-20T01:06:30.000Z"},
        ai("軍手は町内会で用意する旨を足しました。", "2026-05-20T01:07:00.000Z"),
        say("メタの行（抜き出さない）", "2026-05-20T01:08:00.000Z", isMeta=True),
        say("ok 承認します", "2026-05-20T01:10:00.000Z"),
        {"type": "system", "subtype": "local_command", "timestamp": "2026-05-20T01:11:00.000Z",
         "content": "<command-name>/btw</command-name>\n<command-args>箇条書きにしたほうが読みやすいかな</command-args>"},
        ai("箇条書きにする案です。\n\n- 日時: 6 月 1 日 9 時\n- 集合: 公民館前\n- 軍手: 町内会で用意します",
           "2026-05-20T01:12:00.000Z", [{"type": "tool_use", "name": "Edit", "input": {}}]),
        say("さっきの箇条書きの件は取り消します。回覧板で読むので、文のままでよいです。"
            "それと、どの作業でも、編集する前に案を見せてほしい。勝手に直さないで。", "2026-05-21T00:30:00.000Z"),
        ai("承知しました。文のままに戻し、今後は編集の前に案を示します。", "2026-05-21T00:31:00.000Z"),
        say("<command-name>/model</command-name>\n<command-args></command-args>", "2026-05-21T00:35:00.000Z"),
        say("<command-message>tidy</command-message>\n<command-name>/tidy</command-name>\n"
            "<command-args>この案の語尾をそろえて</command-args>", "2026-05-21T00:40:00.000Z"),
        ai("語尾をそろえました。", "2026-05-21T00:41:00.000Z"),
    ]


def session_b(extended=False):
    rows = [
        say(FIRST, "2026-05-20T01:00:00.000Z"),
        ai("別の案です。\n\n6 月 1 日 9 時から公民館前で清掃を行います。連絡先は班長の電話です。"
           "ご不明な点があれば班長までお問い合わせください。", "2026-05-22T01:59:00.000Z"),
        say("お知らせの末尾の「ご不明な点があれば班長まで」は要らない。連絡先は上に書いてあるから。", "2026-05-22T02:00:00.000Z"),
        ai("末尾の一文を除きました。", "2026-05-22T02:01:00.000Z"),
    ]
    if extended:
        rows += [say("雨の場合の扱いも一文で書いてほしい。中止なら中止とだけ。", "2026-05-23T03:00:00.000Z"),
                 ai("雨天中止の一文を足しました。小雨決行の場合の連絡方法も添えました。", "2026-05-23T03:01:00.000Z"),
                 say("連絡方法までは頼んでいない。頼んだ範囲で直して。", "2026-05-23T03:05:00.000Z")]
    return rows


SID_C = "cccc0003-0000-4000-8000-000000000003"
SID_D = "dddd0004-0000-4000-8000-000000000004"
DRILL_1 = ("防災訓練のお知らせ\n\n日頃より町内会の活動にご理解をいただき、ありがとうございます。\n本年も防災訓練を行います。\n"
           "当日は消火器の使い方と、避難経路の確認を行います。\n6 月 8 日 10 時に、小学校の校庭へお集まりください。\n"
           "雨天の場合は、体育館で行います。")
DRILL_2 = DRILL_1.replace("本年も防災訓練を行います。\n", "")
DRILL_3 = "6 月 8 日 10 時、小学校の校庭で防災訓練を行います。消火器の使い方と避難経路を確かめます。雨天の場合は、体育館で行います。"
FB_C1 = "「本年も防災訓練を行います」は、見出しと同じことを言っているので要らない。雨天の扱いは、そのままでいい。"
FB_C2 = "日時と場所が後ろのほうにあって、見つけにくい。最初に書いてほしい。"


def session_c():
    """検証の試験に使う会話（取り込みに使っていない時期）。"""
    return [
        say("防災訓練のお知らせを書いてください。日時は 6 月 8 日 10 時、集合は小学校の校庭です。", "2026-05-25T01:00:00.000Z"),
        ai("案です。\n\n" + DRILL_1, "2026-05-25T01:01:00.000Z"),
        say(FB_C1, "2026-05-25T01:05:00.000Z"),
        ai("直しました。\n\n" + DRILL_2, "2026-05-25T01:06:00.000Z"),
        say(FB_C2, "2026-05-25T01:10:00.000Z"),
        ai("日時と場所を冒頭に移しました。\n\n" + DRILL_3, "2026-05-25T01:11:00.000Z"),
        say("これでいいです。", "2026-05-25T01:15:00.000Z"),
    ]


def session_d(delivered=False):
    """進行中の会話。作業の途中で受け取られた Q001 と、まだ届いていない Q002 を持つ。"""
    rows = [
        say("清掃のお知らせの見出しを考えてください。", "2026-05-26T01:00:00.000Z"),
        ai("見出しの案です。\n\n町内清掃のお知らせ", "2026-05-26T01:01:00.000Z"),
        {"type": "queue-operation", "operation": "enqueue", "content": "その案で進めて", "timestamp": "2026-05-26T01:01:30.000Z"},
        {"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn", "content": "その案で進めて",
         "timestamp": "2026-05-26T01:01:40.000Z"},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "その案で進めて"},
         "timestamp": "2026-05-26T01:01:40.000Z"},
        ai("その案で進めます。", "2026-05-26T01:02:00.000Z"),
        say("見出しに日付を入れてほしい。", "2026-05-26T01:05:00.000Z"),
        ai("日付を入れています。", "2026-05-26T01:06:00.000Z"),
        {"type": "queue-operation", "operation": "enqueue", "content": "それと、太字は使わないで", "timestamp": "2026-05-26T01:07:00.000Z"},
    ]
    if delivered:
        rows += [{"type": "queue-operation", "operation": "dequeue", "timestamp": "2026-05-26T01:09:59.000Z"},
                 say("それと、太字は使わないで", "2026-05-26T01:10:00.000Z"),
                 ai("太字を外しました。", "2026-05-26T01:11:00.000Z"),
                 say("その案で進めて", "2026-05-26T01:20:00.000Z"),
                 ai("進めます。", "2026-05-26T01:21:00.000Z")]
    return rows


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_records(path, rows):
    write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def ledger_of(pdir):
    p = os.path.join(pdir, "chosei", "ledger.json")
    return json.loads(read(p)) if os.path.isfile(p) else {"rounds": [{}]}


def read_rows(path):
    return [json.loads(x) for x in read(path).splitlines() if x.strip()] if os.path.isfile(path) else []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("通過: " if cond else "失敗: ") + name + ("" if cond else "\n      " + str(detail)[:1500]))


def runner(home, project="demo"):
    """SEYO_HOME と SEYO_PROJECT を決めてスクリプトを呼ぶ。project が None なら、作業フォルダからキーを決めさせる。

    GIT_CEILING_DIRECTORIES は、試験のフォルダより上の git の作業ツリーを見つけないようにするためである。
    """
    env = dict(os.environ, SEYO_HOME=home, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", GIT_CEILING_DIRECTORIES=ROOT[0])
    env.pop("SEYO_PROJECT", None)
    if project:
        env["SEYO_PROJECT"] = project

    def run(script, *args):
        p = subprocess.run([sys.executable, "-B", os.path.join(HERE, script)] + list(args),
                           capture_output=True, env=env, timeout=120)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    return run


def review(sid, turn, n=1, **kw):
    r = {"id": "review-%s-%s-%d" % (sid, turn, n), "session": sid, "turn": turn, "date": "", "era": "通常", "note": "",
         "target": {"what": "%s の直前の応答（お知らせの案）" % turn, "repo": "", "commit": "", "files": []},
         "review_text": "", "points": []}
    r.update(kw)
    return r


def point(**kw):
    p = {"n": 1, "quote": "", "scale": "文", "where": "", "target_text": "",
         "after": {"commit": "", "text_or_path": "", "how": ""},
         "user_followup": {"type": "unknown", "turn": "", "evidence": ""}}
    p.update(kw)
    return p


def scene(sid, turn, n, **kw):
    s = {"id": "scene-%s-%s-%d" % (sid, turn, n), "session": sid, "turn": turn, "date": "", "kind": "進め方への指摘",
         "critique": "", "ai_response": {"turn": turn, "excerpt": ""}, "what_happened": "", "prior_instructions": [],
         "from_response_alone": "はい", "confidence": "高", "note": ""}
    s.update(kw)
    return s


def cand(**kw):
    c = {"id": "c01-01", "judgment": "", "user_words": [], "ai_pattern": "", "applies_to": "お知らせの文",
         "doc_kind": "一般の読み手への説明", "general": False, "general_words": "", "evidence": [], "n_utterances": 1,
         "limits": "", "strength": "ふつう", "note": ""}
    c.update(kw)
    return c


R1 = review("aaaa0001", "U002", date="2026-05-20",
            review_text="冒頭のあいさつが長くて、いつ何をするのかが最後まで分からない。日時と場所を最初に書いてほしい。",
            points=[point(quote="冒頭のあいさつが長くて、いつ何をするのかが最後まで分からない。", scale="段落", where="お知らせの冒頭",
                          target_text="皆さま、いつも町内の美化にご協力いただき、誠にありがとうございます。さて、このたび清掃活動を実施する運びとなりました。",
                          after={"commit": "", "text_or_path": "texts/review-aaaa0001-U002-1_p1_after.md",
                                 "how": "日時と場所を冒頭の文に移し、あいさつの文を除いた。"},
                          user_followup={"type": "approved", "turn": "U003", "evidence": "ok 承認します"})])
S1 = scene("aaaa0001", "U004", 1, date="2026-05-21", kind="取り下げ・留保",
           critique="さっきの箇条書きの件は取り消します。回覧板で読むので、文のままでよいです。",
           ai_response={"turn": "U004", "excerpt": "箇条書きにする案です。"},
           what_happened="AI は、お知らせを箇条書きにした案を示した。",
           prior_instructions=[{"turn": "B001", "quote": "箇条書きにしたほうが読みやすいかな"}])
S2 = scene("aaaa0001", "U004", 2, date="2026-05-21", critique="それと、どの作業でも、編集する前に案を見せてほしい。勝手に直さないで。",
           ai_response={"turn": "U004", "excerpt": "箇条書きにする案です。"},
           what_happened="AI は、案を示す前に、お知らせの本文を箇条書きに書き換えた。", from_response_alone="いいえ", confidence="中")
R2 = review("bbbb0002", "U002", date="2026-05-22",
            review_text="お知らせの末尾の「ご不明な点があれば班長まで」は要らない。連絡先は上に書いてあるから。",
            points=[point(quote="お知らせの末尾の「ご不明な点があれば班長まで」は要らない。連絡先は上に書いてあるから。",
                          where="お知らせの末尾", target_text="ご不明な点があれば班長までお問い合わせください。",
                          after={"commit": "", "text_or_path": "", "how": "末尾の一文を除いた。"})])

GOOD_CANDS = [
    cand(id="c01-01", judgment="利用者は、お知らせの冒頭にあいさつが続き、日時と場所が最後まで出てこないことを問題にしている。",
         user_words=["いつ何をするのかが最後まで分からない"], ai_pattern="冒頭にあいさつの文を二つ置き、日時と場所を三文目に書いた。",
         evidence=["review-aaaa0001-U002-1#p1"]),
    cand(id="c01-02", judgment="利用者は、回覧板で読むお知らせを箇条書きにすることを求めていない。",
         user_words=["回覧板で読むので、文のままでよいです"], ai_pattern="利用者の思いつきを受けて、本文を箇条書きにした案を示した。",
         evidence=["scene-aaaa0001-U004-1"], limits="箇条書きの案は利用者自身が出し、あとで取り下げた（scene-aaaa0001-U004-1）。"),
    cand(id="c01-03", judgment="利用者は、案を見せる前に AI が本文を書き換えたことを問題にしている。",
         user_words=["どの作業でも、編集する前に案を見せてほしい"], ai_pattern="案を示す前に本文を書き換えた。",
         applies_to="作業の進め方", doc_kind="進め方", general=True, general_words="どの作業でも",
         evidence=["scene-aaaa0001-U004-2"], strength="強い"),
    cand(id="c01-04", judgment="利用者は、連絡先が上にあるのに、末尾で問い合わせを促す文を重ねることを問題にしている。",
         user_words=["連絡先は上に書いてあるから"], ai_pattern="末尾に問い合わせを促す定型の一文を置いた。",
         evidence=["review-bbbb0002-U002-1#p1"], strength="弱い"),
]
BAD_CANDS = [
    {"id": "c01-01", "judgment": "日時を最初に記載すること。", "user_words": ["利用者が言っていない言葉です"], "ai_pattern": "",
     "applies_to": "", "evidence": ["review-zzzz9999-U001-1"], "n_utterances": 1, "limits": "", "strength": "とても強い"},
    {"id": "c01-02", "judgment": "利用者は、日時を冒頭に置く書き方を良いとした。", "user_words": ["6 月 1 日 9 時から、公民館前に集合して"],
     "ai_pattern": "", "applies_to": "", "evidence": ["review-aaaa0001-U002-1#p7"], "n_utterances": 1, "limits": "", "strength": "弱い"},
    {"id": "x-3", "judgment": "利用者は、勝手な編集を問題にしている。", "user_words": ["勝手に直さないで"], "ai_pattern": "",
     "applies_to": "", "evidence": ["scene-aaaa0001-U004-2"], "n_utterances": 1, "limits": "", "strength": "強い"},
]
EV1 = ["review-aaaa0001-U002-1#p1"]
BAD_NEW = [
    cand(id="c01-01", user_words=["いつ何を", "するのかが", "最後まで", "分からない", "日時と場所"], evidence=EV1),
    cand(id="c01-02", user_words=["冒頭のあいさつが長くて" + "あ" * 301], evidence=EV1),
    cand(id="c01-03", user_words=["いつ何をするのかが"], doc_kind="お知らせ", evidence=EV1),
    cand(id="c01-04", user_words=["いつ何をするのかが"], general=True, general_words="どの作業でも", evidence=EV1),
    cand(id="c01-05", user_words=["いつ何をするのかが"], general=True, general_words="", evidence=EV1),
    cand(id="c01-06", user_words=["いつ何をするのかが"], general=False, general_words="いつ何を", evidence=EV1),
    {k: v for k, v in cand(id="c01-07", user_words=["いつ何をするのかが"], evidence=EV1).items() if k != "doc_kind"},
]


def basic_tests(home, src):
    """1〜7. 抜き出し、範囲、記録の照合、入力、区切った範囲、候補の照合、作業の途中の取り込み。"""
    run = runner(home)
    fa, fb = os.path.join(src, SID_A + ".jsonl"), os.path.join(src, SID_B + ".jsonl")
    write_jsonl(fa, session_a())
    write_jsonl(fb, session_b())
    pdir = os.path.join(home, "projects", "demo")
    rounds = os.path.join(pdir, "chosei", "rounds")

    # 1. 対話の抜き出し
    code, out = run("extract_dialog.py", "--out", os.path.join(src, "out"), "--tz", "+09:00", fa, fb)
    check("抜き出し: 発言の種類ごとの数（U 5、Q 1、B 1、E 1）", code == 0 and "aaaa0001 U 5 Q 1 B 1 E 1" in out, out)
    md = read(os.path.join(src, "out", SID_A + ".md"))
    check("抜き出し: 見出しに時刻（UTC）と現地の日付", "## U004（2026-05-21T00:30:00｜2026-05-21）" in md, md[:800])
    check("抜き出し: 作業中の発言（Q）、/btw の発言（B）、末尾の応答（E）",
          "## Q001（2026-05-20T01:06:30｜2026-05-20）作業中に届いた発言" in md and "## B001（" in md and "## E001（" in md, md)
    check("抜き出し: 道具の入出力、思考、注記、サブエージェント、メタの行、継続の要約を含まない",
          not any(x in md for x in ("抜き出さない", "This session is being continued")), md)
    check("抜き出し: 注記の付いた発言の本文は残る", "日時と場所を最初に書いてほしい。" in md, md)
    check("抜き出し: 引数つきのコマンドは「/名前 引数」で残し、設定のコマンドは落とす",
          "## U005（2026-05-21T00:40:00｜2026-05-21）" in md and "/tidy この案の語尾をそろえて" in md and "/model" not in md, md)

    # 2. 回の範囲と重複の印（置き場はプロジェクトの下の chosei/）
    code, out = run("extract_dialog.py", "--round", "r1", "--tz", "+09:00", fa, fb)
    scope = json.loads(read(os.path.join(rounds, "r1", "scope.json")))
    ss = {s["sid8"]: s for s in scope["sessions"]}
    check("範囲: 2 つのセッションの範囲を、プロジェクトの下に作る",
          code == 0 and set(ss) == {"aaaa0001", "bbbb0002"} and scope["project"] == "demo"
          and os.path.isfile(os.path.join(pdir, "project.json")), out)
    check("範囲: 発言の数（重複を除く）", ss.get("aaaa0001", {}).get("turns") == 7 and ss.get("bbbb0002", {}).get("turns") == 1, out)
    mdb = read(os.path.join(pdir, "chosei", "dialogs", SID_B + ".md"))
    check("範囲: 別のセッションと同じ発言に重複の印", "（重複: aaaa0001 U001 と同じ発言）" in mdb, mdb[:900])

    # 2b. 変換済みの md（Codex の記録や貼り付けた対話を、対話の抜き出しの形に直したもの）を受け取る
    sid_c = "chat0003-pasted"  # 16 進でない名前でも、セッション ID として受け取る
    conv = re.sub(r"｜\d{4}-\d{2}-\d{2}）", "）", read(os.path.join(src, "out", SID_B + ".md")))
    fc = os.path.join(src, sid_c + ".md")
    write(fc, conv.replace("連絡先は上に", "連絡先は冒頭に"))
    code, out = run("extract_dialog.py", "--round", "mdin", "--tz", "+09:00", fc)
    sc = json.loads(read(os.path.join(rounds, "mdin", "scope.json")))
    mdc = read(os.path.join(pdir, "chosei", "dialogs", sid_c + ".md"))
    check("変換済みの md: 日付の無い見出しを受け取り、日付と重複の印を補って範囲を作る",
          code == 0 and [s["turns"] for s in sc["sessions"]] == [1] and "## U002（2026-05-22T02:00:00｜2026-05-22）" in mdc
          and "（重複: aaaa0001 U001 と同じ発言）" in mdc, out + mdc[:600])

    # 3. 記録の照合
    write(os.path.join(rounds, "r1", "records", "texts", "review-aaaa0001-U002-1_p1_after.md"), REVISED + "\n")
    write_records(os.path.join(rounds, "r1", "records", "aaaa0001.jsonl"), [R1, S1, S2])
    write_records(os.path.join(rounds, "r1", "records", "bbbb0002.jsonl"), [R2])
    code, out = run("check_records.py", "--round", "r1")
    check("記録の照合: 正しい記録は問題 0 件", code == 0 and "問題 0 件" in out, out)
    b1 = review("aaaa0001", "U002", 9, date="2026-05-21", review_text=R1["review_text"],
                points=[point(quote="存在しない引用です", scale="節", target_text="対話にも git にも無い長い文がここにあります。",
                              user_followup={"type": "approved", "turn": "U003", "evidence": "承認していない文"})])
    b2 = scene("aaaa0001", "U004", 9, date="2026-05-21", kind="感想", critique="そんな発言はしていない",
               ai_response={"turn": "U004", "excerpt": "どこにも無い応答の文です"})
    b3 = review("bbbb0002", "U001", 1, date="2026-05-20", review_text=FIRST, points=[point(quote=FIRST)])
    b4 = review("aaaa0001", "U002", 8, date="2026-05-20", review_text=R1["review_text"],
                points=[point(quote="日時と場所を最初に書いてほしい。",
                              user_followup={"type": "approved", "turn": "U001", "evidence": "町内会の清掃のお知らせを書いてください。"})])
    b5 = dict(S2, id="scene-aaaa0001-U003-1")
    badp = os.path.join(rounds, "r1", "bad_records.jsonl")
    write_records(badp, [b1, b2, b3, b4, b5])
    code, out = run("check_records.py", "--round", "r1", badp)
    expect = ["('review-aaaa0001-U002-9', 'date'", "('review-aaaa0001-U002-9#p1', 'quote'",
              "('review-aaaa0001-U002-9#p1', 'scale'", "('review-aaaa0001-U002-9#p1', 'evidence'",
              "('review-aaaa0001-U002-9#p1', 'target_text'", "('scene-aaaa0001-U004-9', 'kind'",
              "('scene-aaaa0001-U004-9', 'critique'", "('scene-aaaa0001-U004-9', 'ai_response.excerpt'",
              "('review-bbbb0002-U001-1', 'turn', '重複", "('review-aaaa0001-U002-8#p1', 'user_followup.turn'",
              "('scene-aaaa0001-U003-1', 'id'"]
    miss = [e for e in expect if e not in out]
    check("記録の照合: 誤った記録の問題をすべて見つける（%d 種類）" % len(expect), code == 1 and not miss, "見つけなかった: %s\n%s" % (miss, out))
    os.remove(badp)
    repo = os.path.join(src, "repo")
    write(os.path.join(repo, "notice.md"), "# 清掃のお知らせ\n\n当日は動きやすい服装でお越しください。雨天の場合は中止します。\n")
    wt = review("aaaa0001", "U002", 3, date="2026-05-20", review_text=R1["review_text"],
                target={"what": "リポジトリの notice.md（版は特定できなかった）", "repo": repo, "commit": "", "files": ["notice.md"]},
                points=[point(quote="日時と場所を最初に書いてほしい。", target_text="当日は動きやすい服装でお越しください。")])
    wtp = os.path.join(rounds, "r1", "worktree_records.jsonl")
    write_records(wtp, [wt])
    code, out = run("check_records.py", "--round", "r1", wtp)
    check("記録の照合: 版を特定できないリポジトリの原稿は、作業ツリーのファイルと照合して注意を出す",
          code == 0 and "作業ツリー" in out and "問題 0 件" in out, out)
    os.remove(wtp)

    # 4. 入力の作成（範囲を区切らない場合は、反応を省かない）
    code, out = run("build_input.py", "--round", "r1")
    inp = read(os.path.join(rounds, "r1", "in_01.md"))
    check("入力: 記録を時刻の順に並べる", code == 0 and inp.index("review-aaaa0001-U002-1") < inp.index("scene-aaaa0001-U004-1")
          < inp.index("review-bbbb0002-U002-1"), out)
    check("入力: 反応と、直したあとの本文を入れる", "ok 承認します" in inp and "直したあとの本文（冒頭）: 6 月 1 日 9 時から" in inp, inp)
    check("入力: 記録の担当の注記と確信度を入れない", "確信度" not in inp and "confidence" not in inp, inp)

    # 5. 検証のために範囲を区切った場合は、範囲の外の反応を省く
    code, out = run("extract_dialog.py", "--round", "hold", "--tz", "+09:00", "--until-turn", "aaaa0001:U002", fa)
    write(os.path.join(rounds, "hold", "records", "texts", "review-aaaa0001-U002-1_p1_after.md"), REVISED + "\n")
    write_records(os.path.join(rounds, "hold", "records", "aaaa0001.jsonl"), [R1, S1])
    code, out = run("check_records.py", "--round", "hold")
    check("区切った範囲: 範囲の外の発言の記録を問題にする", code == 1 and "U004 は回の範囲の外" in out, out)
    write_records(os.path.join(rounds, "hold", "records", "aaaa0001.jsonl"), [R1])
    code, out = run("build_input.py", "--round", "hold")
    inp = read(os.path.join(rounds, "hold", "in_01.md"))
    man = json.loads(read(os.path.join(rounds, "hold", "manifest.json")))
    check("区切った範囲: 範囲の外の反応と、それを受けた本文を省く",
          code == 0 and "取り込みに使う範囲の外の発言なので省いた" in inp and "ok 承認します" not in inp
          and "直したあとの本文" not in inp and man["hidden_reactions"] == 1, inp)

    # 6. 候補の照合
    cp = os.path.join(rounds, "r1", "cand_01.jsonl")
    write_records(cp, BAD_CANDS)
    code, out = run("check_candidates.py", "--round", "r1")
    expect = ["規則の形", "user_words が入力に無い", "evidence の id が in_01.md に無い", "strength の値",
              "AI の文か説明の欄にある", "指摘の番号が無い", "id は「c01-<連番>」"]
    miss = [e for e in expect if e not in out]
    check("候補の照合: 誤った候補の問題をすべて見つける（%d 種類）" % len(expect), code == 1 and not miss, "見つけなかった: %s\n%s" % (miss, out))
    write_records(cp, BAD_NEW)
    code, out = run("check_candidates.py", "--round", "r1")
    expect = ["c01-01: user_words は 1〜4 個", "c01-02: user_words の 1 つが 300 字を超える", "c01-03: doc_kind の値 'お知らせ'",
              "c01-04: general_words が user_words のどれの中にも無い", "c01-05: general が true なら",
              "c01-06: general が false なら general_words は空", "c01-07: 欄 doc_kind が無い"]
    miss = [e for e in expect if e not in out]
    check("候補の照合: 写しの数と長さ、文書の種類、範囲を述べた語の印の誤りを見つける（%d 種類）" % len(expect),
          code == 1 and not miss, "見つけなかった: %s\n%s" % (miss, out))
    # 時刻の順に並べる確認のため、ファイルの中の順は時刻の順と違えておく
    write_records(cp, [GOOD_CANDS[3], GOOD_CANDS[0], GOOD_CANDS[1], GOOD_CANDS[2]])
    code, out = run("check_candidates.py", "--round", "r1")
    check("候補の照合: 正しい候補は問題 0 件（300 字以内の写しを 4 つまで、種類と印を数える）",
          code == 0 and "問題 0 件" in out and "進め方 1" in out and "言い方の印 1" in out, out)

    # 7. 作業の途中の取り込み（対話から直接に候補を取り出す）
    code, out = run("extract_dialog.py", "--round", "short", "--tz", "+09:00", fb)
    write_records(os.path.join(rounds, "short", "cand_01.jsonl"), [dict(GOOD_CANDS[3], evidence=["bbbb0002 U002"])])
    code, out = run("check_candidates.py", "--round", "short", "--from-dialogs")
    check("作業の途中の取り込み: 対話の発言を根拠にした候補を通す", code == 0 and "問題 0 件" in out, out)
    write_records(os.path.join(rounds, "short", "cand_01.jsonl"), [dict(GOOD_CANDS[3], evidence=["bbbb0002 U001"])])
    code, out = run("check_candidates.py", "--round", "short", "--from-dialogs")
    check("作業の途中の取り込み: 範囲の外（重複）の発言を根拠にした候補を問題にする", code == 1 and "回の範囲に無い" in out, out)
    return run, fa, fb


def install_tests(run, home, fa, fb):
    """8. 控えへの設置、2 度足さないこと、更新の範囲。"""
    pdir = os.path.join(home, "projects", "demo")
    rounds = os.path.join(pdir, "chosei", "rounds")
    ep = os.path.join(pdir, "entries.jsonl")
    code, out = run("install_round.py", "--round", "short")
    check("設置: 照合を通らない候補の回は控えに足さない", code == 1 and "設置しない" in out and not os.path.isfile(ep), out)
    code, out = run("install_round.py", "--round", "r1")
    rows = read_rows(ep)
    ledger = ledger_of(pdir)
    check("設置: 候補を「回:id」の項目として控えに足し、台帳に回を登録を待つ回として記録する。図鑑は作らない",
          code == 0 and [r["id"] for r in rows] == ["r1:c01-04", "r1:c01-01", "r1:c01-02", "r1:c01-03"]
          and rows[1]["time"] == "2026-05-20T01:05:00" and rows[1]["date"] == "2026-05-20"
          and ledger["rounds"][0]["name"] == "r1" and ledger["rounds"][0]["entries"] == 4
          and ledger["rounds"][0]["genten"] == "未登録" and not os.path.isfile(os.path.join(pdir, "genten.md"))
          and "項目 4（一般の読み手への説明 3、進め方 1）をプロジェクト demo の控えに足した" in out
          and "check_genten.py --round r1 --start" in out, out)
    code, out = run("install_round.py", "--round", "r1")
    check("設置: 同じ回を 2 度足さない", code == 1 and "設置済み" in out and len(read_rows(ep)) == 4, out)

    write_jsonl(fb, session_b(extended=True))
    now = time.time()
    os.utime(fa, (now - 3600, now - 3600))
    code, out = run("extract_dialog.py", "--list", "--tz", "+09:00", fa, fb)
    lines = {ln[:8]: ln for ln in out.splitlines() if ln[:8] in ("aaaa0001", "bbbb0002")}
    check("一覧: 取り込み済み、一部取り込み済み、進行中かもしれない会話を見分ける",
          lines.get("aaaa0001", "").endswith("  取り込み済み")
          and "一部取り込み済み（新しい発言 2）（10 分以内に更新された" in lines.get("bbbb0002", ""), out)

    code, out = run("extract_dialog.py", "--round", "r2", "--tz", "+09:00", fa, fb)
    scope = json.loads(read(os.path.join(rounds, "r2", "scope.json")))
    ss = {s["sid8"]: s for s in scope["sessions"]}
    check("更新の範囲: 取り込み済みのセッションを除き、続きの発言だけを範囲にする",
          set(ss) == {"bbbb0002"} and ss["bbbb0002"]["first_turn"] == "U003" and ss["bbbb0002"]["turns"] == 2, out)
    r3 = review("bbbb0002", "U004", date="2026-05-23", review_text="連絡方法までは頼んでいない。頼んだ範囲で直して。",
                points=[point(quote="連絡方法までは頼んでいない。頼んだ範囲で直して。", where="雨天の扱いの文",
                              target_text="雨天中止の一文を足しました。小雨決行の場合の連絡方法も添えました。",
                              after={"commit": "", "text_or_path": "", "how": ""})])
    write_records(os.path.join(rounds, "r2", "records", "bbbb0002.jsonl"), [r3])
    code1, out1 = run("check_records.py", "--round", "r2")
    code2, out2 = run("build_input.py", "--round", "r2")
    write_records(os.path.join(rounds, "r2", "cand_01.jsonl"), [
        cand(id="c01-01", judgment="利用者は、頼んだ範囲を越えて内容を加えたことを問題にしている。", user_words=["頼んだ範囲で直して"],
             ai_pattern="雨天の扱いを加える依頼に、連絡方法の文も加えた。", applies_to="作業の進め方", doc_kind="進め方",
             evidence=["review-bbbb0002-U004-1#p1"], limits="雨天の扱いの文を足す依頼で、連絡方法までは頼まれていなかった。")])
    check("更新: 次の回の記録と入力を照合する", code1 == 0 and code2 == 0, out1 + out2)


R1_ADD = """
## 一般の読み手への説明

### 日時と場所の位置

> いつ何をするのかが最後まで分からない
<!-- r1:c01-01 -->

### 末尾の問い合わせの文

> 連絡先は上に書いてあるから
<!-- r1:c01-04 -->

## 進め方

### 編集の前の確認

> どの作業でも、編集する前に案を見せてほしい
<!-- r1:c01-03 -->
"""
R2_ADD = """
### 頼まれた範囲

> 頼んだ範囲で直して
<!-- r2:c01-01 -->
（注: 雨天の扱いの文を足す依頼で、連絡方法までは頼まれていなかった。）
"""


def register_tests(run, home):
    """9. 図鑑への登録: 案の照合（引用の一字の違い、id の欠け、前の版の引用の削除、候補の扱いの欠け）、設置、名指しの登録、取り消し。"""
    pdir = os.path.join(home, "projects", "demo")
    rounds = os.path.join(pdir, "chosei", "rounds")
    gp = os.path.join(pdir, "genten.md")
    r1 = os.path.join(rounds, "r1")
    dp, xp = os.path.join(r1, "genten.draft.md"), os.path.join(r1, "excluded.jsonl")
    code0, out0 = run("check_genten.py", "--pending")
    code, out = run("check_genten.py", "--round", "r1", "--start")
    base = read(dp)
    check("登録: 登録を待つ回を示し、案を読み方だけのひな形から始める",
          code0 == 0 and "登録を待つ回 1" in out0 and "- r1（取り込み" in out0 and code == 0
          and base.startswith("# 原文の図鑑（プロジェクト demo）") and "見出しの分類は AI の整理であり" in base
          and "引用は、その位置で何が問題だったかの例であり、規則ではない" in base and "注があれば、その境界と経過を合わせて読み" in base,
          out0 + out + base)
    good = base + R1_ADD
    ex_ok = [{"id": "r1:c01-02", "reason": "取り下げられた指摘"}]

    def try_draft(text, excluded):
        write(dp, text)
        write_records(xp, excluded)
        return run("check_genten.py", "--round", "r1")
    code, out = try_draft(good.replace("最後まで分からない", "最後まで分らない"), ex_ok)
    check("図鑑の照合: 引用が控えの写しと一字違えば問題にする", code == 1 and "一字以上違う" in out, out)
    code, out = try_draft(good.replace("> 連絡先は上に書いてあるから\n<!-- r1:c01-04 -->", "> 連絡先は上に書いてあるから"), ex_ok)
    check("図鑑の照合: 引用の直後に id が無ければ問題にする", code == 1 and "id のコメント（<!-- <id> -->）が無い" in out
          and "登録にも外した一覧にも無い: r1:c01-04" in out, out)
    code, out = try_draft(good, [])
    check("図鑑の照合: この回の候補が、登録にも外した一覧にも無ければ問題にする",
          code == 1 and "登録にも外した一覧にも無い: r1:c01-02" in out, out)
    bad = (good.replace("### 編集の前の確認", "### 編集の前に案を見せること")
           .replace("<!-- r1:c01-01 -->", "<!-- r1:c01-01 -->\n（注: 根拠の無い注）")
           .replace("<!-- r1:c01-04 -->", "<!-- r1:c01-04 -->\n\n> 連絡先は上に書いてあるから\n<!-- r1:c01-09 -->")
           .replace("## 進め方", "## お知らせ"))
    code, out = try_draft(bad, [{"id": "r1:c01-02", "reason": "重要でない"}])
    expect = ["規則や文の形にしない: 編集の前に案を見せること", "limits が空なのに注がある", "id が控えに無い",
              "第 2 階層の見出しは文書の種類", "外した理由は", "種類の見出し お知らせ が、項目の doc_kind 進め方 と違う"]
    miss = [e for e in expect if e not in out]
    check("図鑑の照合: 見出しの形、根拠の無い注、控えに無い id、種類の食い違い、外した理由の誤りを見つける（%d 種類）" % len(expect),
          code == 1 and not miss, "見つけなかった: %s\n%s" % (miss, out))
    try_draft(good, ex_ok)
    code, out = run("check_genten.py", "--round", "r1", "--install")
    ledger = ledger_of(pdir)
    check("図鑑の設置: 問題が 0 件なら案を図鑑として置き、登録した件（見出しと引用の最初の行）と外した件（理由つき）を示す",
          code == 0 and read(gp) == good and ledger["rounds"][0]["genten"] == "登録済み"
          and "- 一般の読み手への説明 / 日時と場所の位置: 「いつ何をするのかが最後まで分からない」（r1:c01-01）" in out
          and "- r1:c01-02（取り下げられた指摘）: 「回覧板で読むので、文のままでよいです」" in out
          and "--include <id> --heading <主題>" in out and "何についての指摘か" not in read(gp), out)
    check("図鑑の設置: そのあとに、この回の登録した項目から共通に移す候補を示す",
          "共通に移す候補 1" in out and "- r1:c01-03（進め方）" in out and "範囲を述べた言い方: 「どの作業でも」" in out, out)

    code, out = run("install_round.py", "--round", "r2")
    r2 = os.path.join(rounds, "r2")
    run("check_genten.py", "--round", "r2", "--start")
    d2 = os.path.join(r2, "genten.draft.md")
    write(d2, re.sub(r"\n### 末尾の問い合わせの文\n\n> 連絡先は上に書いてあるから\n<!-- r1:c01-04 -->\n", "\n", read(d2)) + R2_ADD)
    code1, out1 = run("check_genten.py", "--round", "r2")
    check("図鑑の照合: 前の版の引用が消えていれば問題にする",
          code == 0 and code1 == 1 and "前の版の引用、id、注が、変わったか消えた: r1:c01-04" in out1, out + out1)
    run("check_genten.py", "--round", "r2", "--start", "--force")
    write(d2, read(d2) + R2_ADD)
    code, out = run("check_genten.py", "--round", "r2", "--install")
    check("図鑑の設置: 足すだけの案を置き、前の版を回のフォルダに残す。注は limits のある項目に置ける",
          code == 0 and read(os.path.join(r2, "genten.before.md")) == good and read(gp) == good + R2_ADD
          and "- 進め方 / 頼まれた範囲: 「頼んだ範囲で直して」（r2:c01-01）" in out, out)
    code, out = run("check_genten.py", "--round", "r1", "--include", "r1:c01-02", "--heading", "回覧の文の形")
    g = read(gp)
    check("名指しの登録: 外した候補の写しを主題の見出しの下に機械的に置き、照合して設置し、外した一覧から除く",
          code == 0 and "### 回覧の文の形\n\n> 回覧板で読むので、文のままでよいです\n<!-- r1:c01-02 -->" in g
          and g.index("## 一般の読み手への説明") < g.index("### 回覧の文の形") < g.index("## 進め方")
          and read_rows(xp) == [] and "r2:c01-01" in g and os.path.isfile(os.path.join(r1, "genten.before.md")), out + g)

    ep = os.path.join(pdir, "entries.jsonl")
    code, out = run("install_round.py", "--remove", "r2")
    ledger = ledger_of(pdir)
    code2, out2 = run("extract_dialog.py", "--round", "r2b", "--tz", "+09:00", os.path.join(home, "..", "src", SID_B + ".jsonl"))
    sc = json.loads(read(os.path.join(rounds, "r2b", "scope.json")))
    g = read(gp)
    check("取り消し: 回の項目を控えから、引用と注と空の見出しを図鑑から外し、台帳から回を消す。その範囲は次の回に入る",
          code == 0 and [r["id"][:3] for r in read_rows(ep)] == ["r1:"] * 4 and [r["name"] for r in ledger["rounds"]] == ["r1"]
          and "r2:c01-01" not in g and "### 頼まれた範囲" not in g and "<!-- r1:c01-03 -->" in g
          and sc["sessions"][0]["first_turn"] == "U003" and "引用 1 を図鑑から外し" in out, out + out2 + g)
    code, out = run("install_round.py", "--round", "r2")
    run("check_genten.py", "--round", "r2", "--start", "--force")
    write(d2, read(d2) + R2_ADD)
    code2, out2 = run("check_genten.py", "--round", "r2", "--install")
    check("取り消し: 取り消した回は、控えに足し直して登録し直せる", code == 0 and code2 == 0 and len(read_rows(ep)) == 5
          and "<!-- r2:c01-01 -->" in read(gp), out + out2)


def key_tests(root, src):
    """10. プロジェクトのキーの決め方。"""
    home = os.path.join(root, "home_keys")
    run = runner(home, None)
    ws1, ws2, ws3 = (os.path.join(src, "ws1", "alpha"), os.path.join(src, "ws2", "beta"), os.path.join(src, "ws3", "beta"))
    for d in (ws1, ws2, ws3):
        os.makedirs(d, exist_ok=True)
    code, out = run("seyo_where.py", "--cwd", ws2)
    check("読む場所: 図鑑が 1 つも無ければ、1 行で示して正常に終わる",
          code == 0 and len(out.strip().splitlines()) == 1 and "図鑑は無い" in out, out)
    code, out = run("seyo_project.py", "add", "mykey", "--path", ws1)
    code2, out2 = run("seyo_project.py", "current", "--cwd", ws1)
    check("キー: 場所の一覧と一致すれば、そのプロジェクトにする（フォルダの名前と違うキーでも）",
          code == 0 and code2 == 0 and "プロジェクト mykey（決め方: 場所の一覧" in out2, out + out2)
    code, out = run("seyo_project.py", "current", "--cwd", ws2)
    fb = os.path.join(src, SID_B + ".jsonl")
    code2, out2 = run("extract_dialog.py", "--round", "k1", "--tz", "+09:00", "--cwd", ws2, fb)
    pj = json.loads(read(os.path.join(home, "projects", "beta", "project.json")))
    check("キー: 一致が無ければ、作業フォルダの最上位の名前を新しいキーにし、最初の書き込みで場所を登録する",
          code == 0 and "プロジェクト beta（決め方: 作業フォルダの名前" in out and "まだ登録されていない" in out
          and code2 == 0 and len(pj["paths"]) == 1 and os.path.samefile(pj["paths"][0], ws2), out + out2)
    code, out = run("seyo_project.py", "current", "--cwd", ws3)
    code2, out2 = run("extract_dialog.py", "--round", "k2", "--tz", "+09:00", "--cwd", ws3, fb)
    code3, out3 = run("seyo_where.py", "--cwd", ws3)
    check("キー: 同じ名前で場所の違う作業フォルダでは、決めずに終了し、--project の指定を求める",
          code == 1 and "同じ名前で場所の違う作業フォルダ" in out and "--project <キー> で指定する" in out
          and code2 == 1 and not os.path.isdir(os.path.join(home, "projects", "beta", "chosei", "rounds", "k2"))
          and code3 == 2 and "プロジェクトを決められない" in out3, out + out2 + out3)
    code, out = runner(home, "envkey")("seyo_project.py", "current", "--cwd", ws3)
    code2, out2 = runner(home, "envkey")("seyo_project.py", "current", "--cwd", ws3, "--project", "clikey")
    check("キー: --project は環境変数 SEYO_PROJECT より、環境変数は作業フォルダより優先する",
          "プロジェクト envkey（決め方: 環境変数 SEYO_PROJECT" in out and "プロジェクト clikey（決め方: --project" in out2, out + out2)
    code, out = run("seyo_project.py", "add-path", "beta", ws3)
    code2, out2 = run("seyo_project.py", "add-path", "mykey", ws2)
    code3, out3 = run("seyo_project.py", "current", "--cwd", ws3)
    check("キー: 場所を加えると、その場所でもキーが決まる。1 つの場所は 1 つのプロジェクトにだけ登録する",
          code == 0 and code2 == 1 and "プロジェクト beta に登録されている" in out2 and "プロジェクト beta（決め方: 場所の一覧" in out3,
          out + out2 + out3)
    if shutil.which("git"):
        repo = os.path.join(src, "gitrepo", "gamma")
        os.makedirs(os.path.join(repo, "sub", "dir"))
        subprocess.run(["git", "init", "-q", repo], capture_output=True)
        code, out = run("seyo_project.py", "current", "--cwd", os.path.join(repo, "sub", "dir"))
        check("キー: git の作業ツリーの中では、その作業ツリーの最上位の名前を使う", "プロジェクト gamma（" in out, out)
    else:
        print("省略: git が無いので、作業ツリーの最上位の確認を行わない")


PAPER_ADD = """
## 論文

### 結論の重複

> 連絡先は上に書いてあるから、末尾で繰り返さなくていい
<!-- p1:c01-01 -->

### 図の凡例

> 図の凡例は右上にまとめてほしい
<!-- p1:c01-02 -->
"""


def where_common_tests(run, home):
    """11. 引き継ぎの設定と読む順、共通に移す候補の 2 種類、移す、外す。"""
    import check_genten as G
    paper = os.path.join(home, "projects", "paper")
    code, out = run("seyo_project.py", "add", "paper")
    write_records(os.path.join(paper, "entries.jsonl"), [
        dict(cand(id="c01-01", user_words=["連絡先は上に書いてあるから、末尾で繰り返さなくていい"], doc_kind="論文",
                  applies_to="論文の結論", evidence=["eeee0005 U003"]), id="p1:c01-01", round="p1", time="2026-04-01T00:00:00"),
        dict(cand(id="c01-02", user_words=["図の凡例は右上にまとめてほしい"], doc_kind="論文", applies_to="論文の図",
                  evidence=["eeee0005 U007"]), id="p1:c01-02", round="p1", time="2026-04-02T00:00:00")])
    write(os.path.join(paper, "genten.md"), G.skeleton("プロジェクト paper") + PAPER_ADD)
    code3, out3 = run("seyo_project.py", "inherit", "paper")
    code4, out4 = run("seyo_project.py", "inherit", "nothere")
    pj = json.loads(read(os.path.join(home, "projects", "demo", "project.json")))
    check("引き継ぎ: 名指ししたプロジェクトを引き継ぐ設定にし、無いプロジェクトは断る",
          code == 0 and code3 == 0 and pj["inherits"] == ["paper"] and code4 == 1 and "が無い" in out4, out + out3 + out4)

    code, out = run("common_entries.py", "candidates")
    blocks = out.split("\n- ")
    check("共通の候補: 図鑑に登録した項目のうち、範囲を述べた語の印のあるものと、別のプロジェクトに写しの近いものを挙げ、"
          "どちらでもないものは挙げない",
          code == 0 and "共通に移す候補 2" in out and "範囲を述べた言い方: 「どの作業でも」" in out
          and "別のプロジェクト paper の p1:c01-01 と写しが近い" in out and "連絡先は上に書いてあるから、末尾で" in out
          and not any(b.startswith("r1:c01-01") or b.startswith("r1:c01-02") or b.startswith("r2:") for b in blocks)
          and "p1:c01-02" not in out, out)
    code, out = run("common_entries.py", "add", "r1:c01-03")
    common = read_rows(os.path.join(home, "common", "entries.jsonl"))
    cg = os.path.join(home, "common", "genten.md")
    cgt = read(cg) if os.path.isfile(cg) else ""
    code2, out2 = run("common_entries.py", "candidates")
    code3, out3 = run("check_genten.py", "--common")
    check("共通に移す: 指示した id の控えの項目と、図鑑の引用を同じ種類と主題の見出しで写し、元の項目と引用は残す。"
          "写した項目は候補から除き、共通の図鑑も照合を通る",
          code == 0 and [e["id"] for e in common] == ["demo/r1:c01-03"] and common[0]["origin"] == {"project": "demo", "id": "r1:c01-03"}
          and "## 進め方\n\n### 編集の前の確認\n\n> どの作業でも、編集する前に案を見せてほしい\n<!-- demo/r1:c01-03 -->" in cgt
          and "<!-- r1:c01-03 -->" in read(os.path.join(home, "projects", "demo", "genten.md"))
          and "共通に移す候補 1" in out2 and "除いた項目 1" in out2 and "- r1:c01-03" not in out2
          and code3 == 0 and "問題 0 件" in out3, out + out2 + out3 + cgt)

    code, out = run("seyo_where.py")
    lines = out.strip().splitlines()
    check("読む場所: 共通、引き継ぐプロジェクト、いまのプロジェクトの順に、図鑑のパスと種類の節ごとの字数と行を示す",
          code == 0 and len(lines) == 4 and lines[1].startswith("1. 共通: ") and lines[2].startswith("2. 引き継ぎ paper: ")
          and lines[3].startswith("3. いまのプロジェクト demo: ") and "進め方 引用 2" in lines[3]
          and re.search(r"一般の読み手への説明 引用 3 [\d,]+ 字 行 \d+〜\d+", lines[3]) and "読み方 行 1〜" in lines[1], out)
    code, out = run("seyo_where.py", "--kind", "進め方")
    lines = out.strip().splitlines()
    check("読む場所: --kind で、引き継ぐプロジェクトといまのプロジェクトは、その種類の節と進め方の節だけを示す",
          code == 0 and len(lines) == 4 and "一般の読み手への説明" not in out and "進め方 引用 2" in lines[3]
          and "進め方 の節なし" in lines[2], out)
    code, out = run("seyo_where.py", "--kind", "論文")
    lines = out.strip().splitlines()
    check("読む場所: --kind を付けても、共通の図鑑は節の内訳を出さず、全体を読む対象として字数と引用の数を示す",
          code == 0 and len(lines) == 4 and "共通の図鑑は節を問わず全体を読む" in lines[0]
          and re.fullmatch(r"1\. 共通: .+ 計 [\d,]+ 字｜全体を読む 引用 1", lines[1])
          and "読み方 行" not in lines[1] and "進め方" not in lines[1]
          and "論文 引用 2" in lines[2] and "論文 の節なし" in lines[3] and "一般の読み手への説明" not in out, out)
    code, out = run("seyo_project.py", "uninherit", "paper")
    code2, out2 = run("seyo_where.py")
    check("引き継ぎ: 解除すると、読む順から外れる", code == 0 and "引き継ぎ paper" not in out2 and len(out2.strip().splitlines()) == 3,
          out + out2)

    code, out = runner(home, "paper")("common_entries.py", "add", "p1:c01-01")
    code2, out2 = run("common_entries.py", "candidates")
    check("共通の候補: 共通の項目と写しが近い項目は、既に共通にあるとして除く",
          code == 0 and "共通に移す候補: なし" in out2 and "除いた項目 2" in out2, out + out2)
    code, out = run("common_entries.py", "remove", "r1:c01-03")
    code2, out2 = run("common_entries.py", "remove", "nothere")
    code3, out3 = run("check_genten.py", "--common")
    common = read_rows(os.path.join(home, "common", "entries.jsonl"))
    cgt = read(cg)
    check("共通から外す: 共通の項目と引用だけを外し（空の見出しも外す）、元のプロジェクトの項目は残す。無い id は何もしない",
          code == 0 and [e["id"] for e in common] == ["paper/p1:c01-01"] and code2 == 1 and code3 == 0
          and "demo/r1:c01-03" not in cgt and "## 進め方" not in cgt and "<!-- paper/p1:c01-01 -->" in cgt
          and len(read_rows(os.path.join(home, "projects", "demo", "entries.jsonl"))) == 5, out + out2 + out3 + cgt)


OLD_CANDS = [
    {"id": "e01-01", "judgment": "利用者は、案を見せる前の編集を問題にしている。", "user_words": ["編集する前に案を見せてほしい"],
     "ai_pattern": "案を示す前に本文を書き換えた。", "applies_to": "作業の進め方", "evidence": ["scene-aaaa0001-U004-2"],
     "n_utterances": 1, "limits": "", "strength": "強い", "note": ""},
    {"id": "e01-02", "judgment": "利用者は、末尾の重複を問題にしている。", "user_words": ["連絡先は上に書いてあるから"],
     "ai_pattern": "末尾に定型の一文を置いた。", "applies_to": "お知らせの文", "evidence": ["review-bbbb0002-U002-1#p1"],
     "n_utterances": 1, "limits": "", "strength": "弱い", "note": ""},
    {"id": "e01-03", "judgment": "利用者は、要点が後ろにあることを問題にしている。", "user_words": ["いつ何をするのかが最後まで分からない"],
     "ai_pattern": "冒頭にあいさつを置いた。", "applies_to": "AI の返答（長さ）、作業の進め方",
     "evidence": ["review-aaaa0001-U002-1#p1", "review-bbbb0002-U002-1#p1"], "n_utterances": 2, "limits": "", "strength": "ふつう",
     "note": ""},
    {"id": "e01-04", "judgment": "利用者は、箇条書きを求めていない。", "user_words": ["回覧板で読むので"], "ai_pattern": "箇条書きにした。",
     "applies_to": "発表のスライド", "evidence": ["legacy-bbbb-003"], "n_utterances": 1, "limits": "", "strength": "弱い", "note": ""},
]
OLD_CANDS_2 = [
    {"id": "e02-01", "judgment": "利用者は、箇条書きの案を取り下げた。", "user_words": ["文のままでよいです"], "ai_pattern": "箇条書きにした。",
     "applies_to": "お知らせの文", "evidence": ["scene-aaaa0001-U004-1"], "n_utterances": 1, "limits": "", "strength": "弱い", "note": ""},
]


def migrate_tests(root, src, dialogs):
    """12. 古い形式の候補の移行、セッションの ID による振り分け、元のファイルごとの登録を待つ回。"""
    home = os.path.join(root, "home_mig")
    run = runner(home, None)
    old = os.path.join(src, "old", "cand_01.jsonl")
    old2 = os.path.join(src, "old", "cand_02.jsonl")
    write_records(old, OLD_CANDS)
    write_records(old2, OLD_CANDS_2)
    code, out = run("migrate_legacy.py", "--round", "m1", "--route", "aaaa0001=proja", "--project", "projb",
                    "--dialogs", dialogs, old)
    pa, pb = os.path.join(home, "projects", "proja"), os.path.join(home, "projects", "projb")
    ea, eb = read_rows(os.path.join(pa, "entries.jsonl")), read_rows(os.path.join(pb, "entries.jsonl"))
    ra = os.path.join(pa, "chosei", "rounds", "m1-cand_01")
    check("移行: 根拠のセッションの ID で振り分け、欄の無い候補に種類（applies_to から。決められなければその他）と印を補って控えに足す",
          code == 0 and [e["id"] for e in ea] == ["m1-cand_01:e01-01", "m1-cand_01:e01-03"]
          and [e["id"] for e in eb] == ["m1-cand_01:e01-02", "m1-cand_01:e01-04"]
          and [e["doc_kind"] for e in ea + eb] == ["進め方", "報告", "その他", "発表"]
          and all(e["general"] is False and e["general_words"] == "" for e in ea + eb)
          and "根拠が複数のプロジェクトにまたがる候補 1" in out and "どの --route にも当たらない候補 2" in out
          and "プロジェクト proja: 控えの項目 2（報告 1、進め方 1）、登録を待つ回 1（m1-cand_01）" in out
          and not os.path.isfile(os.path.join(pa, "genten.md")), out)
    sc = json.loads(read(os.path.join(ra, "scope.json")))
    cands = read_rows(os.path.join(ra, "cand_01.jsonl"))
    code2, out2 = run("check_genten.py", "--pending", "--project", "proja")
    check("移行: 元のファイルごとに回を作り、補った候補を回のフォルダに置き、登録を待つ回として台帳に記録する",
          sc["purpose"] == "migrate" and [c["id"] for c in cands] == ["e01-01", "e01-03"] and cands[0]["doc_kind"] == "進め方"
          and ledger_of(pa)["rounds"][0]["genten"] == "未登録" and "登録を待つ回 1" in out2 and "m1-cand_01（移行" in out2,
          out2)
    check("移行: --dialogs を渡すと、根拠の発言の時刻を引いて控えに書く",
          ea[0]["time"] == "2026-05-21T00:30:00" and ea[0]["date"] == "2026-05-21" and eb[1]["time"] == "", ea)
    code, out = run("migrate_legacy.py", "--round", "m1", "--route", "aaaa0001=proja", "--project", "projb", old)
    check("移行: 同じ回は 2 度足さない", code == 1 and "移し済みの回がある" in out and len(read_rows(os.path.join(pa, "entries.jsonl"))) == 2,
          out)
    code, out = run("migrate_legacy.py", "--round", "m2", "--route", "aaaa0001=proja", "--project", "projb",
                    "--mixed", "all", "--dry-run", old, old2)
    code2, out2 = run("migrate_legacy.py", "--round", "m3", "--project", "projc", "--kind", "論文", "--dry-run", old)
    check("移行: --mixed all はまたがる候補をどちらにも入れ、ファイルごとに回を分け、--kind は種類を決め、--dry-run は書き込まない",
          code == 0 and "プロジェクト proja: 控えの項目 3" in out and "登録を待つ回 2（m2-cand_01、m2-cand_02）" in out
          and "プロジェクト projb: 控えの項目 3" in out and code2 == 0 and "プロジェクト projc: 控えの項目 4（論文 4）" in out2
          and len(read_rows(os.path.join(pb, "entries.jsonl"))) == 2 and not os.path.isdir(os.path.join(home, "projects", "projc")),
          out + out2)
    run("check_genten.py", "--round", "m1-cand_01", "--project", "proja", "--start")
    write(os.path.join(ra, "genten.draft.md"), read(os.path.join(ra, "genten.draft.md"))
          + "\n## 進め方\n\n### 編集の前の確認\n\n> 編集する前に案を見せてほしい\n<!-- m1-cand_01:e01-01 -->\n")
    write_records(os.path.join(ra, "excluded.jsonl"), [{"id": "m1-cand_01:e01-03", "reason": "その文書だけの決定"}])
    code, out = run("check_genten.py", "--round", "m1-cand_01", "--project", "proja", "--install")
    code2, out2 = run("check_genten.py", "--pending", "--project", "proja")
    check("移行: 登録を待つ回は、取り込みの回と同じ手順で図鑑に登録できる",
          code == 0 and "<!-- m1-cand_01:e01-01 -->" in read(os.path.join(pa, "genten.md")) and "登録を待つ回 0" in out2, out + out2)
    code, out = run("install_round.py", "--remove", "m1-cand_01", "--project", "proja")
    check("移行: install_round.py --remove で、プロジェクトごとに移行を取り消せる（図鑑の引用も外す）",
          code == 0 and read_rows(os.path.join(pa, "entries.jsonl")) == []
          and "m1-cand_01:e01-01" not in read(os.path.join(pa, "genten.md")), out)


def live_tests(root, fa):
    """13. 進行中の会話を取り込む範囲と、--since。"""
    home = os.path.join(root, "home_live")
    run = runner(home)
    rounds = os.path.join(home, "projects", "demo", "chosei", "rounds")
    fd = os.path.join(root, "src", SID_D + ".jsonl")
    write_jsonl(fd, session_d())
    code, out = run("extract_dialog.py", "--round", "live1", "--tz", "+09:00", fd)
    s = json.loads(read(os.path.join(rounds, "live1", "scope.json")))["sessions"][0]
    check("進行中の会話: 範囲を最後の U までにし、まだ届いていない Q を次の回に回す",
          code == 0 and "進行中の会話とみなし" in out and s["turns"] == 3 and s["last_turn"] == "U002"
          and s["deferred"] == 1 and s["live"], out)
    write_records(os.path.join(rounds, "live1", "cand_01.jsonl"), [
        cand(id="c01-01", judgment="利用者は、見出しに日付が無いことを問題にしている。", user_words=["見出しに日付を入れてほしい"],
             ai_pattern="日付の無い見出しを示した。", evidence=["dddd0004 U002"])])
    code, out = run("install_round.py", "--round", "live1")
    write_jsonl(fd, session_d(delivered=True))
    old = time.time() - 3600
    os.utime(fd, (old, old))
    code2, out2 = run("extract_dialog.py", "--round", "live2", "--tz", "+09:00", fd)
    s = json.loads(read(os.path.join(rounds, "live2", "scope.json")))["sessions"][0]
    md = read(os.path.join(home, "projects", "demo", "chosei", "dialogs", SID_D + ".md"))
    check("進行中の会話: 次の回は、届いた U だけを範囲にし、前の回の発言の番号は変わらない",
          code == 0 and code2 == 0 and s["first_turn"] == "U003" and s["turns"] == 2 and not s["live"]
          and "## U002（2026-05-26T01:05:00" in md and "## Q002" not in md, out + out2 + md[:1500])
    check("進行中の会話: 作業の途中で受け取られた Q は、あとで同じ文の U が来ても消えない",
          "## Q001（2026-05-26T01:01:30" in md and "## U004（2026-05-26T01:20:00" in md, md[:1500])
    code, out = run("extract_dialog.py", "--round", "since1", "--tz", "+09:00", "--since", "2026-05-21", fa)
    s = json.loads(read(os.path.join(rounds, "since1", "scope.json")))["sessions"][0]
    check("範囲の始まり: --since の日付より前の発言を外し、外した数を示す",
          code == 0 and s["turns"] == 2 and s["first_turn"] == "U004" and "--since より前の、まだ取り込んでいない発言 5" in out,
          out)
    # 照合も同じ下限を使う。since_from の欄が無い古い scope.json でも、since と時間帯から同じ下限になる
    sp = os.path.join(rounds, "since1", "scope.json")
    write_records(os.path.join(rounds, "since1", "cand_01.jsonl"), [dict(GOOD_CANDS[0], evidence=["aaaa0001 U002"])])
    code, out = run("check_candidates.py", "--round", "since1", "--from-dialogs")
    sc = json.loads(read(sp))
    for x in sc["sessions"]:
        del x["since_from"]
    sc["tz"] = ""
    write(sp, json.dumps(sc, ensure_ascii=False, indent=1) + "\n")
    code2, out2 = run("check_candidates.py", "--round", "since1", "--from-dialogs")
    check("範囲の始まり: 照合でも --since より前の発言を範囲の外にする（since_from の欄が無い scope.json でも）",
          s["since_from"] == "2026-05-20T15:00:00" and code == 1 and code2 == 1
          and all("範囲の発言 2、" in o and "回の範囲に無い（「<ID8> <番号>」の形）: aaaa0001 U002" in o for o in (out, out2)),
          out + out2)


def unit_files(exam, uid, exhibit, context, gold, fixed=""):
    d = os.path.join(exam, "units", uid)
    write(os.path.join(d, "exhibit.md"), exhibit)
    write(os.path.join(d, "context.md"), context)
    write(os.path.join(d, "gold.json"), json.dumps(gold, ensure_ascii=False, indent=1) + "\n")
    if fixed:
        write(os.path.join(d, "fixed.md"), fixed)


def gold_point(pid, quote, where, status, evidence, in_exhibit, keep_span=""):
    return {"id": pid, "quote": quote, "where": where, "status": status, "status_evidence": evidence,
            "derivable": "本文から言える", "derivable_reason": "本文を読めば言える", "in_exhibit": in_exhibit,
            "keep_span": keep_span}


def exam_tests(run, home, src):
    """14. 検証: 試験の回、問題の照合、測定の準備、回収、採点、集計、内訳。"""
    pdir = os.path.join(home, "projects", "demo")
    rounds = os.path.join(pdir, "chosei", "rounds")
    exam = os.path.join(pdir, "chosei", "exam")
    fc = os.path.join(src, SID_C + ".jsonl")
    write_jsonl(fc, session_c())
    code, out = run("extract_dialog.py", "--round", "ex1", "--exam", "--tz", "+09:00", fc)
    sc = json.loads(read(os.path.join(rounds, "ex1", "scope.json")))
    check("試験の回: 取り込んでいない時期の発言で、設置できない回を作る",
          code == 0 and sc["purpose"] == "exam" and sc["sessions"][0]["turns"] == 4 and "試験の回なので" in out, out)
    code, out = run("install_round.py", "--round", "ex1")
    check("試験の回: install_round.py は設置を断る", code == 1 and "試験の回なので設置しない" in out, out)
    u1, u2, seen_id = "review-cccc0003-U002-1", "review-cccc0003-U003-1", "review-aaaa0001-U002-1"
    rc1 = review("cccc0003", "U002", date="2026-05-25", review_text=FB_C1, points=[
        point(n=1, quote="「本年も防災訓練を行います」は、見出しと同じことを言っているので要らない。", where="二文目",
              target_text="本年も防災訓練を行います。", after={"commit": "", "text_or_path": "", "how": "その文を除いた。"}),
        point(n=2, quote="雨天の扱いは、そのままでいい。", where="最後の文", target_text="雨天の場合は、体育館で行います。",
              after={"commit": "", "text_or_path": "", "how": "変えなかった。"})])
    rc2 = review("cccc0003", "U003", date="2026-05-25", review_text=FB_C2, points=[
        point(quote=FB_C2, scale="段落", where="お知らせの本文", target_text="6 月 8 日 10 時に、小学校の校庭へお集まりください。",
              after={"commit": "", "text_or_path": "", "how": "日時と場所の文を冒頭に移した。"},
              user_followup={"type": "approved", "turn": "U004", "evidence": "これでいいです。"})])
    write_records(os.path.join(rounds, "ex1", "records", "cccc0003.jsonl"), [rc1, rc2])
    code, out = run("check_records.py", "--round", "ex1")
    check("試験の回: 記録の照合は取り込みと同じ", code == 0 and "問題 0 件" in out, out)
    dlg = "chosei/dialogs/%s.md" % SID_C
    ex1 = "出どころ: U002 の直前の応答（%s）\n\n案です。\n\n%s\n" % (dlg, DRILL_1)
    ctx1 = "町内会の回覧板で配るお知らせの案。AI が最初に書いた版で、利用者はこれを読んで直しを頼んだ。\n"
    g1 = {"unit": u1, "round": "ex1", "session": "cccc0003", "turn": "U002", "date": "2026-05-25",
          "exhibit_sources": [{"what": "U002 の直前の応答", "dialog": dlg, "lines": ""}], "exhibit_chars": len(ex1),
          "exhibit_trimmed": False, "rewrite_ok": True, "rewrite_note": "お知らせの案で、書き直しの対象になる", "chain": [u2],
          "points": [gold_point(u1 + "#p1", rc1["points"][0]["quote"], "二文目", "確定", "AI はその文を除き（U003 の直前の応答）、利用者は覆していない。",
                                "本年も防災訓練を行います。"),
                     gold_point(u1 + "#p2", "雨天の扱いは、そのままでいい。", "最後の文", "残す", "利用者が「雨天の扱いは、そのままでいい。」と述べた（U002）。",
                                "雨天の場合は、体育館で行います。", "雨天の場合は、体育館で行います。")], "note": ""}
    ex2 = "出どころ: U003 の直前の応答（%s）\n\n直しました。\n\n%s\n" % (dlg, DRILL_2)
    g2 = {"unit": u2, "round": "ex1", "session": "cccc0003", "turn": "U003", "date": "2026-05-25",
          "exhibit_sources": [{"what": "U003 の直前の応答", "dialog": dlg, "lines": ""}], "exhibit_chars": len(ex2),
          "exhibit_trimmed": False, "rewrite_ok": True, "rewrite_note": "", "chain": [u1],
          "points": [dict(gold_point(u2 + "#p1", FB_C2, "お知らせの本文", "確定", "AI は日時と場所の文を冒頭に移し、利用者は「これでいいです。」と述べた（U004）。",
                                     "6 月 8 日 10 時に、小学校の校庭へお集まりください。"), derivable="固定の知識が要る")], "note": ""}
    unit_files(exam, u1, ex1, ctx1, g1)
    unit_files(exam, u2, ex2, "町内会の回覧板で配るお知らせの案。利用者の直しを受けて AI が書いた二つ目の版である。\n", g2,
               "出どころ: U002 の直前の応答（前の版）\n\n本年も防災訓練を行います。\n")
    code, out = run("check_exam.py")
    check("問題の照合: 正しい単位は問題 0 件", code == 0 and "問題 0 件" in out and "正解の指摘 2" in out
          and "直さないのが正解の箇所 1" in out, out)
    bad = dict(g1, points=[dict(g1["points"][0], quote="違う引用", in_exhibit=""), dict(g1["points"][1], keep_span=""),
                           gold_point(u1 + "#p9", "無い指摘", "", "確定", "根拠", "")])
    unit_files(exam, u1, ex1 + "この行は対話にもどの版にも無い作り物の行です。\n", "見出しと同じことを言っているお知らせ。\n", bad)
    code, out = run("check_exam.py", u1)
    expect = ["対話の抜き出しにも", "quote がもとの記録と違う", "in_exhibit を書く", "keep_span を書く", "#p9 はもとの記録に無い",
              "利用者の発言と同じ並び"]
    miss = [e for e in expect if e not in out]
    check("問題の照合: 誤った単位の問題をすべて見つける（%d 種類）" % len(expect), code == 1 and not miss, "見つけなかった: %s\n%s" % (miss, out))
    unit_files(exam, u1, ex1, ctx1, g1)
    unit_files(exam, seen_id, "出どころ: U002 の直前の応答\n\n" + DRAFT + "\n", "お知らせの案。\n",
               dict(g2, unit=seen_id, round="r1", session="aaaa0001", turn="U002", date="2026-05-20", chain=[],
                    points=[gold_point(seen_id + "#p1", R1["points"][0]["quote"], "お知らせの冒頭", "確定", "AI が直した。",
                                       "日時は 6 月 1 日 9 時")]))
    code, out = run("check_exam.py", seen_id)
    check("問題の照合: 取り込みの回の記録と、取り込み済みの発言を問題にする",
          code == 1 and "試験の回ではない" in out and "取り込み済みの範囲にある" in out, out)

    # 測定の準備。条件 E に渡す文書は、層と、いまのプロジェクトの図鑑である
    write(os.path.join(src, "layer.md"), "# 作り物の層\n\n知らせる文は、日時と場所を最初に書く。\n")
    criteria = [{"id": "1", "text": "書き直しの解消率で、E が A を上回る", "type": "diff", "form": "rewrite", "a": "E", "b": "A",
                 "more_than": 0},
                {"id": "2", "text": "直さないのが正解の箇所を壊した割合が、A より 10 ポイントを超えて上がらない", "type": "keep",
                 "form": "rewrite", "a": "E", "b": "A", "max_increase": 10, "min_n": 10},
                {"id": "3", "text": "書き直しの一致度の中央値が 0.9 以下", "type": "similarity", "cond": "E", "max_median": 0.9},
                {"id": "4", "text": "利用者が読んで、書き直しの向きに同意する", "type": "text"}]
    spec = {"conditions": {"A": [], "E": ["layer.md", os.path.join(pdir, "genten.md")]},
            "forms": {"rewrite": ["A", "E"], "recall": ["A", "E"]}, "reps": {"A": 2, "E": 2},
            "units": [u1, u2, seen_id], "criteria": criteria}
    write(os.path.join(src, "spec_bad.json"), json.dumps(spec, ensure_ascii=False))
    code, out = run("exam_run.py", "prepare", "x0", os.path.join(src, "spec_bad.json"), "--work", os.path.join(home, "work"))
    check("測定の準備: 作業フォルダが SEYO_HOME の中にあるか、取り込み済みの単位があれば準備しない",
          code == 1 and "作業フォルダが SEYO_HOME の中" in out and "取り込み済みの範囲にある単位 1" in out
          and not os.path.isdir(os.path.join(exam, "runs", "x0")), out)
    shutil.rmtree(os.path.join(exam, "units", seen_id))
    spec["units"] = [u1, u2]
    write(os.path.join(src, "spec_ex.json"), json.dumps(spec, ensure_ascii=False))
    work = os.path.join(src, "work")
    code, out = run("exam_run.py", "prepare", "ex", os.path.join(src, "spec_ex.json"), "--work", work)
    rdir = os.path.join(exam, "runs", "ex")
    plan = json.loads(read(os.path.join(rdir, "plan.json"))) if os.path.isfile(os.path.join(rdir, "plan.json")) else {}
    jobs = plan.get("jobs", [])
    check("測定の準備: 同じ箇所の前後の版を別の束に分け、条件 × 形 × 回 × 束の job を作る",
          code == 0 and "job 16 件" in out and plan["batches"]["rewrite"] == [[u1], [u2]], out)
    ej = [j for j in jobs if j["cond"] == "E" and j["form"] == "rewrite"][0]
    aj = [j for j in jobs if j["cond"] == "A" and j["form"] == "rewrite"][0]
    files = [read(p) for p in glob.glob(os.path.join(work, "*", "*.md"))]
    check("測定の準備: 文書（層と図鑑）は条件 E の job にだけ置き、問題には正解と条件の名前を入れない",
          os.path.isfile(os.path.join(work, ej["code"], "00_docs.md")) and not os.path.isfile(os.path.join(work, aj["code"], "00_docs.md"))
          and "# 原文の図鑑（プロジェクト demo）" in read(os.path.join(work, ej["code"], "00_docs.md"))
          and "最初に `00_docs.md` を全部読みます。" in read(os.path.join(work, ej["code"], "00_README.md"))
          and read(os.path.join(work, aj["code"], "q1.md")).startswith("あなたは、著者に見せる前に原稿を書き直す担当です。")
          and not any(FB_C1 in t or FB_C2 in t or "status_evidence" in t for t in files), out)

    def answer(j, i, uid):
        if j["form"] == "recall":
            return '{"n": 1, "where": "本年も防災訓練を行います。", "issue": "見出しの繰り返し"}\n'
        return DRILL_3 + "\n" if j["cond"] == "E" else read(os.path.join(exam, "units", uid, "exhibit.md"))
    for j in jobs:
        for i, uid in enumerate(j["units"], 1):
            write(os.path.join(work, j["code"], "a%d.%s" % (i, "jsonl" if j["form"] == "recall" else "md")), answer(j, i, uid))
    code, out = run("exam_run.py", "reset-job", "ex", jobs[0]["code"])
    code1, out1 = run("exam_run.py", "pending", "ex")
    code2, out2 = run("exam_run.py", "collect", "ex")
    check("回収: やり直す job の回答を消し、未回答と欠けを示す",
          "未完了の job 1 件" in out1 and jobs[0]["code"] in out1 and code2 == 1 and "欠け 1 件" in out2, out1 + out2)
    for i, uid in enumerate(jobs[0]["units"], 1):
        write(os.path.join(work, jobs[0]["code"], "a%d.%s" % (i, "jsonl" if jobs[0]["form"] == "recall" else "md")),
              answer(jobs[0], i, uid))
    code, out = run("exam_run.py", "collect", "ex")
    check("回収: 回答がそろえば欠け 0 件", code == 0 and "欠け 0 件" in out, out)

    # 採点（作り物の判定。条件 E は解消し、A は解消しない）
    def grades(grader, flip=False, drop=False):
        key = json.loads(read(os.path.join(rdir, "grade_key_%s.json" % grader)))
        first = True
        for pid, info in key["packets"].items():
            g = json.loads(read(os.path.join(exam, "units", info["unit"], "gold.json")))
            lines = []
            for lab, who in info["labels"].items():
                e = who.startswith("E/")
                for p in g["points"]:
                    if p["status"] == "確定":
                        r = {"recall": ("hit", "miss"), "rewrite": ("resolved", "unresolved")}[info["form"]][0 if e else 1]
                    else:
                        r = "clear" if info["form"] == "recall" else "kept"
                    if flip and first:
                        r, first = {"hit": "miss", "miss": "hit", "resolved": "unresolved", "unresolved": "resolved",
                                    "clear": "flagged", "kept": "broken"}[r], False
                    lines.append(json.dumps({"label": lab, "point": p["id"], "result": r, "reason": "作り物"}, ensure_ascii=False))
            write(os.path.join(key["dir"], "r_%s.jsonl" % pid), "\n".join(lines[1:] if drop and pid == sorted(key["packets"])[0] else lines) + "\n")
        return key
    code, out = run("exam_run.py", "grade-prepare", "ex", "g1")
    key = json.loads(read(os.path.join(rdir, "grade_key_g1.json")))
    packets = [read(p) for p in glob.glob(os.path.join(key["dir"], "g_*.md"))]
    check("採点の準備: 資料を作り、束ごとの依頼文を示し、資料に条件の名前と job の符号を入れない",
          code == 0 and "採点の資料 4 件" in out and "にある採点の資料のうち、次の 4 個を採点してください。" in out
          and not any(j["code"] in t or "E/" in t for t in packets for j in jobs), out)
    grades("g1", drop=True)
    code1, out1 = run("exam_run.py", "grade-collect", "ex", "g1")
    code2, out2 = run("exam_run.py", "grade-pending", "ex", "g1")
    check("採点の回収: 符号と指摘の組の欠けを見つける", code1 == 1 and "欠けた組 1" in out1 and "そろっていない資料 1 件" in out2,
          out1 + out2)
    grades("g1")
    code, out = run("exam_run.py", "grade-collect", "ex", "g1")
    check("採点の回収: 組がそろえば欠け 0 件", code == 0 and "欠けのある資料 0 件" in out, out)
    code, out = run("exam_run.py", "grade-prepare", "ex", "g2", "--every", "3")
    grades("g2", flip=True)
    run("exam_run.py", "grade-collect", "ex", "g2")
    code, out = run("exam_run.py", "aggregate", "ex")
    check("集計: 単位ごとの平均、ブートストラップの区間、採点の一致、判定基準の判定",
          code == 0 and "| A | 2 | 0.0% | 4 |" in out and "| E | 2 | 100.0% | 4 |" in out
          and "| E − A | 2 | +100.0 | +100.0 〜 +100.0 |" in out and "採点の一致（g1 と g2）" in out
          and "| 1 書き直しの解消率で、E が A を上回る | 満たす |" in out and "| 合否に使わない |" in out
          and "| 3 書き直しの一致度の中央値が 0.9 以下 | 満たす |" in out and "| 手で判定する |" in out, out)
    code, out = run("exam_run.py", "breakdown", "ex")
    check("内訳: 指摘ごとの当たり方、同じ条件の 2 回の重なり、何から言えるかごとの率",
          code == 0 and "指摘ごとの当たり方" in out and "E の 2 回: 両方 " in out and "固定の知識が要る: A 0/2、E 2/2" in out, out)
    sp = json.loads(read(os.path.join(rdir, "spec.json")))
    sp["criteria"][0]["more_than"] = 50
    write(os.path.join(rdir, "spec.json"), json.dumps(sp, ensure_ascii=False))
    code, out = run("exam_run.py", "aggregate", "ex")
    check("集計: 判定基準が prepare の時点から変わっていれば知らせる", code == 1 and "判定基準が、prepare の時点の記録から変わっている" in out, out)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    keep = None
    if "--keep" in sys.argv:
        keep = os.path.abspath(sys.argv[sys.argv.index("--keep") + 1])
    root = keep or tempfile.mkdtemp(prefix="chosei-selftest-")
    ROOT[0] = root
    if keep and os.path.isdir(root):
        shutil.rmtree(root)
    home, src = os.path.join(root, "home"), os.path.join(root, "src")
    for d in (home, src):
        os.makedirs(d, exist_ok=True)
    run, fa, fb = basic_tests(home, src)
    install_tests(run, home, fa, fb)
    register_tests(run, home)
    key_tests(root, src)
    where_common_tests(run, home)
    migrate_tests(root, src, os.path.join(home, "projects", "demo", "chosei", "dialogs"))
    live_tests(root, fa)
    exam_tests(run, home, src)

    n_bad = sum(1 for _, ok in RESULTS if not ok)
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    print("selftest: 全 %d 項目が通った" % len(RESULTS) if not n_bad else "selftest: %d 項目のうち %d 項目が失敗" % (len(RESULTS), n_bad))
    sys.exit(1 if n_bad else 0)


if __name__ == "__main__":
    main()
