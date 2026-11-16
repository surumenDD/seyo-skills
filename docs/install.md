# 導入の手引き

seyo-skills の 6 つのスキルを入れる方法は 3 つあります。方法によって、handoff-seyo のフックの扱いと、スキルの一覧に名前だけを出す設定が使えるかが違います。

| 方法 | 入れられるエージェント | handoff-seyo のフック | 名前だけを出す設定 | 確かめたこと |
|---|---|---|---|---|
| 1. フォルダを結ぶ（`tools/link.py`） | スキルのフォルダを読むエージェント（Claude Code、Codex など） | `settings.json` に手で登録する | 使える | 作者の Windows の環境で、このリポジトリの道具を使って動作を確かめた |
| 2. Claude Code のプラグイン | Claude Code | 自動で有効になる | 使えない | 定義のファイルは `claude plugin validate` を通った（アイコンが無いという警告だけ）。プラグインとして導入したときの動きは確かめていない |
| 3. スキルを導入する CLI（`npx skills add`） | この CLI が対応するエージェント | `settings.json` に手で登録する | Claude Code では使える | 確かめていない |

どの方法でも、6 つを同じ置き場に入れてください。slide-seyo と delegate-seyo は、同じ親フォルダにある ronjutsu-seyo2 と buntai-seyo を読み、2 つの層は同じ親フォルダの chosei-seyo のスクリプトで図鑑の場所を知ります。2 つの層が無い置き場では、作業のスキルは手順と検査だけで働き、chosei-seyo が無い置き場では、図鑑を読まずに層だけで書きます。

## 前提

| 使うもの | 要るもの |
|---|---|
| すべてのスクリプト（`tools/` と各スキルの `scripts/`） | Python 3。標準ライブラリだけで動きます |
| slide-seyo の PDF の検査 | Python 3.9 以上と、`skills/slide-seyo/scripts/requirements.txt` のパッケージ（pypdfium2、numpy） |
| slide-seyo の HTML の検査 | Node と、`skills/slide-seyo/scripts` での `npm install`、Chrome か Edge |
| slide-seyo の .pptx の PDF への書き出し（`export_pdf.ps1`） | Windows と PowerPoint |
| handoff-seyo のフック | Claude Code と、`python` というコマンドで起動する Python 3 |
| handoff-seyo の使用率の正確な値 | ステータス行のスクリプトへの追記（[handoff-seyo の README](../skills/handoff-seyo/README.md)） |

ほかの OS での状況は次のとおりです。

- slide-seyo の HTML と PDF の検査は、Python、Node、Playwright が動く OS で動くように書かれています（HTML の検査は、macOS と Linux のブラウザの置き場も探します）。作者が動作を確かめたのは Windows です。
- slide-seyo の .pptx の書き出しは、Windows の PowerPoint だけで動きます。ほかの環境では、使っているツール（PowerPoint、Keynote、Google スライド）で PDF に保存し、その PDF をエージェントに渡します。LaTeX のスライドは、組版した PDF をそのまま渡します。
- handoff-seyo のステータス行への追記の例は PowerShell で書いてあります。ほかのシェルでも、同じ値を同じ場所に書けば、フックはそれを読みます（書き方は handoff-seyo の README）。追記が無い場合、フックは記録（transcript）の最後の使用量から使用率を概算し、知らせの文に概算だと書きます。

### `python` が無く `python3` だけがある環境

handoff-seyo のフックは、`python` というコマンドを、シェルを通さずに直接起動します（プラグインの `hooks/hooks.json` も、方法 1 と方法 3 で登録する例も同じです）。macOS や Linux には、`python3` だけがあり `python` が無い環境があります。その環境ではフックが起動せず、使用率が高いときの知らせも、文脈の圧縮の直後の手順の知らせも出ません。起動できないフックは、Claude Code の文書では処理を止めない失敗として扱われ、作業はそのまま進み、記録にフックの失敗が表示されることがあります（macOS と Linux の実機では試していません）。

`python --version` が失敗する環境では、次のどちらかで対処します。

- `python` というコマンドで Python 3 が起動するようにする。Ubuntu 24.04 では、`python-is-python3` パッケージが `/usr/bin/python` を `python3` に結びます。ほかの環境では、`python3` へのシンボリックリンクを `python` という名前で、PATH の通ったフォルダに置きます。プラグインで入れた場合は、`hooks/hooks.json` を書き換えずに済むこちらを使います。
- 方法 1 と方法 3 では、`settings.json` に登録するフックの `"command"` を `"python3"` にします。

この手引きと README のコマンドの `python` も、その環境では `python3` に読み替えます。

## 1. フォルダを結ぶ

リポジトリを clone し、`tools/link.py` で、`skills/` の下の各スキルのフォルダをエージェントのスキルの置き場に結びます。Windows ではジャンクション（管理者の権限が要らない）を、ほかの OS ではシンボリックリンクを作ります。置き場に同じ名前のフォルダがあり、それがこのリポジトリへのリンクでなければ、上書きせずに終わります。

```
git clone <このリポジトリ> seyo-skills
cd seyo-skills
python tools/link.py --dry-run
python tools/link.py
```

| 目的 | コマンド |
|---|---|
| 行うことだけを表示する | `python tools/link.py --dry-run` |
| Claude Code の置き場（`~/.claude/skills`）に結ぶ | `python tools/link.py` |
| 別の置き場に結ぶ | `python tools/link.py --to ~/.agents/skills` |
| 一部だけを結ぶ | `python tools/link.py --only slide-seyo ronjutsu-seyo2 buntai-seyo chosei-seyo` |
| このリポジトリへのリンクだけを外す | `python tools/link.py --unlink` |

リンクなので、リポジトリで `git pull` を実行すれば、結んだ先にも反映されます。

### エージェントごとの置き場

利用者の全プロジェクトで使う置き場は、各エージェントの文書（2026-11-16 時点）では次のとおりです。複数のエージェントで使うときは、`--to` を変えて置き場ごとに実行します。

| エージェント | 置き場 | 備考 |
|---|---|---|
| Claude Code | `~/.claude/skills` | `link.py` の既定 |
| Codex | `~/.agents/skills` | 互換のために `~/.codex/skills` も読みます |
| Cursor | `~/.agents/skills`、`~/.cursor/skills` | `~/.claude/skills` と `~/.codex/skills` も読みます |
| GitHub Copilot | `~/.copilot/skills`、`~/.agents/skills` | |

handoff-seyo のフックは Claude Code のもので、ほかのエージェントでは働きません。フックが無い環境では、handoff-seyo は、長い作業の区切りごとにエージェントの判断で継続書を書きます。chosei-seyo が会話を読み取れる記録の形式は Claude Code のセッションの jsonl で、Codex の記録は、形式を直せば使えます（`skills/chosei-seyo/references/data.md` の 2 節）。

### handoff-seyo のフックを登録する

Claude Code の `~/.claude/settings.json` の `hooks` に、次の 3 つを足します。`<置き場>` は、スキルを結んだフォルダの絶対パス（Windows なら `C:/Users/<名前>/.claude/skills`、macOS なら `/Users/<名前>/.claude/skills`）に置き換えます。この形ではシェルを通さないので、`~` は使えません。`hooks` にすでにほかの項目があれば、同じイベントの配列に足します。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["-X", "utf8", "-I", "<置き場>/handoff-seyo/scripts/post_compact.py"],
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["-X", "utf8", "-I", "<置き場>/handoff-seyo/scripts/ctx_notice.py"],
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["-X", "utf8", "-I", "<置き場>/handoff-seyo/scripts/ctx_notice.py"],
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

設定は、新しく開始したセッションから有効になります。閾値（既定 90%）は、環境変数 `HANDOFF_CTX_THRESHOLD` で変えられます。使用率の正確な値をフックに渡すための、ステータス行のスクリプトへの追記は、[handoff-seyo の README](../skills/handoff-seyo/README.md) にあります。

### スキルの一覧に名前だけを出す

Claude Code では、`settings.json` の `skillOverrides` で、スキルごとに、エージェントに見せる範囲を決められます。`"name-only"` にすると、エージェントには名前だけが見え、説明文は見えません。名前を出して頼んだときだけスキルを使わせたい場合に設定します。

```json
{
  "skillOverrides": {
    "slide-seyo": "name-only",
    "delegate-seyo": "name-only",
    "handoff-seyo": "name-only",
    "ronjutsu-seyo2": "name-only",
    "buntai-seyo": "name-only",
    "chosei-seyo": "name-only"
  }
}
```

値は、`"on"`（既定。名前と説明文を見せる）、`"name-only"`、`"user-invocable-only"`（エージェントからは隠し、利用者だけが呼べる）、`"off"`（隠す）の 4 つです。プラグインで入れたスキルには、この設定は効きません。

## 2. Claude Code のプラグイン

Claude Code のセッションで、マーケットプレイスを足してから、プラグインを入れます。

```
/plugin marketplace add <このリポジトリ>
/plugin install seyo@seyo-skills
```

`/plugin install` は、すぐには入れずに、プラグインの詳細の画面を開きます。そこで入れる範囲（既定は利用者の全プロジェクト）を選びます。シェルからは `claude plugin marketplace add <このリポジトリ>` と `claude plugin install seyo@seyo-skills` で同じことができます。

プラグインで入れると、次の点が方法 1 と違います。

- スキルの名前が `seyo:<名前>` の形になり、スラッシュで呼ぶときは `/seyo:slide-seyo` と入力します。
- handoff-seyo のフックは、リポジトリの `hooks/hooks.json` で自動で有効になります。ステータス行への追記は、プラグインでは入らないので、手で足します。
- `skillOverrides` が効かないので、名前だけを出す設定は使えません。

### フォルダを結ぶ方法から移る

方法 1 で入れていた場合は、プラグインを入れる前に、次の 2 つを外します。

1. `python tools/link.py --unlink` で、結んだフォルダを外す。プラグインのスキルは `seyo:<名前>` という別の名前で入るので、結んだフォルダが残ると、同じスキルが 2 つ読み込まれます。
2. `settings.json` に手で登録した handoff-seyo のフックを外す。プラグインのフックと手で登録したフックの両方があると、同じフックが二重に動きます。

`skillOverrides` の 6 行は、プラグインのスキルには効かないので、消してかまいません。

## 3. スキルを導入する CLI

[skills](https://github.com/vercel-labs/skills)（`npx skills`）は、リポジトリの `skills/<名前>/SKILL.md` を探して、指定したエージェントの置き場に入れる CLI です。このリポジトリはその構成なので、対応しているはずですが、確かめていません。

```
npx skills add <このリポジトリ> --skill '*' -g -a claude-code
```

`-g` は利用者の全プロジェクトの置き場に、付けなければいまのプロジェクトの置き場に入れます。`-a` で対象のエージェント（`claude-code`、`codex` など）を選びます。この CLI の文書と実装（2026-11-16 時点）では、スキルの本体を `~/.agents/skills` に置き、各エージェントの置き場からそこへリンクを張ります。リンクを作れないときは写します。

handoff-seyo のフックは、方法 1 と同じく `settings.json` に手で登録します。`<置き場>` には、Claude Code の置き場（`~/.claude/skills`）の絶対パスを使います。

## 動作の確認

リポジトリで検査を実行します。

```
python tools/check.py
```

次の 6 つの項目を調べ、問題があれば内容を表示して、終了コード 1 で終わります。

| 項目 | 内容 |
|---|---|
| 1 frontmatter | 各スキルの `SKILL.md` の name と description |
| 2 参照 | `SKILL.md` と `README.md` が名前を挙げた `references/` と `scripts/` のファイルがあるか |
| 3 自己テスト | 各スキルの自己テスト。slide-seyo の 2 つは、依存のパッケージ（PDF は pypdfium2 と numpy、HTML は Node と `scripts/node_modules`）が無ければ飛ばし、その旨を表示します |
| 4 入れないもの | `__pycache__`、`.venv`、`node_modules`、`*.pyc` が git の対象になっていないか |
| 5 環境のパス | 利用者の名前を含む絶対パスが書かれていないか |
| 6 非公開の語 | 環境変数 `SEYO_FORBIDDEN` か `~/.seyo/forbidden.txt` に書いた語が含まれていないか。一覧が無ければ飛ばします |

自己テストを飛ばすときは `--skip-selftest` を付けます。

handoff-seyo のフックは、模擬の入力で確かめられます（[handoff-seyo の README](../skills/handoff-seyo/README.md) の「動作の確認」）。Claude Code では、新しいセッションで `/` を入力し、スキルの一覧に 6 つの名前（プラグインでは `seyo:<名前>`）が出るかを見ます。

## 更新

| 方法 | 更新のしかた |
|---|---|
| 1. フォルダを結ぶ | リポジトリで `git pull` を実行する |
| 2. プラグイン | `/plugin marketplace update seyo-skills` でマーケットプレイスを更新し、`/plugin` の Installed の画面で更新する。シェルからは `claude plugin update seyo@seyo-skills` |
| 3. CLI | `npx skills update -g` |

`skills/slide-seyo/scripts/package.json` か `requirements.txt` が変わったときは、依存のパッケージを入れ直します（slide-seyo の README の「依存パッケージのインストール」）。

## 削除

| 方法 | 削除のしかた |
|---|---|
| 1. フォルダを結ぶ | `python tools/link.py --unlink` でリンクを外し、`settings.json` に足した handoff-seyo のフックと `skillOverrides` の行を消してから、リポジトリを削除する |
| 2. プラグイン | `/plugin uninstall seyo@seyo-skills` のあと、`/plugin marketplace remove seyo-skills` |
| 3. CLI | `npx skills remove -g slide-seyo delegate-seyo handoff-seyo ronjutsu-seyo2 buntai-seyo chosei-seyo` で外し、`settings.json` に足したフックを消す |

どの方法でも、ステータス行のスクリプトに足した handoff-seyo の行は手で消します。chosei-seyo が作った図鑑と記録は `~/.seyo/` に残るので、不要なら削除します。
