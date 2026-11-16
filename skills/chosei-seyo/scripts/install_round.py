"""照合を通った回の候補を、プロジェクトの控え（entries.jsonl）に足し、台帳に回を「登録を待つ回」として記録する。

控えは取り出した指摘の全部で、照合と復元に使う。文章を書くエージェントは読まない。図鑑（genten.md）への登録は、
このあと登録の担当が行い（references/brief_register.md）、check_genten.py が照合して置く。
控えに足すのは利用者の確認を待たずに行う。指摘を足すだけで、前の項目を削らないからである。
- 候補は check_candidates.py と同じ照合を通す（入力 in_NN.md の無い回は、対話から直接に取り出した回として照合する）。
  問題が 1 件でもあれば足さない。
- 項目の id は「<回>:<候補の id>」にする。候補の id は回の中でしか一意でないからである。
- 同じ回は 2 度足さない（台帳か控えにその回があれば断る）。
- 検証の試験の回（extract_dialog.py --exam で作った回）は足さない。採点に使う時期の指摘が図鑑に入ると、
  その時期の試験が未見でなくなるからである。
- 項目の時刻は、根拠の発言のうち最も早いものの時刻である。

使い方: python install_round.py --round <回> [--home <フォルダ>] [--project <キー>] [--cwd <作業フォルダ>]
        python install_round.py --remove <回>
            回の項目を控えから外し、図鑑からその回の引用と注を外し（空になった見出しも外す）、台帳から回を消す。
            取り消した回の範囲の発言は取り込み済みでなくなり、次の回の範囲に入る。共通に写した項目は残る。
"""
import argparse
import os
import sys

sys.dont_write_bytecode = True  # スキルのフォルダに、この PC のパスを含む .pyc を残さない
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seyo_common as C  # noqa: E402
import check_candidates  # noqa: E402
import check_genten as G  # noqa: E402

FIELDS = ("doc_kind", "applies_to", "user_words", "general", "general_words", "ai_pattern", "limits", "judgment",
          "evidence", "n_utterances", "strength", "note")


def to_entry(rnd, cand, time="", date=""):
    e = {"id": "%s:%s" % (rnd, cand.get("id")), "round": rnd, "source_id": cand.get("id"), "time": time, "date": date}
    for k in FIELDS:
        e[k] = cand.get(k, "" if k not in ("user_words", "evidence") else [])
    e["installed_at"] = C.now_str()
    return e


def installed_already(pdir, rnd):
    ledger = C.load_ledger(pdir)
    return (any(r.get("name") == rnd for r in ledger.get("rounds", []))
            or any(e.get("round") == rnd for e in C.load_entries(pdir)))


def record_round(pdir, entry):
    ledger = C.load_ledger(pdir)
    ledger["rounds"] = [r for r in ledger.get("rounds", []) if r.get("name") != entry["name"]] + [entry]
    C.write_json(C.ledger_path(pdir), ledger)


def next_step(rnd):
    return ("次は図鑑への登録: python scripts/check_genten.py --round %s --start で案を作り、登録の担当に "
            "references/brief_register.md で案に足させ、python scripts/check_genten.py --round %s --install で照合して置く"
            % (rnd, rnd))


def install(home, key, pdir, rnd):
    scope = C.load_scope(pdir, rnd)
    if scope.get("purpose") == "exam":
        print("回 %s は検証の試験の回なので設置しない。採点に使う時期の指摘が図鑑に入ると、その時期の試験が未見でなくなる" % rnd)
        return 1
    if installed_already(pdir, rnd):
        print("回 %s は設置済み。同じ回を 2 度足さない（入れ直すなら --remove %s で外してから設置する）" % (rnd, rnd))
        return 1
    direct = check_candidates.is_direct(pdir, rnd)
    res = check_candidates.check(pdir, rnd, from_dialogs=direct)
    for ln in res["lines"]:
        print(ln)
    if res["bad"]:
        print("候補の照合で問題 %d 件。設置しない" % len(res["bad"]))
        for x in res["bad"][:40]:
            print("  ", x)
        return 1
    if not res["rows"]:
        print("回 %s に候補が無い。設置しない" % rnd)
        return 1
    dlg = C.Dialogs(C.dialogs_dir(pdir), C.scope_tz(scope))
    new = []
    for cand in res["rows"]:
        t, d = C.evidence_time(dlg, cand.get("evidence"))
        new.append(to_entry(rnd, cand, t, d))
    rows = C.load_entries(pdir)
    have = {e.get("id") for e in rows}
    clash = [e["id"] for e in new if e["id"] in have]
    if clash:
        print("控えに同じ id がある: %s。設置しない" % "、".join(clash[:10]))
        return 1
    C.ensure_project(home, key)
    C.save_entries(pdir, rows + new)
    record_round(pdir, {"name": rnd, "kind": "取り込み", "installed_at": C.now_str(), "entries": len(new),
                        "genten": "未登録", "since": scope.get("since", ""), "until": scope.get("until", ""),
                        "until_turn": scope.get("until_turn", ""),
                        "sessions": {s["session"]: {"start_after": s.get("start_after", ""), "end": s["end"],
                                                    "last_turn": s.get("last_turn", "")}
                                     for s in scope.get("sessions", [])}})
    kinds = {}
    for e in new:
        kinds[e["doc_kind"]] = kinds.get(e["doc_kind"], 0) + 1
    print("控えに足した: 回 %s の項目 %d（%s）をプロジェクト %s の控えに足した（全 %d 項目）。回は登録を待つ"
          % (rnd, len(new), "、".join("%s %d" % (k, kinds[k]) for k in C.KINDS if k in kinds), key, len(rows) + len(new)))
    print(next_step(rnd))
    return 0


def remove(home, key, pdir, rnd):
    rows = C.load_entries(pdir)
    ledger = C.load_ledger(pdir)
    keep = [e for e in rows if e.get("round") != rnd]
    gone = {e.get("id") for e in rows if e.get("round") == rnd}
    in_ledger = any(r.get("name") == rnd for r in ledger.get("rounds", []))
    if len(keep) == len(rows) and not in_ledger:
        print("回 %s は、プロジェクト %s に設置されていない" % (rnd, key))
        return 1
    gp = C.genten_path(pdir)
    n_quotes = 0
    if os.path.isfile(gp):
        text, n_quotes = G.remove_ids(C.read_text(gp), gone)
        if n_quotes:
            before = G.next_free(os.path.join(C.round_dir(pdir, rnd), "genten.before_remove.md"))
            C.write_text(before, C.read_text(gp))
            C.write_text(gp, text)
    C.save_entries(pdir, keep)
    ledger["rounds"] = [r for r in ledger.get("rounds", []) if r.get("name") != rnd]
    C.write_json(C.ledger_path(pdir), ledger)
    print("取り消した: 回 %s の項目 %d を控えから、引用 %d を図鑑から外し、台帳から回を消した"
          % (rnd, len(rows) - len(keep), n_quotes))
    copied = [e.get("id") for e in C.load_entries(C.common_dir(home))
              if (e.get("origin") or {}).get("project") == key and (e.get("origin") or {}).get("id") in gone]
    if copied:
        print("共通に写した項目 %d は残した。外すなら: python scripts/common_entries.py remove %s"
              % (len(copied), " ".join(copied)))
    return 0


def main():
    C.setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--round")
    g.add_argument("--remove")
    C.add_place_args(ap)
    a = ap.parse_args()
    home, key, pdir = C.place(a)
    sys.exit(install(home, key, pdir, a.round) if a.round else remove(home, key, pdir, a.remove))


if __name__ == "__main__":
    main()
