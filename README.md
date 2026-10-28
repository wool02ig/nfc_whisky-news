# NFC ウイスキーニュース v1.0 beta

国内・海外のウイスキーニュースを、Google News RSSから取得して静的HTMLに表示する初期版。

## セットアップ
1. GitHubで**専用の公開リポジトリ**（例: `nfc_whisky-news`）を新規作成。
2. このフォルダの**中身**（`.github` を含む）をリポジトリのルートに配置してpushする。
3. リポジトリ設定の `Settings > Actions > General > Workflow permissions` で `Read and write permissions` が必要な場合は有効化する（組織ポリシー等で制限されている場合あり）。
4. `Actions > Update whisky news > Run workflow` で初回収集を手動起動。`data/news.json` が更新されることを確認。
5. `Settings > Pages > Build and deployment > Deploy from a branch`、branch `main` / `(root)` で公開。
6. 公開URL（`https://<username>.github.io/nfc_whisky-news/` など）をNFCタグに書き込む。

## 更新
- 1日4回（日本時間 00:17, 06:17, 12:17, 18:17）にGitHub Actionsをスケジュール実行。
- GitHub側の都合で遅延・不実行になる場合あり。長期非アクティブ時はscheduleが停止する場合あり。
- 直近30日以内の最大100件。検索言語ごとに日本語と英語を収集。海外ニュースは原文のまま。
- ニュースの公開期間・内容・網羅性はRSSの提供状況によって変わる。

## 運用・制限
- 現状の記事データは空。初回GitHub Actions実行後にニュースが表示される。
- Google News RSSは正式な保証付きAPIではないため、取得に失敗する場合がある。
- 全フィード取得に失敗した際は既存JSONを保持する。
- 取得に成功したフィードがあれば、新旧記事をマージし期限超過記事を削除。
- 記事全文・画像を転載せず、見出し・情報源・リンクのみ掲載。
- 同じ報道の別見出しは完全に統合できない場合あり。
- 地域の区分は検索RSSの日本語版／英語版で判定する暫定仕様。記事の実際の発信国とは一致しない場合がある。
- 実行・通信の動作検証はGitHub環境で行うこと。
