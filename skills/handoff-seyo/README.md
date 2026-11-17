# handoff-seyo

長い作業を、文脈の圧縮や次のセッションに引き継ぐための継続書を作成し、再開時に読み直すスキルです。Claude Code 用のフックを 2 つ同梱しています。

このファイルは人が読むための説明です。エージェントが読むのは `SKILL.md` です。

## 仕組み

| 部品 | 働き |
|---|---|
| `SKILL.md` | 継続書をいつ、どこに、何を書くか。再開時に何を読むか |
| `scripts/ctx_notice.py` | コンテキストの使用率が閾値（既定 90%）を超えたとき、継続書を検討するようエージェントに 1 回だけ知らせる |
| `scripts/post_compact.py` | 文脈の圧縮の直後に、再開の手順をエージェントに知らせる。最新の継続書の場所と、その「読み直す文書」の見出しの下の行をそのまま示し、読んだ文書を次の返答の最初の行に書かせる |
| ステータス行のスクリプトへの追記 | 使用率をフックに渡す |

フックの入力には使用率が含まれません。そのため、ステータス行のスクリプトが受け取った使用率を一時フォルダの `claude-ctx/<セッション ID>.txt` に書き、フックがそれを読みます。この値がない環境では、環境変数 `HANDOFF_CTX_WINDOW` に窓のトークン数（100 万トークンの窓なら `1000000`）を設定します。フックは、記録（transcript）の最後の使用量をその値で割ります。どちらも無ければ、フックは知らせません。記録には窓の大きさが残らないので、推測した窓で割ると誤った使用率を知らせてしまうからです。

知らせは、使用率が上がるたびに 1 回だけ出ます。圧縮で使用率が下がると、次の上昇でもう一度出ます。サブエージェントの中では出ません。

## 設定

フックは次の 3 つです。

| イベント | matcher | スクリプト |
|---|---|---|
| PostToolUse | `*` | `ctx_notice.py` |
| UserPromptSubmit | なし | `ctx_notice.py` |
| SessionStart | `compact` | `post_compact.py` |

Claude Code のプラグインとして入れた場合は、リポジトリの `hooks/hooks.json` で 3 つが自動で有効になり、登録は要りません。

スキルのフォルダを `~/.claude/skills` などに置いた場合（スキルを導入する CLI で入れた場合と、リポジトリを clone して結んだ場合）は、`~/.claude/settings.json` の `hooks` に 3 つを登録します。書き方は、リポジトリの `docs/install.md` の「handoff-seyo のフックを登録する」にあります。フックは `python` というコマンドで起動するので、`python3` だけがある環境では、同じ手引きの前提の節を見てください。この形からプラグインに移るときは、手で登録したフックを外します。両方があると、同じフックが二重に動きます。

使用率をフックに渡すには、ステータス行のスクリプトに、受け取った `context_window.used_percentage` の値を `<一時フォルダ>/claude-ctx/<session_id>.txt` に書く処理を足します。一時フォルダは、フックの Python の `tempfile.gettempdir()` が返すフォルダ（Windows では `%TEMP%`）です。PowerShell のステータス行のスクリプトでは、入力の JSON を読んだ変数（次の例では `$d`）を使って、次の行を足します。

```powershell
# handoff-seyo: 使用率をフックに渡す（失敗しても表示は続ける）
try {
  if ($d.session_id -and $null -ne $d.context_window.used_percentage) {
    $ctxDir = Join-Path ([System.IO.Path]::GetTempPath()) 'claude-ctx'
    if (-not (Test-Path $ctxDir)) { [void](New-Item -ItemType Directory -Path $ctxDir -Force) }
    [System.IO.File]::WriteAllText((Join-Path $ctxDir "$($d.session_id).txt"), "$([math]::Round([double]$d.context_window.used_percentage))")
  }
} catch {}
```

作者が動作を確かめたのは、Windows の PowerShell のこの書き方です。ほかのシェルでも、同じ値を同じ場所に書けば、フックはそれを読みます。この追記が無い場合と、値が 30 分より古い場合は、`HANDOFF_CTX_WINDOW` があれば記録の使用量から計算し、無ければ知らせません。

閾値は環境変数 `HANDOFF_CTX_THRESHOLD` で変更できます。窓のトークン数は `HANDOFF_CTX_WINDOW` で指定します（ステータス行の値がないときだけ使います）。

設定の変更は、新しく開始したセッションから有効になります。

## アンインストール

1. フックを外す。プラグインで入れた場合は、プラグインを削除すればフックも外れます。フォルダを置いた場合は、`~/.claude/settings.json` の `hooks` から、`handoff-seyo` を含む 3 つの項目を削除し、`skillOverrides` に `"handoff-seyo"` の行があれば、それも削除します。
2. ステータス行のスクリプトから、「handoff-seyo」という注釈の付いた行を削除する。
3. スキルを削除する。方法ごとの手順は、リポジトリの `docs/install.md` の「削除」にあります。

## 動作の確認

模擬の入力で確認できます。使用率 91% の値を置いて実行すると、知らせの JSON が出力されます。

```
python -X utf8 -I scripts/ctx_notice.py < 入力.json
python -X utf8 -I scripts/post_compact.py < 入力.json
```

入力は `{"session_id": "...", "hook_event_name": "PostToolUse", "cwd": "..."}` の形です。
