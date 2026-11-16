"""検証の測定の道具。受験の担当と採点の担当に渡す資料を作り、回答と採点を回収し、集計と内訳を出す。

用語: 測定（run）は 1 回の測定、job は「条件 × 形 × 回 × 単位の束」で、受験の担当 1 つが受け持つ。
受験の担当と採点の担当には、条件の名前を見せない。job と回答には無作為の符号を付け、対応は
SEYO_HOME/projects/<キー>/chosei/exam/runs/<測定>/ の plan.json と grade_key_<採点者>.json に置く。
仕様の形は references/kensho.md にある。受験と採点の担当への文は、作者の測定で使った文面のまま、このファイルに置いてある。

使い方（どのコマンドにも --home <フォルダ>、--project <キー>、--cwd <作業フォルダ> を付けられる。
既定は環境変数 SEYO_HOME（無ければ ~/.seyo）と、作業フォルダから決まるプロジェクト）
  python exam_run.py prepare <測定> <spec.json> --work <作業フォルダ>
      問題を照合し、作業フォルダの下に job ごとのフォルダ（符号の名前）を作り、受験の担当への依頼文を表示する。
      判定基準（spec の criteria）を plan.json に記録する
  python exam_run.py pending <測定>             回答がそろっていない job と、その依頼文を表示する
  python exam_run.py reset-job <測定> <符号>     job の回答を消し、最初からやり直せるようにする
  python exam_run.py collect <測定>             回答を runs/<測定>/answers/ に写し、欠けと形式の誤りを表示する
  python exam_run.py grade-prepare <測定> <採点者> [--every N]
      採点の資料を作り、10 万字以内の束ごとに、採点の担当への依頼文を表示する。--every 3 は 3 つに 1 つの資料だけにする
  python exam_run.py grade-pending <測定> <採点者>  採点がそろっていない資料を束にして、依頼文を表示する
  python exam_run.py grade-collect <測定> <採点者>  採点を写し、組の欠けと値の誤りを表示する
  python exam_run.py aggregate <測定> [--grader g1] 集計と判定基準の判定を表示し、runs/<測定>/result_<採点者>.md に書く
  python exam_run.py breakdown <測定> [--grader g1] 内訳を表示し、runs/<測定>/breakdown_<採点者>.md に書く
"""
import argparse
import datetime
import difflib
import glob
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import sys

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402
import check_exam  # noqa: E402

FORMS = ("rewrite", "recall")
FORM_JA = {"rewrite": "書き直し", "recall": "指摘の再現"}
POS = {"recall": "hit", "rewrite": "resolved"}
NEG = {"recall": "flagged", "rewrite": "broken"}
VALID = {"recall": ({"hit", "miss"}, {"flagged", "clear"}),
         "rewrite": ({"resolved", "unresolved", "worse"}, {"kept", "broken"})}
BUNDLE_CHARS = 100000

# ---- 受験と採点の担当への文（作者の測定で使った文面。変えると、測定の結果と比べられなくなる） ----

RECALL_TASK = """あなたは、著者に代わって原稿を確認する担当です。下の「文脈」を踏まえて「本文」を読み、著者がこれを読んだら指摘すると考える問題を、重要な順に最大 {k} 件挙げてください。

- 1 件ごとに、どの箇所か（本文からそのまま引用する）、何が問題か、を 1〜3 文で具体的に書いてください。
- 問題がないと考える箇所を、数を埋めるために挙げないでください。{k} 件に満たなくてかまいません。
- 読み手の立場で読んでください。読み手がその箇所までに知らないこと、読み手が抱く疑問は、問題として挙げてかまいません。

回答は、次の形式の JSON を 1 行に 1 件ずつ、ファイル `{ans}` に書いてください（ほかの文字を入れない）。
{{"n": 1, "where": "本文からの引用", "issue": "何が問題か"}}
"""

REWRITE_TASK = """あなたは、著者に見せる前に原稿を書き直す担当です。下の「文脈」を踏まえて「本文」を読み、著者が読んだら指摘すると考える問題を、すべて直した版を、ファイル `{ans}` に書いてください。

- 問題があると考える所は、語の入れ替えで済ませず、必要なら文や段落を組み直してください。
- 本文に無い事実を足さないでください。
- 「出どころ:」の行は写さなくてかまいません。
- ファイルに書くのは、書き直した全文だけです。説明は要りません。
"""

README = """# 作業の手順

このフォルダの中のファイルだけを使います。フォルダの外のファイルは、読まないでください（検索もしないでください）。

{docs_line}問題は `q1.md` から `q{n}.md` まで、{n} 問あります。番号の順に 1 問ずつ進めます。

1. `qK.md` を読む。
2. その問題の回答を、問題に書かれた名前のファイルに書く。
3. 回答を書き終えてから、次の問題のファイルを開く。あとの問題を先に読まない。前の問題の回答を、あとから直さない。

すべての回答を書き終えたら、「完了」とだけ返してください。回答の内容を返答に書かないでください。
"""

DOCS_LINE = "最初に `00_docs.md` を全部読みます。そこに書かれた方針に従って、問題に答えます。\n\n"

EXAMINEE_REQUEST = "フォルダ {folder} の `00_README.md` を読み、そのとおりに作業してください。このフォルダの外のファイルは読まないでください。"

GRADE_RECALL = """# 採点: 指摘の再現

原稿の確認の回答を採点します。「本文」「著者の指摘」「回答」を読み、回答（符号ごと）について、著者の指摘ごとに、回答のどれかが同じ問題を指しているかを判定してください。どの回答がどういう条件で書かれたかは、伏せてあります。

hit: 回答のどれかが、著者の指摘と同じ箇所の、同じ性質の問題を、具体的に述べている。言葉が違っていてもよい。
miss: それ以外。

次の場合は miss です。
- 回答が、著者の指摘と同じ語（著者が好んで使う言い方）を使っているが、どの箇所の何が問題かを述べていない。
- 箇所は同じだが、問題の性質が違う。
- 問題の性質は同じだが、別の箇所について述べている（著者の指摘が文書全体に向いている場合を除く）。

hit か miss かで迷う場合は miss とし、reason の最初に「迷い:」と書いてください。

「直さないのが正解の箇所」がある場合は、その箇所ごとにも判定してください。
flagged: 回答のどれかが、その箇所を問題として挙げている。
clear: 挙げていない。

結果は、ファイル `{out}` に、次の形式の JSON を 1 行に 1 件ずつ書いてください。回答の符号 × （著者の指摘 ＋ 直さないのが正解の箇所）のすべての組について 1 行ずつ書きます。
{{"label": "回答の符号", "point": "指摘または箇所の id", "result": "hit / miss / flagged / clear", "answer_n": 該当する回答の番号（無ければ 0）, "reason": "1 文"}}
"""

GRADE_REWRITE = """# 採点: 書き直し

原稿の推敲の結果を採点します。「もとの本文」「著者の指摘」「書き直した本文」を読み、書き直し（符号ごと）について、著者の指摘ごとに判定してください。どの書き直しがどういう条件で書かれたかは、伏せてあります。

resolved: 著者が指摘した問題が、書き直した本文では解消している。
unresolved: 残っている。または、同じ問題が別の形で残っている。
worse: 著者が指摘した問題そのものが、書き直しで強まっている。

判定するのは、著者が指摘した問題だけです。指摘の箇所が変わっていなければ unresolved です。指摘と関係のない所が良くなった、悪くなった、は判定に入れません。resolved か unresolved かで迷う場合は unresolved とし、reason の最初に「迷い:」と書いてください。

「直さないのが正解の箇所」がある場合は、その箇所ごとにも判定してください。
kept: その箇所の内容と働きが保たれている。
broken: 削られた、または著者が残した意図が失われる形に変えられた。

結果は、ファイル `{out}` に、次の形式の JSON を 1 行に 1 件ずつ書いてください。書き直しの符号 × （著者の指摘 ＋ 直さないのが正解の箇所）のすべての組について 1 行ずつ書きます。
{{"label": "書き直しの符号", "point": "指摘または箇所の id", "result": "resolved / unresolved / worse / kept / broken", "reason": "1 文"}}
"""

GRADER_REQUEST = """フォルダ {folder} にある採点の資料のうち、次の {n} 個を採点してください。

{names}

1 つずつ順に読み、それぞれの冒頭に書かれた指示のとおりに採点して、指示にある名前のファイル（`r_` で始まる .jsonl）を、同じフォルダに書いてください。1 つの資料の採点を書き終えてから、次の資料を開いてください。

- 判定は、資料に書かれた本文、著者の指摘、回答だけにもとづいて行います。このフォルダの外のファイルと、指定した資料以外の資料は読まないでください。
- 回答の符号（X01 など）は、資料ごとに無作為に付け直してあります。資料をまたいで、同じ符号を同じ書き手と考えないでください。
- すべての符号と、すべての指摘（と「直さないのが正解の箇所」）の組について、1 行ずつ書いてください。抜けがないか、書いたあとに数えてください。

すべて書き終えたら、「完了」とだけ返してください。"""


# ---- 置き場と読み込み ----------------------------------------------------

def rd(p):
    return C.read_text(p)


def run_dir(home, run):
    return os.path.join(C.exam_dir(home), "runs", run)


_units = {}


def unit(home, uid):
    if uid not in _units:
        d = os.path.join(C.exam_dir(home), "units", uid)
        g = C.read_json(os.path.join(d, "gold.json"))
        fx = os.path.join(d, "fixed.md")
        _units[uid] = {
            "id": uid, "round": g.get("round", ""), "date": g.get("date", ""), "rewrite_ok": bool(g.get("rewrite_ok")),
            "chain": list(g.get("chain") or []),
            "gold": [p for p in g["points"] if p["status"] == "確定" and p["derivable"] != "測れない"],
            "keep": [p for p in g["points"] if p["status"] in ("反論が通った", "残す") and p.get("keep_span")],
            "exhibit": rd(os.path.join(d, "exhibit.md")), "context": rd(os.path.join(d, "context.md")).strip(),
            "fixed": rd(fx) if os.path.isfile(fx) else ""}
    return _units[uid]


def k_for(n):
    return 3 if n <= 500 else 5 if n <= 2000 else 8 if n <= 6000 else 12


def ans_name(form, i):
    return "a%d.jsonl" % i if form == "recall" else "a%d.md" % i


def load_plan(home, run):
    p = os.path.join(run_dir(home, run), "plan.json")
    if not os.path.isfile(p):
        raise SystemExit("測定の計画が無い: %s（先に prepare を実行する）" % p)
    return C.read_json(p)


def fingerprint(criteria):
    return hashlib.sha256(json.dumps(criteria, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def inside(child, parent):
    child, parent = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


# ---- 準備 ----------------------------------------------------------------

def make_batches(units, size, chars):
    """同じ箇所の前後の版（chain）を同じ束に入れずに、単位を束に分ける。あとの版が前の版の答えになるからである。"""
    link = {u["id"]: set(u["chain"]) for u in units}
    for u in units:
        for c in u["chain"]:
            link.setdefault(c, set()).add(u["id"])
    batches = []
    for u in units:
        n = len(u["exhibit"])
        for b in batches:
            if len(b[0]) < size and b[1] + n <= chars and not (link[u["id"]] & set(b[0])):
                b[0].append(u["id"])
                b[1] += n
                break
        else:
            batches.append([[u["id"]], n])
    return [b[0] for b in batches]


def cmd_prepare(home, a):
    run = a.run
    if not re.fullmatch(r"[0-9A-Za-z._-]+", run):
        raise SystemExit("測定の名前は英数字と . _ - だけにする: %s" % run)
    rdir = run_dir(home, run)
    if os.path.isfile(os.path.join(rdir, "plan.json")):
        raise SystemExit("この測定には plan.json がある: %s" % rdir)
    spec_path = os.path.abspath(a.spec)
    spec = C.read_json(spec_path)
    work = os.path.abspath(a.work)
    errors = []
    if inside(work, a.seyo_home):
        errors.append("作業フォルダが SEYO_HOME の中にある。受験の担当がフォルダの外を探すと、正解と図鑑が見つかる")
    conds, forms = spec.get("conditions") or {}, spec.get("forms") or {}
    const = spec.get("constant") or {}
    for f, cs in forms.items():
        if f not in FORMS:
            errors.append("forms の形は rewrite か recall: %s" % f)
        for c in cs:
            if c not in conds:
                errors.append("forms の条件 %s が conditions に無い" % c)
    base = os.path.dirname(spec_path)

    def res(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))
    docs = {c: [res(p) for p in ps] for c, ps in conds.items()}
    const = {n: res(p) for n, p in const.items()}
    for p in [x for ps in docs.values() for x in ps] + list(const.values()):
        if not os.path.isfile(p):
            errors.append("文書が無い: %s" % p)
    udir = os.path.join(C.exam_dir(home), "units")
    uids = spec.get("units") or (sorted(x for x in os.listdir(udir) if os.path.isfile(os.path.join(udir, x, "gold.json")))
                                 if os.path.isdir(udir) else [])
    if not uids:
        errors.append("単位が無い: %s" % udir)
    ex = check_exam.Exam(home)
    seen = []
    for uid in uids:
        bad, _, s, _ = ex.check(uid)
        errors += bad
        if s:
            seen.append(uid)
    unseen = spec.get("unseen", True)
    if unseen and seen:
        errors.append("取り込み済みの範囲にある単位 %d（未見の測定に使えない。取り込んだ指摘の再現を測るなら "
                      "spec に \"unseen\": false と書く）: %s" % (len(seen), "、".join(seen[:10])))
    for c in spec.get("criteria") or []:
        if c.get("type", "text") not in ("diff", "keep", "similarity", "text"):
            errors.append("判定基準の type は diff、keep、similarity、text のどれか: %s" % c)
    if errors:
        print("準備しない。問題 %d 件" % len(errors))
        for x in errors[:60]:
            print("  ", x)
        sys.exit(1)
    us = [unit(home, u) for u in uids]
    rmax = spec.get("rewrite_max_chars", 6000)
    eligible = {"recall": [u for u in us if u["gold"] or u["keep"]],
                "rewrite": [u for u in us if u["rewrite_ok"] and len(u["exhibit"]) <= rmax and u["gold"]]}
    batches = {f: make_batches(eligible[f], spec.get("batch_size", 5), spec.get("batch_chars", 60000)) for f in FORMS}
    rnd = random.Random(run)
    jobs, codes = [], set()
    for form in FORMS:
        for cond in forms.get(form, []):
            for rep in range(1, (spec.get("reps") or {}).get(cond, 1) + 1):
                for bi, batch in enumerate(batches[form], 1):
                    code = ""
                    while not code or code in codes:
                        code = "j" + "".join(rnd.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(6))
                    codes.add(code)
                    jobs.append({"code": code, "cond": cond, "form": form, "rep": rep, "batch": bi, "units": batch})
    rnd.shuffle(jobs)  # 依頼の順から条件のまとまりが分からないようにする
    os.makedirs(os.path.join(rdir, "docs"), exist_ok=True)
    bodies = {}
    for c, ps in docs.items():
        bodies[c] = "\n\n---\n\n".join(rd(p) for p in ps)
        if ps:
            C.write_text(os.path.join(rdir, "docs", "%s.md" % c), bodies[c])
    for n, p in const.items():
        shutil.copyfile(p, os.path.join(rdir, "docs", "const_%s.jsonl" % n))
    for j in jobs:
        d = os.path.join(work, j["code"])
        os.makedirs(d, exist_ok=True)
        docs_line = ""
        if docs[j["cond"]]:
            C.write_text(os.path.join(d, "00_docs.md"), bodies[j["cond"]])
            docs_line = DOCS_LINE
        C.write_text(os.path.join(d, "00_README.md"), README.format(docs_line=docs_line, n=len(j["units"])))
        for i, uid in enumerate(j["units"], 1):
            u = unit(home, uid)
            if j["form"] == "recall":
                task = RECALL_TASK.format(k=k_for(len(u["exhibit"])), ans=ans_name("recall", i))
            else:
                task = REWRITE_TASK.format(ans=ans_name("rewrite", i))
            q = [task, "## 文脈", "", u["context"], ""]
            if u["fixed"]:
                q += ["## 参考の資料", "", u["fixed"], ""]
            q += ["## 本文", "", u["exhibit"]]
            C.write_text(os.path.join(d, "q%d.md" % i), "\n".join(q))
    criteria = spec.get("criteria") or []
    spec2 = dict(spec, conditions=docs, constant=const)
    plan = {"run": run, "created_at": C.ts_str(datetime.datetime.now(C.UTC)), "work": work, "spec": spec2,
            "units": uids, "unseen": unseen, "seen_units": seen, "batches": batches, "jobs": jobs,
            "criteria": criteria, "criteria_sha256": fingerprint(criteria)}
    shutil.copyfile(spec_path, os.path.join(rdir, "spec.json"))
    C.write_json(os.path.join(rdir, "plan.json"), plan)
    nf = {f: sum(1 for j in jobs if j["form"] == f) for f in FORMS}
    print("問題の照合: 単位 %d、問題 0 件%s" % (len(uids), "" if unseen else "（取り込み済みの単位 %d を含む）" % len(seen)))
    print("単位: 書き直し %d（束 %d）、指摘の再現 %d（束 %d）" % (
        len(eligible["rewrite"]), len(batches["rewrite"]), len(eligible["recall"]), len(batches["recall"])))
    print("job %d 件（書き直し %d、指摘の再現 %d）。受験の担当は job ごとに 1 つ起動する" % (len(jobs), nf["rewrite"], nf["recall"]))
    print("判定基準 %d 件を plan.json に記録した（指紋 %s）。集計は、この記録と照らして判定する"
          % (len(criteria), plan["criteria_sha256"]))
    print("受験の担当への依頼文（job ごとに、<符号> だけを入れ替える。ほかは一字も変えない）:")
    print("---")
    print(EXAMINEE_REQUEST.format(folder=os.path.join(work, "<符号>")))
    print("---")
    for j in jobs:
        print(j["code"], FORM_JA[j["form"]], "問題 %d" % len(j["units"]))


# ---- 受験の回収 ------------------------------------------------------------

def answered(plan, j, i):
    p = os.path.join(plan["work"], j["code"], ans_name(j["form"], i))
    return os.path.isfile(p) and rd(p).strip() != ""


def cmd_pending(home, a):
    plan = load_plan(home, a.run)
    n = 0
    for j in plan["jobs"]:
        miss = [i for i in range(1, len(j["units"]) + 1) if not answered(plan, j, i)]
        if miss:
            n += 1
            print("%s  問題 %d のうち未回答 %d" % (j["code"], len(j["units"]), len(miss)))
            print("  " + EXAMINEE_REQUEST.format(folder=os.path.join(plan["work"], j["code"])))
    print("未完了の job %d 件（全 %d 件）" % (n, len(plan["jobs"])))


def cmd_reset_job(home, a):
    plan = load_plan(home, a.run)
    if a.code not in {j["code"] for j in plan["jobs"]}:
        raise SystemExit("この測定に符号 %s の job は無い" % a.code)
    d = os.path.join(plan["work"], a.code)
    k = 0
    for p in glob.glob(os.path.join(d, "a*.jsonl")) + glob.glob(os.path.join(d, "a*.md")):
        os.remove(p)
        k += 1
    print("%s: 回答のファイル %d 個を消した" % (a.code, k))


def bad_answer_lines(text):
    n = 0
    for ln in text.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
            if not isinstance(o, dict) or not {"n", "where", "issue"} <= set(o):
                n += 1
        except Exception:
            n += 1
    return n


def cmd_collect(home, a):
    plan = load_plan(home, a.run)
    miss, malformed = 0, 0
    for j in plan["jobs"]:
        for i, uid in enumerate(j["units"], 1):
            name = ans_name(j["form"], i)
            src = os.path.join(plan["work"], j["code"], name)
            if not answered(plan, j, i):
                miss += 1
                print("回答が無い:", j["code"], name, uid)
                continue
            dst = os.path.join(run_dir(home, a.run), "answers", j["code"], name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            if j["form"] == "recall":
                b = bad_answer_lines(rd(src))
                if b:
                    malformed += 1
                    print("形式の誤り: %s %s の %d 行が {\"n\", \"where\", \"issue\"} の JSON でない" % (j["code"], name, b))
    print("欠け %d 件、形式の誤りのある回答 %d 件" % (miss, malformed))
    sys.exit(1 if miss else 0)


def read_answer(home, run, j, i):
    p = os.path.join(run_dir(home, run), "answers", j["code"], ans_name(j["form"], i))
    return rd(p) if os.path.isfile(p) else None


# ---- 採点 ----------------------------------------------------------------

def bundles_of(pids, sizes):
    out, cur, n = [], [], 0
    for pid in pids:
        if cur and n + sizes[pid] > BUNDLE_CHARS:
            out.append(cur)
            cur, n = [], 0
        cur.append(pid)
        n += sizes[pid]
    if cur:
        out.append(cur)
    return out


def print_requests(folder, bundles):
    for k, b in enumerate(bundles, 1):
        print("--- 束 %d（資料 %d）" % (k, len(b)))
        print(GRADER_REQUEST.format(folder=folder, n=len(b), names="、".join("g_%s.md" % p for p in b)))
    print("---")


def cmd_grade_prepare(home, a):
    plan = load_plan(home, a.run)
    gdir = os.path.join(plan["work"], "grade_" + a.grader)
    rnd = random.Random(a.run + a.grader)
    const = {n: os.path.join(run_dir(home, a.run), "docs", "const_%s.jsonl" % n)
             for n in (plan["spec"].get("constant") or {})}
    by = {}
    for j in plan["jobs"]:
        for i, uid in enumerate(j["units"], 1):
            by.setdefault((j["form"], uid), []).append((j, i))
    items = sorted(by.items(), key=lambda kv: (kv[0][0] != "recall", kv[0][1]))
    if a.every > 1:
        items = items[::a.every]  # 1 番目、1+N 番目、… の資料だけを採点させる（2 人目の採点者で一致率を見るため）
    key, sizes, order = {}, {}, []
    for (form, uid), lst in items:
        u = unit(home, uid)
        answers = []
        for j, i in lst:
            ans = read_answer(home, a.run, j, i)
            if ans is not None:
                answers.append(("%s/%s/%d" % (j["cond"], j["code"], j["rep"]), ans))
        if form == "recall":
            for name, path in const.items():
                lines = [x for x in rd(path).splitlines() if x.strip()][:k_for(len(u["exhibit"]))]
                answers.append(("%s/const/1" % name, "\n".join(lines)))
        if not answers:
            continue
        rnd.shuffle(answers)
        pid = "%s_%s" % ("rc" if form == "recall" else "rw", uid.replace("review-", ""))
        out = "r_%s.jsonl" % pid
        body = [(GRADE_RECALL if form == "recall" else GRADE_REWRITE).format(out=out), "## 文脈", "", u["context"], ""]
        if u["fixed"]:
            body += ["## 参考の資料", "", u["fixed"], ""]
        body += ["## " + ("本文" if form == "recall" else "もとの本文"), "", u["exhibit"], "", "## 著者の指摘", ""]
        for p in u["gold"]:
            body += ["### 指摘 %s" % p["id"], "- 著者の発言: " + p["quote"], "- 対象: " + p["where"],
                     "- 本文の該当の箇所: " + (p.get("in_exhibit") or "（文書全体）"), ""]
        if u["keep"]:
            body += ["## 直さないのが正解の箇所", ""]
            for p in u["keep"]:
                body += ["### 箇所 %s" % p["id"], "- 本文の該当の箇所: " + p["keep_span"],
                         "- 経緯: 著者はいったん次のように述べたが、あとで取り下げた（または、残すと決めた）: " + p["quote"], ""]
        body += ["## " + ("回答" if form == "recall" else "書き直した本文"), ""]
        labels = {}
        for n, (who, ans) in enumerate(answers, 1):
            lab = "X%02d" % n
            labels[lab] = who
            body += ["### 符号 %s" % lab, "", ans.strip(), ""]
        text = "\n".join(body)
        C.write_text(os.path.join(gdir, "g_%s.md" % pid), text)
        key[pid] = {"form": form, "unit": uid, "labels": labels}
        sizes[pid] = len(text)
        order.append(pid)
    bundles = bundles_of(order, sizes)
    C.write_json(os.path.join(run_dir(home, a.run), "grade_key_%s.json" % a.grader),
                 {"dir": gdir, "every": a.every, "packets": key, "sizes": sizes, "bundles": bundles})
    print("採点の資料 %d 件、束 %d。採点の担当は束ごとに 1 つ起動する。依頼文（一字も変えない）:" % (len(order), len(bundles)))
    print_requests(gdir, bundles)


def load_key(home, run, grader):
    p = os.path.join(run_dir(home, run), "grade_key_%s.json" % grader)
    if not os.path.isfile(p):
        raise SystemExit("採点の資料が無い: %s（先に grade-prepare を実行する）" % p)
    return C.read_json(p)


def read_grades(home, path, info):
    """(有効な判定 {(符号, id): 結果}, 欠けた組の数, 値の誤りの数)"""
    u = unit(home, info["unit"])
    gold_ids, keep_ids = {p["id"] for p in u["gold"]}, {p["id"] for p in u["keep"]}
    expected = {(lab, p) for lab in info["labels"] for p in gold_ids | keep_ids}
    got, invalid = {}, 0
    if os.path.isfile(path):
        for ln in rd(path).splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except Exception:
                invalid += 1
                continue
            pair = (r.get("label"), r.get("point"))
            ok = VALID[info["form"]][0 if pair[1] in gold_ids else 1]
            if pair not in expected or r.get("result") not in ok:
                invalid += 1
                continue
            got[pair] = r["result"]
    return got, len(expected - set(got)), invalid


def cmd_grade_pending(home, a):
    k = load_key(home, a.run, a.grader)
    pend = [pid for pid, info in k["packets"].items()
            if read_grades(home, os.path.join(k["dir"], "r_%s.jsonl" % pid), info)[1]]
    order = [p for b in k["bundles"] for p in b]
    pend.sort(key=lambda p: order.index(p) if p in order else len(order))
    print("採点がそろっていない資料 %d 件（全 %d 件）" % (len(pend), len(k["packets"])))
    if pend:
        print_requests(k["dir"], bundles_of(pend, k["sizes"]))


def cmd_grade_collect(home, a):
    k = load_key(home, a.run, a.grader)
    miss = 0
    for pid, info in k["packets"].items():
        src = os.path.join(k["dir"], "r_%s.jsonl" % pid)
        _, lack, invalid = read_grades(home, src, info)
        if os.path.isfile(src):
            dst = os.path.join(run_dir(home, a.run), "grades_" + a.grader, "r_%s.jsonl" % pid)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        if lack or invalid:
            miss += 1
            print("採点の欠け: %s（欠けた組 %d、値か組の誤り %d）" % (pid, lack, invalid))
    print("欠けのある資料 %d 件（全 %d 件）" % (miss, len(k["packets"])))
    sys.exit(1 if miss else 0)


def load_grades(home, run, grader):
    """{(形, 単位, 「条件/符号/回」, 指摘の id): 結果}。回収した採点のうち、有効な行だけ。"""
    k = load_key(home, run, grader)
    out = {}
    for pid, info in k["packets"].items():
        got, _, _ = read_grades(home, os.path.join(run_dir(home, run), "grades_" + grader, "r_%s.jsonl" % pid), info)
        for (lab, point), res in got.items():
            out[(info["form"], info["unit"], info["labels"][lab], point)] = res
    return out


# ---- 集計 ----------------------------------------------------------------

def boot(diffs, n=5000, seed=1):
    """単位を抜き直すブートストラップで、差の平均の 95% の区間を出す。"""
    rnd = random.Random(seed)
    m = []
    for _ in range(n):
        s = [diffs[rnd.randrange(len(diffs))] for _ in diffs]
        m.append(sum(s) / len(s))
    m.sort()
    return m[int(0.025 * n)], m[int(0.975 * n)]


def tables(home, g):
    """形ごとに (条件 → 単位 → 率, 条件 → 直さないのが正解の箇所の 0/1 の一覧, 条件 → 指摘の延べ数)。"""
    out = {}
    for form in FORMS:
        per, ctl = {}, {}
        for (f, uid, who, point), r in g.items():
            if f != form:
                continue
            cond, code, _ = who.split("/")
            u = unit(home, uid)
            if point in {p["id"] for p in u["gold"]}:
                per.setdefault(cond, {}).setdefault(uid, {}).setdefault(code, []).append(1 if r == POS[form] else 0)
            elif point in {p["id"] for p in u["keep"]}:
                ctl.setdefault(cond, []).append(1 if r == NEG[form] else 0)
        rate = {c: {uid: statistics.mean(sum(v) / len(v) for v in codes.values()) for uid, codes in us.items()}
                for c, us in per.items()}
        npts = {c: sum(len(v) for codes in us.values() for v in codes.values()) for c, us in per.items()}
        out[form] = (rate, ctl, npts)
    return out


def diff_of(rate, a, b):
    if a not in rate or b not in rate:
        return None
    common = sorted(set(rate[a]) & set(rate[b]))
    if len(common) < 2:
        return None
    d = [rate[a][u] - rate[b][u] for u in common]
    lo, hi = boot(d)
    return 100 * statistics.mean(d), 100 * lo, 100 * hi, len(common)


def similarity(home, plan, run):
    sim = {}
    for j in plan["jobs"]:
        if j["form"] != "rewrite":
            continue
        for i, uid in enumerate(j["units"], 1):
            ans = read_answer(home, run, j, i)
            if ans is not None:
                ratio = difflib.SequenceMatcher(None, unit(home, uid)["exhibit"], ans, autojunk=False).ratio()
                sim.setdefault(j["cond"], []).append(ratio)
    return sim


def judge(c, tab, sim):
    t = c.get("type", "text")
    if t == "diff":
        d = diff_of(tab[c["form"]][0], c["a"], c["b"])
        if d is None:
            return "判定できない", "条件が無いか、共通の単位が 2 つ未満"
        mean, lo, hi, n = d
        ok = (mean > c["more_than"] if "more_than" in c else True) and \
             (mean >= c["at_least"] if "at_least" in c else True) and (lo > 0 if c.get("ci_excludes_zero") else True)
        return ("満たす" if ok else "満たさない"), "%+.1f（%+.1f〜%+.1f、%d 単位）" % (mean, lo, hi, n)
    if t == "keep":
        ctl = tab[c["form"]][1]
        ka, kb = ctl.get(c["a"]), ctl.get(c["b"])
        if not ka or not kb:
            return "判定できない", "直さないのが正解の箇所の判定が無い"
        inc = 100 * (statistics.mean(ka) - statistics.mean(kb))
        num = "%s %d/%d、%s %d/%d、差 %+.1f" % (c["a"], sum(ka), len(ka), c["b"], sum(kb), len(kb), inc)
        if min(len(ka), len(kb)) < c.get("min_n", 0):
            return "合否に使わない", num + "（件数が下限 %d に届かない）" % c["min_n"]
        return ("満たす" if inc <= c["max_increase"] else "満たさない"), num
    if t == "similarity":
        v = sim.get(c["cond"])
        if not v:
            return "判定できない", "書き直しの回答が無い"
        med = statistics.median(v)
        return ("満たす" if med <= c["max_median"] else "満たさない"), "中央値 %.2f" % med
    return "手で判定する", ""


def cmd_aggregate(home, a):
    plan = load_plan(home, a.run)
    g = load_grades(home, a.run, a.grader)
    tab = tables(home, g)
    sim = similarity(home, plan, a.run)
    lines = ["# 測定 %s の集計（採点者 %s）" % (a.run, a.grader), ""]
    changed = fingerprint(plan.get("criteria") or []) != plan.get("criteria_sha256")
    sp = os.path.join(run_dir(home, a.run), "spec.json")
    if os.path.isfile(sp) and fingerprint(C.read_json(sp).get("criteria") or []) != plan.get("criteria_sha256"):
        changed = True
    if changed:
        lines += ["判定基準が、prepare の時点の記録から変わっている。変えたあとの基準による判定は、測定の前に決めた判定ではない。", ""]
    lines += ["単位: %s" % ("すべて未見（取り込み済みの範囲の外）" if not plan.get("seen_units")
                          else "取り込み済みの単位 %d を含む（未見の測定ではない）" % len(plan["seen_units"])), ""]
    pairs = [tuple(x) for x in plan["spec"].get("compare") or []]
    for form in FORMS:
        rate, ctl, npts = tab[form]
        if not rate:
            continue
        lines += ["## " + FORM_JA[form], "",
                  "| 条件 | 単位の数 | %s（単位ごとの平均） | 指摘の数（延べ） | 直さないのが正解の箇所を%s割合（件数） |"
                  % ("再現率" if form == "recall" else "解消率", "挙げた" if form == "recall" else "壊した"),
                  "|---|---|---|---|---|"]
        for c in sorted(rate):
            k = ctl.get(c, [])
            lines.append("| %s | %d | %.1f%% | %d | %s |" % (
                c, len(rate[c]), 100 * statistics.mean(rate[c].values()), npts[c],
                ("%.0f%%（%d）" % (100 * statistics.mean(k), len(k))) if k else "—"))
        lines += ["", "| 比較 | 共通の単位 | 差（ポイント） | 95% の区間 |", "|---|---|---|---|"]
        done = set()
        for x, y in [(c, "A") for c in sorted(rate) if c != "A"] + pairs:
            d = diff_of(rate, x, y)
            if d and (x, y) not in done:
                done.add((x, y))
                lines.append("| %s − %s | %d | %+.1f | %+.1f 〜 %+.1f |" % (x, y, d[3], d[0], d[1], d[2]))
        lines.append("")
    if sim:
        lines += ["## 書き直しの、もとの本文との一致度", ""]
        for c in sorted(sim):
            v = sorted(sim[c])
            lines.append("- %s: 中央値 %.2f（最小 %.2f、最大 %.2f、%d 件）" % (c, statistics.median(v), v[0], v[-1], len(v)))
        lines.append("")
    others = [os.path.basename(p)[len("grade_key_"):-len(".json")]
              for p in sorted(glob.glob(os.path.join(run_dir(home, a.run), "grade_key_*.json")))]
    for o in [x for x in others if x != a.grader]:
        g2 = load_grades(home, a.run, o)
        common = [k for k in g if k in g2]
        if common:
            same = sum(1 for k in common if g[k] == g2[k])
            lines += ["## 採点の一致（%s と %s）" % (a.grader, o), "",
                      "%d 組中 %d（%.1f%%）" % (len(common), same, 100 * same / len(common)), ""]
    crit = plan.get("criteria") or []
    if crit:
        lines += ["## 判定基準（prepare の時点の記録）", "", "| 基準 | 結果 | 数値 |", "|---|---|---|"]
        for c in crit:
            r, num = judge(c, tab, sim)
            lines.append("| %s %s | %s | %s |" % (c.get("id", ""), c.get("text", ""), r, num))
        lines.append("")
    text = "\n".join(lines) + "\n"
    C.write_text(os.path.join(run_dir(home, a.run), "result_%s.md" % a.grader), text)
    print(text)
    sys.exit(1 if changed else 0)


def cmd_breakdown(home, a):
    plan = load_plan(home, a.run)
    g = load_grades(home, a.run, a.grader)
    lines = ["# 測定 %s の内訳（採点者 %s）" % (a.run, a.grader)]
    pairs = [tuple(x) for x in plan["spec"].get("compare") or []]
    for form in FORMS:
        pts, keep = {}, {}
        for (f, uid, who, point), r in g.items():
            if f != form:
                continue
            u = unit(home, uid)
            if point in {p["id"] for p in u["gold"]}:
                pts.setdefault((uid, point), {})[who] = 1 if r == POS[form] else 0
            elif point in {p["id"] for p in u["keep"]}:
                keep.setdefault((uid, point), {})[who] = r
        if not pts:
            continue
        conds = sorted({w.split("/")[0] for d in pts.values() for w in d})

        def of(d, c):
            return [v for w, v in d.items() if w.split("/")[0] == c]
        never = sum(1 for d in pts.values() if not any(d.values()))
        allc = sum(1 for d in pts.values() if all(any(of(d, c)) for c in conds))
        lines += ["", "## %s: 指摘ごとの当たり方（正解 %d 件、単位 %d）" % (FORM_JA[form], len(pts), len({k[0] for k in pts})),
                  "", "- どの条件のどの回でも%s: %d 件。全条件で（どれかの回で）%s: %d 件" % (
                      "当たらない" if form == "recall" else "解消しない", never,
                      "当たる" if form == "recall" else "解消する", allc)]
        for c in conds:
            reps = sorted({w.split("/")[2] for d in pts.values() for w in d if w.split("/")[0] == c})
            row = []
            for r in reps:
                v = [x for d in pts.values() for w, x in d.items() if w.split("/")[0] == c and w.split("/")[2] == r]
                row.append("回 %s: %d/%d" % (r, sum(v), len(v)))
            lines.append("- %s: %s" % (c, "、".join(row)))
        for c in conds:
            pair = []
            for d in pts.values():
                r = {w.split("/")[2]: v for w, v in d.items() if w.split("/")[0] == c}
                if "1" in r and "2" in r:
                    pair.append((r["1"], r["2"]))
            if pair:
                both = sum(1 for x, y in pair if x and y)
                one = sum(1 for x, y in pair if x != y)
                lines.append("- %s の 2 回: 両方 %d、片方だけ %d、どちらもなし %d" % (c, both, one, len(pair) - both - one))
        for x, y in [(c, "A") for c in conds if c != "A"] + pairs:
            if x in conds and y in conds:
                up = dn = eq = 0
                for d in pts.values():
                    vx, vy = of(d, x), of(d, y)
                    if not vx or not vy:
                        continue
                    mx, my = statistics.mean(vx), statistics.mean(vy)
                    up, dn, eq = up + (mx > my), dn + (mx < my), eq + (mx == my)
                lines.append("- 指摘ごとの %s と %s: %s が上 %d、下 %d、同じ %d" % (x, y, x, up, dn, eq))
        rounds = sorted({unit(home, uid)["round"] for uid, _ in pts})
        if len(rounds) > 1:
            lines.append("- 試験の回ごとの率（単位ごとの平均）")
            for rnd in rounds:
                sub = {k: d for k, d in pts.items() if unit(home, k[0])["round"] == rnd}
                rate = {}
                for (uid, _), d in sub.items():
                    for w, v in d.items():
                        rate.setdefault(w.split("/")[0], {}).setdefault(uid, {}).setdefault(w.split("/")[1], []).append(v)
                rate = {c: {u: statistics.mean(sum(v) / len(v) for v in cs.values()) for u, cs in us.items()}
                        for c, us in rate.items()}
                row = []
                for c in sorted(rate):
                    s = "%s %.1f%%（%d 単位）" % (c, 100 * statistics.mean(rate[c].values()), len(rate[c]))
                    d = diff_of(rate, c, "A") if c != "A" else None
                    if d:
                        s += " A との差 %+.1f（%+.1f〜%+.1f）" % d[:3]
                    row.append(s)
                lines.append("  - %s: %s" % (rnd, "／".join(row)))
        deriv = {p["id"]: p["derivable"] for uid, _ in pts for p in unit(home, uid)["gold"]}
        if len(set(deriv.values())) > 1:
            lines.append("- 何から言えるかごとの率（指摘の延べ）")
            for dv in sorted(set(deriv.values())):
                row = []
                for c in conds:
                    v = [x for (uid, point), d in pts.items() if deriv[point] == dv for x in of(d, c)]
                    if v:
                        row.append("%s %d/%d" % (c, sum(v), len(v)))
                lines.append("  - %s: %s" % (dv, "、".join(row)))
        if keep:
            lines.append("- 直さないのが正解の箇所（%d か所）を%s回答の数" % (len(keep), "挙げた" if form == "recall" else "壊した"))
            for c in conds:
                v = [r for d in keep.values() for w, r in d.items() if w.split("/")[0] == c]
                lines.append("  - %s: %d/%d" % (c, sum(1 for r in v if r == NEG[form]), len(v)))
    cnt = {}
    for j in plan["jobs"]:
        if j["form"] == "recall":
            for i, _ in enumerate(j["units"], 1):
                ans = read_answer(home, a.run, j, i)
                if ans is not None:
                    cnt.setdefault(j["cond"], []).append(sum(1 for x in ans.splitlines() if x.strip().startswith("{")))
    if cnt:
        lines += ["", "## 指摘の再現の、1 回答あたりの件数", ""]
        lines += ["- %s: %.1f 件（%d 回答）" % (c, statistics.mean(v), len(v)) for c, v in sorted(cnt.items())]
    text = "\n".join(lines) + "\n"
    C.write_text(os.path.join(run_dir(home, a.run), "breakdown_%s.md" % a.grader), text)
    print(text)


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    C.add_place_args(common)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", parents=[common])
    p.add_argument("run")
    p.add_argument("spec")
    p.add_argument("--work", required=True)
    for name in ("pending", "collect"):
        sub.add_parser(name, parents=[common]).add_argument("run")
    p = sub.add_parser("reset-job", parents=[common])
    p.add_argument("run")
    p.add_argument("code")
    p = sub.add_parser("grade-prepare", parents=[common])
    p.add_argument("run")
    p.add_argument("grader")
    p.add_argument("--every", type=int, default=1)
    for name in ("grade-pending", "grade-collect"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("run")
        p.add_argument("grader")
    for name in ("aggregate", "breakdown"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("run")
        p.add_argument("--grader", default="g1")
    a = ap.parse_args()
    # 以下の関数の引数 home は、プロジェクトのフォルダである（検証の置き場はその下の chosei/exam/）
    a.seyo_home, _, home = C.place(a)
    {"prepare": cmd_prepare, "pending": cmd_pending, "reset-job": cmd_reset_job, "collect": cmd_collect,
     "grade-prepare": cmd_grade_prepare, "grade-pending": cmd_grade_pending, "grade-collect": cmd_grade_collect,
     "aggregate": cmd_aggregate, "breakdown": cmd_breakdown}[a.cmd](home, a)


if __name__ == "__main__":
    main()
