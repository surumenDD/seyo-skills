# slide-seyo

AI エージェントに発表用のスライドを作成させるためのスキルです。見出しに主張を書き、主張ごとに証拠の示し方を選び、描画した結果を検査スクリプトで測定してから仕上げます。

このファイルは人が読むための説明で、エージェントが読むのは `SKILL.md` です。

## 確認の状況（2026-11-16）

- 文章の方針（何をどの順で主張するか、語と文をどう選ぶか）は、論述の層（ronjutsu-seyo）と文体の層（buntai-seyo）の 2 つのスキルにあります。このスキルが持つのは、作業の手順、検査の道具、スライドという形に固有の基準（証拠の示し方、デザインの設定値、避ける見た目）です。
- 可読性の基準と検査スクリプトは、自己テストと実際のデッキで動作を確認してあります。
- 方針を層に分けたあとの版では、まだスライドを作成していません。

## 使い方

名前を出して依頼します。

```
/slide-seyo この README をもとに、ゼミ用に 5 枚で紹介スライドを作って
```

```
slide-seyo で、来月の学会発表（15 分）のスライドを作りたい。段階ごとに承認を待って進めて
```

```
/slide-seyo この deck.pdf を点検して
```

## 進み方

| 依頼の仕方 | エージェントの進み方 |
|---|---|
| 指定なし | 構成案（前提と、見出しの一覧）を提示し、そのまま全スライドを作成して検査します。前提が違っていたら、構成案の時点で止めてください |
| 承認を待つよう指定する | 構成、話す内容、図の見本を順に確認し、承認のあとに作成します（`references/stages.md`） |
| 構成案は不要と伝える | 構成案を提示せずに作成します |

依頼の内容だけでは構成を決められない場合、エージェントは作成を始める前に、任せてよいか、相談したい点があるかを尋ねます。

## 検査する項目

| 項目 | 既定 |
|---|---|
| 本文の文字サイズ | 18 pt 以上（高さ 7.5 in のスライドでの値） |
| 補足（出典、ページ番号）の文字サイズ | 12 pt 以上 |
| 文字と背景のコントラスト比 | 7:1 以上。大きい文字と補足は 4.5:1 以上 |
| スライドからのはみ出し、文字どうしの重なり | ない |
| 見出しの行数 | 2 行まで |

閾値は変更でき、エージェントが変更したときは、構成案と報告に理由を書きます。

検査スクリプトが測定するのは、文字として描画されたものだけです。画像になっている図の中の文字、配置のバランス、図が主張を示しているかは、エージェントが描画した画像を見て確認します。

## 対応している形式

| 形式 | 検査の方法 |
|---|---|
| HTML のスライド | ブラウザで描画して測定します（`scripts/check_html.mjs`） |
| .pptx、Keynote、Google スライド、LaTeX のスライド | PDF に書き出して、その PDF を測定します（`scripts/check_pdf.py`） |

.pptx は、Windows に PowerPoint がインストールされていれば、エージェントが PowerPoint に PDF を書き出させます。それ以外の環境では、使っているツールで PDF として保存して、エージェントに渡してください。

## プロジェクトの決まりを優先させる

プロジェクトに決まりがあれば、エージェントはこのスキルの既定より優先します。次のどれかに書いておくか、依頼のときにファイルを指定してください。

- `CLAUDE.md`、`AGENTS.md`
- `docs/` の下の、スライドの決定や用語の文書
- 会場や所属のテンプレート

## 一緒に使うスキル

- 論述の層（ronjutsu-seyo）、文体の層（buntai-seyo）: 構成と文言の基準です。エージェントは、構成案を書く前、スライドの文言と発表原稿を書く前、文言を確認するときに読みます
- imozuru: 図の描き方や発表の形式についての、文献と実例の調査。このリポジトリには含まれない別のスキルです
- delegate-seyo: 別のエージェントへの委譲と、結果の確認
- yomiyasu: 検査スクリプトだけを、日本語の文言の機械的な点検に任意で使います。本文は読み込みません。このリポジトリには含まれない別のスキルです

## 依存パッケージのインストール

検査スクリプトを使うには、初回だけ準備が必要です。どちらも、このフォルダで実行します。

HTML の検査（Node と、Chrome か Edge が必要です）

```
cd scripts
npm install
node check_html.mjs --selftest
```

PDF の検査（Python 3.9 以上が必要です）

```
python -m venv scripts/.venv
scripts\.venv\Scripts\python -m pip install -r scripts/requirements.txt
scripts\.venv\Scripts\python scripts/check_pdf.py --selftest
```

上は Windows のパスです。macOS と Linux では `scripts\.venv\Scripts\python` を `scripts/.venv/bin/python` にします。

インストールしたパッケージは、このフォルダの `scripts/node_modules/` と `scripts/.venv/` に入ります（作者の環境では合計約 92 MB）。

## 起動の設定

名前を出して頼んだときだけ使われるよう、説明文で指定しています。Claude Code では、`settings.json` の `skillOverrides` で `name-only` にすると、説明文も一覧から外れます。プラグインで入れた場合、この設定は効きません。

```json
{ "skillOverrides": { "slide-seyo": "name-only" } }
```

## アンインストール

スキルの削除は、入れた方法ごとに、リポジトリの `docs/install.md` の「削除」の手順で行います。`settings.json` の `skillOverrides` に `"slide-seyo"` の行を足していれば、それも削除します。依存パッケージは、このフォルダと一緒に消えます。

削除せずに無効化する場合は、`skillOverrides` の値を `"off"` にします。

## ファイルの構成

```
slide-seyo/
├─ SKILL.md                  エージェントが読む本体
├─ README.md                 このファイル
├─ references/
│  ├─ structure.md           構成の原則（スライドに固有のもの）
│  ├─ patterns.md            避けるデザインのパターンと、修正前後の例
│  ├─ wording-ja.md          文言と発表原稿（日本語。スライドに固有のもの）
│  ├─ wording-en.md          文言と発表原稿（英語。英語に固有のもの）
│  ├─ theme.md               デザインの設定値と、差し替え方
│  ├─ legibility.md          可読性の基準と、その理由
│  ├─ stages.md              段階ごとに承認を待つ場合の手順
│  ├─ backend-html.md        HTML で作成するとき
│  ├─ backend-pdf.md         .pptx など、PDF に書き出せる形式で作成するとき
│  └─ sources.md             根拠にした文献の一覧
└─ scripts/
   ├─ check_html.mjs         HTML の検査
   ├─ inpage_measure.js      ページの中で動く測定と判定
   ├─ package.json
   ├─ check_pdf.py           PDF の検査
   ├─ export_pdf.ps1         PowerPoint に PDF を書き出させる（Windows）
   └─ requirements.txt
```
