# Deckline auto-adjust (AnkiWeb版)

締切までに新規カードを終わらせるよう、「1日の新規カード数」を毎日自動計算して
AnkiWebに反映するツールです。GitHub Actionsで動くので、PCもiPadも開かずに
バックグラウンドで動作します。iPadからは `settings.html` という設定画面で
デッキ・締切日・進め方(均等/順番)を操作します。

## ⚠️ 最初に必ず読んでください

- これはAnki公式の「アドオン」ではなく、`anki`パッケージの内部APIを直接呼び出す
  非公式なスクリプトです。Ankiのアップデートで動かなくなる可能性があります。
- **初回はスケジュール実行ではなく、手動実行(下記手順)で必ず動作確認してください。**
- 心配な場合は、事前にAnkiデスクトップ版の「ファイル > エクスポート」で
  コレクションのバックアップ(.colpkg)を取っておくと安心です。
- パスワードやトークンはGitHubの「Secrets」やブラウザのlocalStorageに保存します。
  他人と共有する端末では使わないでください。

## できること

- **均等モード**: デッキごとに個別の締切を設定し、複数デッキを並行して
  それぞれの締切に間に合うペースで進める
- **順番モード**: 複数デッキで1つの締切を共有し、上から順番に1つずつ
  終わらせる(1つ目が終わったら自動的に2つ目に新規カード予算が回る)
- iPadから `settings.html` を開いて、デッキ選択・締切日(カレンダー)・
  進め方をタップだけで設定・保存

## セットアップ手順

### 1. GitHubアカウントを作る(すでにあればスキップ)
https://github.com/ で無料登録。

### 2. このフォルダの中身でリポジトリを作る
- GitHubで新規リポジトリを作成(**Private推奨**)
- このフォルダ一式をアップロード:
  `adjust_deck_limits.py` / `config.json` / `requirements.txt` /
  `settings.html` / `.github/workflows/deckline.yml`

### 3. AnkiWebのログイン情報をSecretsに登録
リポジトリの Settings → Secrets and variables → Actions → New repository secret
- `ANKIWEB_USERNAME` : AnkiWebのメールアドレス
- `ANKIWEB_PASSWORD` : AnkiWebのパスワード

### 4. 初回の手動実行(デッキ一覧を取得するため)
リポジトリの Actions タブ → 「Deckline auto-adjust」→ Run workflow
これで `decks.json` が生成され、リポジトリに自動コミットされます。
ログに `[deckline]` から始まる行が出るので、エラーが出ていないか確認してください。

### 5. Personal Access Token(設定画面用)を作る
設定画面(`settings.html`)がリポジトリの config.json を読み書きするために必要です。
- GitHub右上のアイコン → Settings → Developer settings →
  Personal access tokens → Fine-grained tokens → Generate new token
- Repository access: 該当リポジトリのみを選択
- Permissions: **Contents = Read and write** のみ付与(他は不要)
- 発行されたトークン(`github_pat_...`)をメモしておく

### 6. settings.html を開けるようにする(GitHub Pages)
- リポジトリの Settings → Pages → Source を「Deploy from a branch」に設定し、
  ブランチを選んで保存
- 数分後、`https://(あなたのユーザー名).github.io/(リポジトリ名)/settings.html`
  でアクセスできるようになります
- iPadのSafariでこのURLを開き、「共有」→「ホーム画面に追加」しておくと
  アプリのように使えます

### 7. settings.html で接続設定
初回起動時に設定シートが開くので、以下を入力:
- GitHubユーザー名 / Organization
- リポジトリ名
- 手順5で作ったPersonal Access Token

入力後は自動でデッキ一覧・設定を読み込みます。

### 8. デッキと締切を設定して保存
- 「満遍なく進める」タブ: デッキと締切日をデッキごとに追加
- 「1つずつ終わらせる」タブ: グループを作り、共有の締切日と
  デッキの順番(↑↓で並べ替え可能)を設定
- 「保存してAnkiWebに反映」を押すと `config.json` がリポジトリに保存されます

### 9. 反映を確認
保存しただけでは即座には反映されません。次のいずれかのタイミングで
GitHub Actionsが実行され、AnkiWebに反映されます:
- 毎日決まった時刻の自動実行(`.github/workflows/deckline.yml` の `cron`)
- Actionsタブから手動実行(すぐ反映したい時はこちら)

反映後、iPadでAnkiMobileを開いて同期すれば、新規カード数の上限が変わっています。

## 計算ロジック

**均等モード**
```
1日の新規カード数 = 切り上げ(残りの新規カード数 ÷ 残り日数)
```

**順番モード**
```
今日の総予算 = 切り上げ(グループ内の合計残り枚数 ÷ 残り日数)
→ 順番が早いデッキから、この予算を使い切るまで割り当てる
  (1つ目のデッキの残りが予算より少なければ、余りは2つ目のデッキに回る)
```

毎日再計算するので、サボった日があれば翌日以降の枚数が自動で増えます。

## よくあるつまずき

- デッキ名は完全一致が必要です(設定画面はプルダウンなので基本問題なし)。
- 初回実行はコレクション全体をダウンロードするため少し時間がかかります。
- `decks.json` が古いと感じたら、Actionsタブから手動実行してください。
- 同時にiPadで学習中に自動実行が走ると同期がぶつかる可能性があるため、
  cronの時刻は自分が使わなさそうな時間帯(深夜など)にしておくと安全です。
- Personal Access Tokenは絶対に公開リポジトリのコードに書き込まないでください
  (設定画面はブラウザのlocalStorageにのみ保存し、リポジトリには保存しません)。
