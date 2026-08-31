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

1. ~~ローカル設定または動画登録情報を返す`GET /v1/sensor/list`を追加する。~~ 完了
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
- `GET /vst/api/v1/sensor/list` -> `200`（`konro_inspection`疑似センサー）を確認済み。

## 2026-08-31 追記

- RT-VLM の `/v1/streams/add` と `/v1/generate_captions` を Alert Bridge 経由で確認し、Alert Rule は HTTP 201、Elasticsearch では `active` になった。
- コンテナ内の `localhost` はホストの MediaMTX ではないため、RTSP URL の `localhost`/`127.0.0.1` を `host.docker.internal` に変換し、compose に `host-gateway` を追加した。
- RT-VLM テストは `18 passed`。実際の要約・アラート生成は OpenAI 互換 VLM の応答と Kafka/Alert 設定に依存する。
- 残課題は、外部 VLM 応答を使ったチャンク処理と、生成されたアラートが UI/Elasticsearch に現れることの確認。
- 最新イメージで Alert Rule 再登録と `inference_active=true` を確認した。Kafka は未起動のため、caption のKafka配信ログには接続拒否が出る。外部 VLM の実応答待ちで、アラート文書の新規生成は未確認。
- 疑似RTSPは10秒チャンクで継続取得できている。RT-VLMワーカーは外部OpenAI互換推論の応答待ちとなるため、APIキー・モデル利用可否を確認できるまで新規イベント生成の成否は判定できない。
- Kafka（`confluentinc/cp-kafka:7.5.0`）を起動し、AlertSinkからのIncident投稿がHTTP 202で受理されることを確認した。`VLM_DETECTED`用の最小Alert設定を登録した結果、`mdx-vlm-incidents-2026-08-31`へ新規文書が保存され、`/video-analytics-api/incidents`の件数が5件になった。
- 新規文書のVLM検証は、疑似センサーに録画タイムラインがないため`verification-failed`（VST 404）となるが、Alert生成・Elasticsearch保存・UI取得までの経路は動作した。
- センサー別API（`/alert-bridge/api/v1/realtime/incidents?sensor_id=local-konro-inspection`）でも生成済みAlertを取得でき、疑似RTSP実行中に複数件が蓄積することを確認した。
- pass-through用の`local-konro-inspection-pt2`で、`video_path=/media/konro_inspection.mp4`、`verificationResponseCode=200`、`verificationResponseStatus=OK`を確認した。VSTタイムラインなしでもローカル動画によるVLM検証とElastic保存が完了する。
- 検証用ルールは`local-konro-inspection-pt2`の1件に整理した。RT-VLMテスト18件とruffチェックは成功。
- `services/alert/docker-compose.local.yml`でAlert Bridge/Kafka/Redisを一括起動し、`/health`が200、RT-VLMストリームが`inference_active=true`になることを確認した。以後、手動`docker run`は不要。
- 保存動画E2Eを実行し、`konro_inspection.mp4`（3.75MB）をアップロード後、10秒チャンク2件のcaption生成と削除までHTTP経路で完了した。VLM応答は各チャンク約1.2〜2.0秒。
- 保存動画検索の`preflight.py`は、VST `127.0.0.1:31000` が未起動のため停止した。RT-VLM/Alert経路の障害ではなく、VST/NVStreamer起動が次の前提条件である。
- 停止済み`vss-vios-nvstreamer-lvs`を再起動すると sensor API は`200`になったが、`/vst/api/v1/storage/timelines`は`404`（storage adaptor未提供）で、保存動画検索の前提は未充足。ストリーム一覧も0件。
