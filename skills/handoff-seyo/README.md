# handoff-seyo

長い作業を文脈の圧縮や次のセッションに引き継ぐための継続書を書き、再開時に読み直させるスキルです。Claude Code 用のフックを二つ同梱しています。

このファイルは人が読むための説明です。エージェントが読むのは `SKILL.md` です。

## 仕組み

| 部品 | 働き |
|---|---|
| `SKILL.md` | 継続書をいつ、どこに、何を書くか。再開時に何を読むか |
| `scripts/ctx_notice.py` | コンテキストの使用率が閾値（既定 90%）を超えたとき、継続書を書くかを検討するようエージェントに一回だけ知らせる |
| `scripts/post_compact.py` | 文脈の圧縮の直後に、再開の手順をエージェントに知らせる |
| ステータス行のスクリプトへの追記 | 使用率をフックに渡す |

`ctx_notice.py` の知らせは継続書を書くかの検討を求めるだけです。書くかどうかは、エージェントがプロジェクトの規則と利用者の指示に従って決めます。

`post_compact.py` は、最新の継続書の場所と、継続書の「読み直す文書」の見出しの下の行を、書かれたとおりに示します。エージェントは読んだ文書と節を次の返答の最初の行に書きます。利用者はその行を見て、読み飛ばしに気づけます。

## 使用率の得方

フックの入力には使用率が含まれません。`ctx_notice.py` は次の順に使用率を得ます。

1. ステータス行のスクリプトが書いた値を使います。30 分より古い値は使いません。
2. 1 の値が無いか古く、環境変数 `HANDOFF_CTX_WINDOW` に窓のトークン数があれば、記録（transcript）の最後の使用量をその値で割ります。100 万トークンの窓なら `1000000` を設定します。
3. どちらも無ければ、知らせません。記録には窓の大きさが残らないので、窓を推測して割ると誤った使用率を知らせるからです。

知らせは、使用率が閾値を超えると一回だけ出ます。圧縮で使用率が下がると、次に閾値を超えたときにもう一度出ます。サブエージェントの中では出ません。

## 設定

フックは次の三つです。

| イベント | matcher | スクリプト |
|---|---|---|
| PostToolUse | `*` | `ctx_notice.py` |
| UserPromptSubmit | なし | `ctx_notice.py` |
| SessionStart | `compact` | `post_compact.py` |

Claude Code のプラグインとして入れた場合は、リポジトリの `hooks/hooks.json` で三つが自動で有効になります。ほかの方法で入れた場合は `~/.claude/settings.json` に手で登録します。書き方は[導入の手引き](../../docs/install.md)の「handoff-seyo のフックを登録する」にあります。

閾値は環境変数 `HANDOFF_CTX_THRESHOLD` で変えられます。設定の変更は新しく開始したセッションから有効になります。

### ステータス行のスクリプトに足す行

ステータス行のスクリプトは入力の JSON で `context_window.used_percentage` を受け取ります。この値を `<一時フォルダ>/claude-ctx/<session_id>.txt` に書く処理を足します。一時フォルダは Python の `tempfile.gettempdir()` が返すフォルダで、Windows では `%TEMP%` です。PowerShell のスクリプトでは、入力の JSON を読んだ変数（次の例では `$d`）を使って、次の行を足します。

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

作者が動作を確かめたのは、Windows の PowerShell でのこの書き方です。ほかのシェルでも、同じ値を同じ場所に書けば、フックはその値を読みます。

## 削除

削除の手順は[導入の手引き](../../docs/install.md#削除)にあります。ステータス行のスクリプトに足した行は「handoff-seyo」の注釈を目印に消します。

## 動作の確認

模擬の入力で確かめられます。

```
python -X utf8 -I scripts/ctx_notice.py < 入力.json
python -X utf8 -I scripts/post_compact.py < 入力.json
```

入力は `{"session_id": "...", "hook_event_name": "PostToolUse", "cwd": "..."}` の形です。一時フォルダの `claude-ctx/<session_id>.txt` に `91` のような閾値を超える値を置いて `ctx_notice.py` を実行すると、知らせの JSON が出力されます。`post_compact.py` は、`cwd` の近くにある最新の継続書をもとに再開の手順を出力します。
