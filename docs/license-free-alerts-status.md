# NVIDIA非依存Alerts経路の作業記録

## 現在の状態

NGCから取得できない`vss-video-analytics-api`の代わりに、
`services/analytics/video-analytics-api-local`を追加した。
Elasticsearchを直接検索し、Alerts UIが使用する`/incidents`を含む
互換エンドポイントを提供する。

## 起動構成

- 待受ポート: `8081`
- HAProxy: `/video-analytics-api/*`を8081へ転送
- Elasticsearch: `ELASTICSEARCH_URL`（既定値`http://127.0.0.1:9200`）
- Compose override: `deploy/docker/developer-profiles/dev-profile-lvs/license-free.override.yml`
- コンテナ: `mdx-vss-video-analytics-api-1`

## 検証済み

- `GET /health` -> `200`
- `GET /incidents?...` -> `200`（UIの一覧取得形式）
- `GET /alerts` -> `200`
- `GET /alerts/severe` -> `200`
- `GET /frames/alerts` -> `200`
- Alerts UIで4件のイベントを表示

## 制約

現在表示される文書はraw event/captionであり、正式なAlert文書でない場合がある。
その場合、重要度・発火ルール・詳細説明などは`N/A`になる。
これは一覧APIの互換性確認を優先した暫定仕様で、判定品質を再現するものではない。

## 再開手順

統合Composeを、`generated.env`と`license-free.override.yml`を指定して起動する。
API単体を手動起動する必要はない。

## 次回のアクションアイテム

1. ローカル設定または動画登録情報を返す`GET /v1/sensor/list`を追加する。
2. `Create Alert Rule`からAlert Bridgeへルールを登録・削除する。
3. 疑似動画を対象にルールが動作し、Alerts画面へ反映されることを確認する。
4. raw eventと正式Alertの表示項目マッピングを整理する。
5. 起動手順と未設定環境変数の警告を整理する。

## 2026-08-28 中断時点

- Alerts UIは表示可能（Elasticsearch由来のイベント3〜4件）。
- `mdx-vss-video-analytics-api-1`は統合Compose管理、`healthy`。
- 互換APIの再起動後疎通を確認済み。
- NGCの`vss-video-analytics-api:3.2.0`取得はAccess Deniedで未使用。
- 次回はセンサー一覧APIから再開する。
