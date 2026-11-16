"""chosei-seyo のスクリプトが共有する関数。単独では実行しない。

利用者ごとのデータは、公開のスキルの外の SEYO_HOME（既定は ~/.seyo）に置く。
指摘の控え（entries.jsonl）と図鑑（genten.md）はプロジェクトごとに projects/<キー>/ に、利用者が指示した指摘だけを common/ に置く。
回、対話の抜き出し、台帳、検証は、プロジェクトの下の chosei/ に置く。置き場の全体は references/data.md にある。
"""
import datetime
import io
import json
import os
import re
import subprocess
import sys

NL = "\n"
UTC = datetime.timezone.utc

# 図鑑の節。層の文書の種類の名前に「進め方」と「その他」を加えたもの。この順に節を並べる
KINDS = ("論文", "発表", "報告", "査読への回答", "メール", "AI 向けの文書", "付属文書", "一般の読み手への説明",
         "進め方", "その他")


def setup_stdout():
    """Windows の端末でも日本語の出力が乱れないようにする。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---- 置き場 -------------------------------------------------------------

def seyo_home(arg=None):
    return os.path.abspath(os.path.expanduser(arg or os.environ.get("SEYO_HOME") or "~/.seyo"))


def projects_dir(home):
    return os.path.join(home, "projects")


def project_dir(home, key):
    return os.path.join(home, "projects", key)


def common_dir(home):
    return os.path.join(home, "common")


def entries_path(d):
    """d はプロジェクトのフォルダか共通のフォルダ。"""
    return os.path.join(d, "entries.jsonl")


def genten_path(d):
    return os.path.join(d, "genten.md")


def project_json_path(pdir):
    return os.path.join(pdir, "project.json")


# 作業用の置き場。引数 pdir はプロジェクトのフォルダ（project_dir の値）である

def chosei_dir(pdir):
    return os.path.join(pdir, "chosei")


def dialogs_dir(pdir):
    return os.path.join(pdir, "chosei", "dialogs")


def round_dir(pdir, name):
    return os.path.join(pdir, "chosei", "rounds", name)


def ledger_path(pdir):
    return os.path.join(pdir, "chosei", "ledger.json")


def exam_dir(pdir):
    return os.path.join(pdir, "chosei", "exam")


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1) + NL)


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        f.write(text)


def read_text(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read().replace("\r\n", NL)


def read_jsonl(path, bad):
    """1 行 1 件の JSON を読む。読めない行は bad に加える。"""
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception as e:
                bad.append("%s の %d 行目が JSON として読めない: %s" % (os.path.basename(path), i, str(e)[:80]))
    return rows


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=NL) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + NL)


def now_str():
    return ts_str(datetime.datetime.now(UTC))


# ---- プロジェクト --------------------------------------------------------

BAD_KEY = re.compile(r'[\\/:*?"<>|\s]')


def safe_key(name):
    """フォルダの名前を、キーに使える形にする（使えない文字を _ に置き換える）。"""
    return BAD_KEY.sub("_", name or "").strip("._")


def check_key(key):
    key = (key or "").strip()
    if not key or BAD_KEY.search(key) or key.startswith("."):
        raise SystemExit("プロジェクトのキーに使えない値: %r（空白と \\ / : * ? \" < > | を使わず、. で始めない）" % key)
    return key


def samepath(a, b):
    na = os.path.normcase(os.path.normpath(os.path.abspath(a)))
    nb = os.path.normcase(os.path.normpath(os.path.abspath(b)))
    return na == nb


def top_of(cwd):
    """作業フォルダの最上位。git の作業ツリーの中ならその作業ツリーの最上位、そうでなければ作業フォルダそのもの。"""
    cwd = os.path.abspath(cwd)
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, timeout=30)
        if out.returncode == 0:
            t = out.stdout.decode("utf-8", "replace").strip()
            if t:
                return os.path.normpath(t)
    except Exception:
        pass
    return os.path.normpath(cwd)


def load_project(home, key):
    return read_json(project_json_path(project_dir(home, key)))


def save_project(home, key, pj):
    write_json(project_json_path(project_dir(home, key)), pj)


def all_projects(home):
    """{キー: project.json の中身}。project.json のあるフォルダだけを数える。"""
    out = {}
    d = projects_dir(home)
    if os.path.isdir(d):
        for k in sorted(os.listdir(d)):
            pj = read_json(project_json_path(os.path.join(d, k)))
            if pj is not None:
                out[k] = pj
    return out


def ensure_project(home, key, path=None):
    """project.json が無ければ作り、path が登録されていなければ加える。"""
    pj = load_project(home, key)
    changed = pj is None
    if pj is None:
        pj = {"key": key, "paths": [], "inherits": []}
    pj.setdefault("paths", [])
    pj.setdefault("inherits", [])
    if path and not any(samepath(p, path) for p in pj["paths"]):
        pj["paths"].append(os.path.normpath(path))
        changed = True
    if changed:
        save_project(home, key, pj)
    return pj


SAME_NAME = ("作業フォルダ %s の名前から決まるプロジェクト %s には、別の場所（%s）が登録されている。"
             "同じ名前で場所の違う作業フォルダなので、プロジェクトを決めずに終了する。--project <キー> で指定する。"
             "同じプロジェクトの別の場所なら seyo_project.py add-path %s <このフォルダ>、"
             "別のプロジェクトなら seyo_project.py add <新しいキー> --path <このフォルダ> で登録すると、次からはこの場所で決まる")


def resolve_project(home, project=None, cwd=None, create=False):
    """プロジェクトのキーを決め、(キー, プロジェクトのフォルダ, 決め方, 作業フォルダの最上位) を返す。

    優先の順は、--project、環境変数 SEYO_PROJECT、project.json の場所の一覧との一致、作業フォルダの最上位の名前。
    名前で決めたキーに別の場所が登録されていれば、決めずに終了する。create が真なら project.json を作り、
    名前で決めた場合は作業フォルダの最上位を場所の一覧に加える。
    """
    top = top_of(cwd or os.getcwd())
    env = (os.environ.get("SEYO_PROJECT") or "").strip()
    if project:
        key, how = check_key(project), "--project"
    elif env:
        key, how = check_key(env), "環境変数 SEYO_PROJECT"
    else:
        projs = all_projects(home)
        hits = [k for k, pj in projs.items() if any(samepath(p, top) for p in pj.get("paths") or [])]
        if len(hits) > 1:
            raise SystemExit("作業フォルダ %s が、複数のプロジェクト（%s）の場所に登録されている。"
                             "--project <キー> で指定する" % (top, "、".join(hits)))
        if hits:
            key, how = hits[0], "場所の一覧"
        else:
            key = safe_key(os.path.basename(top))
            if not key:
                raise SystemExit("作業フォルダ %s の名前からキーを決められない。--project <キー> で指定する" % top)
            pj = projs.get(key)
            if pj and pj.get("paths"):
                raise SystemExit(SAME_NAME % (top, key, "、".join(pj["paths"]), key))
            how = "作業フォルダの名前"
    if create:
        ensure_project(home, key, top if how == "作業フォルダの名前" else None)
    return key, project_dir(home, key), how, top


def add_place_args(ap):
    ap.add_argument("--home", help="SEYO_HOME（既定は環境変数 SEYO_HOME、無ければ ~/.seyo）")
    ap.add_argument("--project", help="プロジェクトのキー（既定は環境変数 SEYO_PROJECT、場所の一覧、作業フォルダの名前の順）")
    ap.add_argument("--cwd", help="プロジェクトを決める作業フォルダ（既定はいまのフォルダ）")


def place(a, create=False):
    """(SEYO_HOME, キー, プロジェクトのフォルダ)。"""
    home = seyo_home(a.home)
    key, pdir, _, _ = resolve_project(home, a.project, a.cwd, create)
    return home, key, pdir


# ---- 控え（entries.jsonl） -------------------------------------------------

def load_entries(d):
    p = entries_path(d)
    if not os.path.isfile(p):
        return []
    bad = []
    rows = read_jsonl(p, bad)
    if bad:
        raise SystemExit("控えを読めない: " + bad[0])
    return rows


def save_entries(d, rows):
    write_jsonl(entries_path(d), rows)


# ---- 候補の形式 ------------------------------------------------------------

# 古い形式の候補の applies_to から種類を決める手がかり。applies_to の中で最も前に現れた語の種類にする
KIND_HINTS = (
    ("査読への回答", ("査読",)),
    ("論文", ("論文", "要旨")),
    ("発表", ("発表", "スライド", "台本")),
    ("メール", ("メール",)),
    ("AI 向けの文書", ("AI 向け", "依頼文", "指示書", "スキルの文書", "スキルの本体", "プロンプト", "CLAUDE.md",
                     "AGENTS.md", "継続書")),
    ("付属文書", ("付属文書", "README")),
    ("一般の読み手への説明", ("一般の読み手",)),
    ("進め方", ("進め方",)),
    ("報告", ("返答", "報告", "説明")),
)


def kind_from_applies_to(text):
    best = None
    for kind, words in KIND_HINTS:
        for w in words:
            i = (text or "").find(w)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, kind)
    return best[1] if best else None


def upgrade_candidate(r, kind=None):
    """古い形式の候補（doc_kind、general、general_words の欄が無い）に欄を補う。(補った写し, 種類の決め方) を返す。

    種類は、kind を渡せばそれに、無ければ applies_to から決め、決められなければ「その他」にする。
    general は偽にする。古い候補の担当は、範囲を述べた語に印を付けていないからである。
    """
    r = dict(r)
    how = "候補の欄"
    if not r.get("doc_kind"):
        if kind:
            r["doc_kind"], how = kind, "指定"
        else:
            k = kind_from_applies_to(r.get("applies_to"))
            r["doc_kind"], how = (k, "applies_to") if k else ("その他", "決められない")
    r.setdefault("general", False)
    r.setdefault("general_words", "")
    return r, how


# ---- 文字列の比較 ---------------------------------------------------------

def norm(s):
    """空白を除く。写しの照合は、改行や空白の違いを問わない。"""
    return re.sub(r"\s+", "", s or "")


def loose(s):
    """書式の記号（太字、コード、表の縦棒、引用、見出し、箇条書き）も除いた比較用の文字列。"""
    return re.sub(r"[*`|>#\-]", "", norm(s))


def pieces(s, minlen=3):
    """「（中略）」と改行で区切った断片のうち、空白を除いて minlen 字を超えるもの。"""
    return [p for p in re.split(r"（中略）|\n", s or "") if len(norm(p)) > minlen]


def find_in(hay_n, hay_l, s, minlen=3):
    """s の断片がすべて hay にあるか。(一致, 書式の記号を除けば一致, 無かった断片の一覧) を返す。"""
    miss, loose_only = [], False
    for p in pieces(s, minlen):
        if norm(p) in hay_n:
            continue
        if loose(p) and loose(p) in hay_l:
            loose_only = True
            continue
        miss.append(p.strip())
    return (not miss and not loose_only), (not miss and loose_only), miss


# ---- 時刻 ---------------------------------------------------------------

def parse_ts(s):
    """記録の時刻（ISO 8601）を UTC の datetime にする。時間帯の無い時刻は UTC とみなす。"""
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def ts_str(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") if d else ""


def tz_of(arg):
    """「+09:00」のような指定を時間帯にする。None はこの PC の時間帯を使う。"""
    if not arg:
        return None
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})?", arg.strip())
    if not m:
        raise SystemExit("--tz は +09:00 の形で指定する: %s" % arg)
    sign = 1 if m.group(1) == "+" else -1
    delta = datetime.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
    return datetime.timezone(sign * delta)


def local_date(d, tz=None):
    if d is None:
        return ""
    return (d.astimezone(tz) if tz else d.astimezone()).date().isoformat()


def tz_label(tz, sample=None):
    d = sample or datetime.datetime.now(UTC)
    off = (d.astimezone(tz) if tz else d.astimezone()).utcoffset()
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return "UTC%s%02d:%02d" % (sign, abs(mins) // 60, abs(mins) % 60)


def parse_until(s, name="--until"):
    """--until と --since の値。日付（YYYY-MM-DD。その日を含む、現地の日付）か、時刻（時間帯が無ければ UTC）。"""
    if not s:
        return None
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return ("date", s)
    d = parse_ts(s)
    if d is None:
        raise SystemExit("%s を読めない: %s" % (name, s))
    return ("ts", d)


def within_until(d, until, tz=None):
    if until is None:
        return True
    if d is None:
        return False
    kind, v = until
    if kind == "date":
        return local_date(d, tz) <= v
    return d <= v


def within_since(d, since, tz=None):
    """--since の値（parse_until と同じ形）。日付ならその日を含み、時刻ならその時刻を含む。"""
    if since is None:
        return True
    if d is None:
        return False
    kind, v = since
    if kind == "date":
        return local_date(d, tz) >= v
    return d >= v


def since_bound(since, tz=None):
    """--since の値を、範囲に含む最初の時刻（UTC）にする。日付なら、その日の現地の 0 時。"""
    if since is None:
        return None
    kind, v = since
    if kind == "date":
        d = datetime.datetime.strptime(v, "%Y-%m-%d")
        return (d.replace(tzinfo=tz) if tz else d.astimezone()).astimezone(UTC)
    # 発言の時刻は秒までなので、秒の端数は繰り上げても含む発言は変わらず、ts_str で切り捨てても下限がずれない
    return v.replace(microsecond=0) + datetime.timedelta(seconds=1) if v.microsecond else v


# ---- 対話の md ------------------------------------------------------------

HEAD = re.compile(r"^## ([UQBE]\d+)（([^）]*)）([^\n]*)$", re.M)
# セッション ID は、Claude Code の記録では 16 進の文字列で、変換した md では英数字と . _ - からなる名前でよい
SID8 = r"[0-9A-Za-z._-]{8}"
DUP = re.compile(r"重複: (%s) ([UQB]\d+)" % SID8)
EV_RECORD = re.compile(r"(?:review|scene)-(%s)-([UQBE]\d+)-\d+(?:#p\d+)?" % SID8)
EV_TURN = re.compile(r"(%s)\s+([UQBE]\d+)" % SID8)


def evidence_ref(e):
    """候補の evidence の 1 つを (ID8, 発言の番号) にする。記録の id か「<ID8> <番号>」の形でなければ None。"""
    e = (e or "").strip()
    m = EV_RECORD.fullmatch(e) or EV_TURN.fullmatch(e)
    return (m.group(1), m.group(2)) if m else None


def evidence_tokens(e):
    """evidence の 1 つを、- と空白と # で区切った語の一覧にする（セッションの ID の先頭で振り分けるため）。"""
    return [t for t in re.split(r"[-\s#]+", e or "") if t]


def parse_dialog(text, tz=None):
    """対話の md を読み、{発言の番号: 辞書} を、記録の順に返す。

    辞書の欄: ts（UTC の datetime）、ts_raw、date（見出しの現地の日付。無ければ tz で計算）、
    dup（重複の相手「ID8 発言」か空）、prev（直前の応答）、user（利用者の発言）、order。
    """
    turns = {}
    parts = HEAD.split(text)
    for i in range(1, len(parts), 4):
        t, inner, rest, body = parts[i], parts[i + 1], parts[i + 2], parts[i + 3]
        bits = inner.split("｜")
        d = parse_ts(bits[0])
        date = bits[1].strip() if len(bits) > 1 else local_date(d, tz)
        m = re.split(r"^### ユーザーの発言[ \t]*$", body, flags=re.M)
        prev = re.sub(r"^\s*### 直前の応答[ \t]*\n?", "", m[0]).strip()
        user = m[1].strip() if len(m) > 1 else ""
        if user == "（なし）":
            user = ""
        if prev == "（なし）":
            prev = ""
        dm = DUP.search(rest)
        turns[t] = {"ts": d, "ts_raw": bits[0].strip(), "date": date,
                    "dup": ("%s %s" % (dm.group(1), dm.group(2))) if dm else "",
                    "prev": prev, "user": user, "order": len(turns)}
    return turns


class Dialogs:
    """dialogs フォルダの対話を、セッション ID の先頭 8 文字で引く。読み込みは必要になった時に行う。"""

    def __init__(self, folder, tz=None):
        self.folder = folder
        self.tz = tz
        self._cache = {}

    def path_of(self, sid):
        sid = (sid or "").strip()
        if not sid or not os.path.isdir(self.folder):
            return None
        for fn in sorted(os.listdir(self.folder)):
            if fn.endswith(".md") and fn.startswith(sid):
                return os.path.join(self.folder, fn)
        return None

    def get(self, sid):
        """(本文, 空白を除いた本文, 書式を除いた本文, 発言の辞書) か None。"""
        sid = (sid or "").strip()[:8]
        if sid not in self._cache:
            p = self.path_of(sid)
            if p is None:
                self._cache[sid] = None
            else:
                text = read_text(p)
                self._cache[sid] = (text, norm(text), loose(text), parse_dialog(text, self.tz))
        return self._cache[sid]

    def turn(self, sid, turn):
        d = self.get(sid)
        return d[3].get((turn or "").strip()) if d else None

    def resolve(self, default_sid, ref):
        """「U012」か「aaaa0001 U012」の形の参照を、(ID8, 発言の番号, 発言の辞書か None) にする。"""
        ref = (ref or "").strip()
        m = re.fullmatch(r"(%s)\s+([UQBE]\d+)" % SID8, ref)
        sid, turn = (m.group(1), m.group(2)) if m else (default_sid, ref)
        return sid, turn, self.turn(sid, turn)


def evidence_time(dlg, evidence):
    """evidence の発言のうち最も早いものの (時刻の文字列, 現地の日付)。対話で引けなければ ("", "")。"""
    best = None
    for e in evidence or []:
        ref = evidence_ref(e)
        t = dlg.turn(ref[0], ref[1]) if ref else None
        if t and t["ts"] and (best is None or t["ts"] < best[0]):
            best = (t["ts"], t["date"])
    return (ts_str(best[0]), best[1]) if best else ("", "")


# ---- 取り込み済みの範囲 ----------------------------------------------------

def load_ledger(pdir):
    return read_json(ledger_path(pdir), {"rounds": []})


def covered_until(ledger, session):
    """設置した回のうち、そのセッションを最も後まで取り込んだ時刻（UTC の datetime）。無ければ None。"""
    best = None
    for r in ledger.get("rounds", []):
        s = (r.get("sessions") or {}).get(session)
        if s:
            d = parse_ts(s.get("end"))
            if d and (best is None or d > best):
                best = d
    return best


def covered_until_sid8(ledger, sid8):
    """covered_until を、セッション ID の先頭 8 文字で引く（試験の単位は ID8 しか持たない）。"""
    best = None
    for r in ledger.get("rounds", []):
        for sid, s in (r.get("sessions") or {}).items():
            d = parse_ts(s.get("end")) if sid[:8] == sid8 else None
            if d and (best is None or d > best):
                best = d
    return best


def load_scope(pdir, name):
    p = os.path.join(round_dir(pdir, name), "scope.json")
    sc = read_json(p)
    if sc is None:
        raise SystemExit("回の範囲が無い: %s（先に extract_dialog.py --round %s を実行する。"
                         "プロジェクトが違うなら --project で指定する）" % (p, name))
    return sc


def scope_tz(scope):
    """範囲を作ったときの時間帯。--tz が無ければ、記録した tz_label（UTC+09:00 の形）から。"""
    if scope.get("tz"):
        return tz_of(scope["tz"])
    m = re.fullmatch(r"UTC([+-]\d{2}:\d{2})", scope.get("tz_label") or "")
    return tz_of(m.group(1)) if m else None


def scope_index(scope):
    """{ID8: (この時刻より後, 終わりの時刻, この時刻から)}。どれも時刻か None。

    3 つ目は --since の下限。since_from の欄が無い古い scope.json では、since と時間帯から求める。
    """
    out = {}
    old = since_bound(parse_until(scope.get("since"), "since"), scope_tz(scope)) if scope.get("since") else None
    for s in scope.get("sessions", []):
        frm = parse_ts(s.get("since_from")) if "since_from" in s else old
        out[s["sid8"]] = (parse_ts(s.get("start_after")), parse_ts(s.get("end")), frm)
    return out


def in_scope(idx, sid, d):
    if sid not in idx or d is None:
        return False
    start, end, frm = idx[sid]
    return (start is None or d > start) and (frm is None or d >= frm) and (end is None or d <= end)
