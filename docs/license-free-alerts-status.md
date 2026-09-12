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
2. ~~`Create Alert Rule`からAlert Bridgeへルールを登録・削除する。~~ 完了
3. ~~疑似動画を対象にルールが動作し、Alerts画面へ反映されることを確認する。~~ 完了
4. ~~raw eventと正式Alertの表示項目マッピングを整理する。~~ 完了
5. ~~起動手順と未設定環境変数の警告を整理する。~~ 起動・検証スクリプトを追加済み。Composeの警告は起動対象外サービス由来のため残存。
6. ~~VST Storage Adaptorを用意し、保存動画検索のtimeline API（現状404）を実装または接続する。~~ ローカルStorage互換APIで実装済み。`/vst/api/v1/storage/timelines` は登録済み動画に対して `200` を返す。
7. ~~`vss-agent`を含む検索・要約経路の外部公開ポートとCompose起動順を固定し、再起動後の疎通を自動検証する。~~ 完了

8. RT-CVの正規化JSONイベントに`info.video_path`を付与し、ローカルAlert Bridgeの
   パススルー検証が保存動画を参照できるようにした。Alertsプロファイルでは
   `${VSS_DATA_DIR}/videos/dev-profile-alerts`を`/data/videos`へ読み取り専用でマウントする。

現状では、raw captionレスポンスに`documentType=raw_events`と`isRawEvent=true`
を追加し、正式Alertとの区別をAPI契約に反映済み。Composeの未設定変数警告は
起動対象外サービスのinclude定義に由来するため、動作を変えない範囲での整理を
別途検討する。正式Alert文書の生成・表示はraw caption経路とは分けて確認する。
起動スクリプトの再実行による既存コンテナ再利用と、Alert Bridgeを含む一括スモークテストの再現性も確認済み。

## 2026-08-28 中断時点

- Alerts UIは表示可能（Elasticsearch由来のイベント3〜4件）。
- `mdx-vss-video-analytics-api-1`は統合Compose管理、`healthy`。
- 互換APIの再起動後疎通を確認済み。
- NGCの`vss-video-analytics-api:3.2.0`取得はAccess Deniedで未使用。
- センサー一覧APIは実装済み。現在はUIからのルール作成・表示確認へ移行。
- `GET /vst/api/v1/sensor/list` -> `200`（`konro_inspection`疑似センサー）を確認済み。

## 2026-09-10 追記

- LVSプロファイルの`NEXT_PUBLIC_ENABLE_ALERTS_TAB`を`true`に設定し、Alertsタブを常時有効化。
- NVStreamer互換UIのMedia Uploadでドラッグ&ドロップを実装。ファイル選択と同じアップロード経路を使用する。
- ローカルサービスのCompose build contextを修正し、リポジトリルートから再ビルドできることを確認。
- NVStreamer、VST Storage、MediaMTX、Agent、Alert Bridge、UIを稼働状態で確認。全体スモークテスト成功。
- フォーク側の先行履歴をマージし、`codex/openai-rt-vlm`へpush済み（`8ad5e4de7`）。

## 2026-08-31 追記

- RT-VLM の `/v1/streams/add` と `/v1/generate_captions` を Alert Bridge 経由で確認し、Alert Rule は HTTP 201、Elasticsearch では `active` になった。
- コンテナ内の `localhost` はホストの MediaMTX ではないため、RTSP URL の `localhost`/`127.0.0.1` を `host.docker.internal` に変換し、compose に `host-gateway` を追加した。
- RT-VLM テストは `18 passed`。実際の要約・アラート生成は OpenAI 互換 VLM の応答と Kafka/Alert 設定に依存する。
- 外部VLM応答を使ったチャンク処理、Alert生成、Elasticsearch保存、Alerts UI表示まで確認済み。
- 最新イメージで Alert Rule 再登録と `inference_active=true` を確認した。Kafka は未起動のため、caption のKafka配信ログには接続拒否が出る。外部 VLM の実応答待ちで、アラート文書の新規生成は未確認。
- 疑似RTSPは10秒チャンクで継続取得できている。RT-VLMワーカーは外部OpenAI互換推論の応答待ちとなるため、APIキー・モデル利用可否を確認できるまで新規イベント生成の成否は判定できない。
- Kafka（`confluentinc/cp-kafka:7.5.0`）を起動し、AlertSinkからのIncident投稿がHTTP 202で受理されることを確認した。`VLM_DETECTED`用の最小Alert設定を登録した結果、`mdx-vlm-incidents-2026-08-31`へ新規文書が保存され、`/video-analytics-api/incidents`の件数が5件になった。
- 初期イベントでは、疑似センサーに録画タイムラインがないため`verification-failed`（VST 404）となる場合がある。pass-throughルールではローカル動画を使用し、VLM検証はHTTP 200で完了する。
- センサー別API（`/alert-bridge/api/v1/realtime/incidents?sensor_id=local-konro-inspection`）でも生成済みAlertを取得でき、疑似RTSP実行中に複数件が蓄積することを確認した。
- pass-through用の`local-konro-inspection-pt2`で、`video_path=/media/konro_inspection.mp4`、`verificationResponseCode=200`、`verificationResponseStatus=OK`を確認した。VSTタイムラインなしでもローカル動画によるVLM検証とElastic保存が完了する。
- 検証用ルールは`local-konro-inspection-pt2`の1件に整理した。RT-VLMテスト18件とruffチェックは成功。
- `services/alert/docker-compose.local.yml`でAlert Bridge/Kafka/Redisを一括起動し、`/health`が200、RT-VLMストリームが`inference_active=true`になることを確認した。以後、手動`docker run`は不要。
- 保存動画E2Eを実行し、`konro_inspection.mp4`（3.75MB）をアップロード後、10秒チャンク2件のcaption生成と削除までHTTP経路で完了した。VLM応答は各チャンク約1.2〜2.0秒。
- `vss-agent`を再起動し、内部`/health`が`{"value":{"isAlive":true}}`、`openai_vlm`（remote）と`es_caption`検索バックエンドが初期化されることを確認した。
- Agent検索の実行では、従来の`VST_INTERNAL_URL=http://<HOST_IP>:30888`がVST stream APIで502を返した。ローカルNVStreamer互換APIのsensor endpointは`31000`で200を返すため、`license-free.override.yml`に`VST_INTERNAL_URL=http://127.0.0.1:31000`の上書きを追加した。既存コンテナへ反映するには次回Compose再作成が必要。
- Compose再作成時は生成環境ファイルの`VSS_AGENT_PORT=8000`がコマンドへ展開されるため、override側で`command`も`8001`へ固定した。反映後、Agent検索は502ではなく`video_list=[]`を返し、VST接続自体は復旧した。保存動画をVSTへ登録していないため、caption検索結果は空である。
- VST互換アップロードAPIへ`konro_inspection.mp4`を登録すると、sensor/stream IDは発行される。しかし`POST /api/v1/videos/{sensor_id}/complete`は`/storage/timelines`の404で停止する。保存動画検索を成立させるには、timelineとstorage URLを提供するローカルStorage Adaptor、またはAgentのローカルファイルフォールバックが必要。
- ローカルファイルフォールバックを実装してAgentイメージを再ビルドしたが、NVStreamerの応答する`/home/vst/.../streamer_videos`がホストのbind mountへ現れず、Agentからファイルを読めなかった。NVStreamer側の保存先設定をbind mountへ合わせることが次の作業。
- ローカルStorage互換サービス`vss-vst-storage-local`を追加し、`31001`でupload/timelines/file-url/sensor-streamsを提供。単体のhealth、動画保存、timeline取得を確認済み。Agent overrideの`VST_INTERNAL_URL`を`http://127.0.0.1:31001`へ変更した。再開時はStorageサービス起動後に`vss-agent`をoverride付きComposeで再作成し、アップロード完了APIを再検証する。
- 2026-09-02: Storageへ`konro_inspection.mp4`を登録し、Agentのcomplete APIからRT-VLM caption生成（`chunks_processed=1`）まで成功。Agentの`vst_video_list`は動画を検出し、`vst_video_summarize_captions`も成功した。LVS直接解析はhuman prompt callback未登録で失敗したが、caption検索・要約の主要経路には影響しない。
- 2026-09-02追記: Elasticsearchを直接検索すると、既存の`default_*` caption文書は存在するが、今回のlocal Storage sensor IDに紐づく文書は0件だった。RT-VLMのcaption生成結果をKafka/Elasticsearchへ保存する経路は未接続であり、Agent要約の成功はVST側の要約応答を含むため、ES永続化の成功とは分けて扱う。
- 2026-09-02追記2: 公開Kafkaイメージ（`confluentinc/cp-kafka:8.2.0`）とトピック初期化を起動し、RT-VLMをプロファイルの`HOST_IP:9092`へ接続。実動画のcomplete API実行後、`default_<sensor>`インデックスに`raw_events` caption文書がLogstash経由で保存されることを確認した。ローカルRT-VLM composeの既定値と起動スクリプトにもKafka起動を反映し、再現可能な主要経路になった。
- 2026-09-02追記3: 既存のMediaMTXへ`konro_inspection.mp4`を疑似RTSP配信し、RT-VLMの`/v1/streams/add`へ`local-konro-inspection-pt2`を登録。キーワード一致によりAlert BridgeへHTTP 202で通知され、VLM検証後に`mdx-vlm-incidents-2026-09-02`へ正式incidentが保存された。Alerts APIでも同sensor_idに対しraw eventとformal incident（`isRawEvent=false`）の両方を取得できることを確認。検証後、ストリーム削除とMediaMTX停止を実施した。
- 2026-09-02追記4: Alert Bridge専用Kafkaとの9092番ポート競合を避ける`services/alert/docker-compose.external-kafka.yml`を追加。既存のプロファイルKafkaを再利用し、Alert Bridge/Redisをライセンスフリー起動スクリプトから冪等に起動できるようにした。
- 2026-09-02追記5: 保存動画要約の非対話APIでHITL callbackが未登録でも設定済みの既定scenario/eventsを使えるフォールバックをAgentへ追加。ローカル`vss-lvs`は公開依存（`uvicorn`、`sse-starlette`、`json_repair`等）の欠落が段階的に検出されるため、現時点では起動スクリプトの主要経路に含めず停止している。
- 2026-09-02追記6: `vss-lvs`が利用できない場合、`lvs_video_understanding`は`video_understanding`へフォールバックするようにした。`konro_inspection.mp4`に対するAgent OpenAI互換API呼び出しで、Storageから動画を取得し、OpenAI互換RT-VLMへ送信する経路がHTTP 200まで完了した。
- 2026-09-02追記7: 保存動画要約の直接VLMフォールバック、Agent設定取得警告の修正、NVIDIA依存インベントリ、READMEのライセンスフリー起動手順をフォークへ同期した。本日の検証後、関連コンテナは停止済み。再開時は `./deploy/docker/scripts/start-license-free-lvs.sh` を実行する。

## 再開コマンド（2026-08-31）

```bash
docker run -d --name vss-vst-storage-local --network host \
  -v "$PWD/deploy/docker/data-dir/videos/dev-profile-lvs:/data/videos" \
  vss-vst-storage-local:dev
cd deploy/docker
docker compose --env-file developer-profiles/dev-profile-lvs/generated.env \
  -f compose.yml -f developer-profiles/dev-profile-lvs/license-free.override.yml \
  up -d --no-deps --force-recreate vss-agent
```

分離Composeをまとめて起動する場合は、リポジトリルートから次を実行する。

```bash
./deploy/docker/scripts/start-license-free-lvs.sh
```

起動後の主要経路を一括確認する場合:

```bash
./deploy/docker/scripts/verify-license-free-lvs.sh
```

この検証には、稼働中のVLMコンテナがNVIDIA NGC由来でないことの確認も含む。

疑似RTSPで正式Alert経路を再現する場合（MediaMTXとffmpegを使用）:

```bash
./deploy/docker/scripts/demo-license-free-alert.sh
```
- 保存動画検索の`preflight.py`は、VST `127.0.0.1:31000` が未起動のため停止した。RT-VLM/Alert経路の障害ではなく、VST/NVStreamer起動が次の前提条件である。
- 停止済み`vss-vios-nvstreamer-lvs`を再起動すると sensor API は`200`になったが、`/vst/api/v1/storage/timelines`は`404`（storage adaptor未提供）で、保存動画検索の前提は未充足。ストリーム一覧も0件。

### 2026-09-11 behavior-analytics と skills の確認

- 現チェックアウトには`skills/operations/`ディレクトリは存在せず、`skills/vss-*`構成になっている。本家最新版で運用系スキルの配置が変更されている可能性があるため、最新版との差分確認を別途行う。
- `skills/`配下の`vss-*`はクライアントアプリ本体ではなく、AgentがAPIやサービスを操作するためのスキル定義・手順書である。実行可否は呼び出し先サービスの実装に依存する。
- `vss-manage-alerts`、`vss-manage-video-io-storage`、`vss-setup-video-analytics-api`、`vss-ask-video`、`vss-summarize-video`、`vss-generate-video-report`は、現在のローカル互換サービスで基本経路を利用できる。
- `vss-search-archive`と`vss-generate-video-report-rag`はElasticsearch、Storage、Embedding/RAGの構成に依存し、基本機能と本家完全互換を分けて扱う。
- `vss-query-analytics`と`vss-setup-behavior-analytics`はBehavior AnalyticsのKafka入力を前提とする。Behavior Analytics本体のCompose/Dockerfileは現在もNVIDIAレジストリイメージと内部ビルドURLに依存しており、license-free経路には未接続。
- `vss-deploy-detection-tracking-2d/3d`、`vss-deploy-dense-captioning`、`vss-deploy-video-embedding`などのデプロイ系スキルは、DeepStream/NVIDIAモデル/NVStreamer依存が残るため、現状のlicense-free構成では未対応または部分対応。
- Behavior Analyticsの分析ロジック自体はPython/NumPy/OpenCV等でCPU実行可能だが、Kafka/Redis/MQTT、必要に応じたTriton、上流の物体検出が別途必要。GPUはBehavior Analytics固有ではなく、主に上流推論・VLM側で要求される。
- Behavior Analytics用の`services/analytics/behavior-analytics/docker/Dockerfile.local`と`deploy/docker/services/analytics/behavior-analytics/compose.local.yml`を追加した。`python:3.13-slim-bookworm`ベースでNVIDIA/NGC内部ツールを使わずにビルドでき、`vss-behavior-analytics-local:dev`のビルドと主要依存のimportを確認済み。次回はKafka接続下でPlaybackまたは2Dアプリを実行する。
- 2026-09-12: 公開Kafka（`apache/kafka:3.9.0`）へwarehouse 2D playbackデータを投入し、CPU版2D AnalyticsをE2E実行した。`mdx-behavior`と`mdx-incidents`へのprotobuf出力を確認し、NVIDIAイメージなしで分析処理が動作した。元設定に`incidents`トピック定義が欠落していたため追加修正した。`mdx-events`は今回のデータ条件では出力なし。
- 2026-09-12追記: RT-CV localのJSONイベントとBA入力のprotobuf契約を接続する`apps/adapters/json_to_protobuf.py`を追加。公開Kafka上で`ds-perception`→`mdx-raw`の変換を実データで確認した。`compose.local.yml`にはアダプター用サービスも追加済み。次はRT-CV local、アダプター、CPU版2D Analyticsを同時起動し、実動画由来の検出からbehavior/incidentまで通す。
- 2026-09-12追記2: `konro_inspection.mp4`をRT-CV localのdemo検出器へ登録し、RT-CV JSON→protobufアダプター→CPU版2D Analyticsを同時起動した。`ds-perception`、`mdx-raw`、`mdx-behavior`への出力と、BAログのbehavior生成（sensor `konro-e2e`）を確認。warehouse設定では単独のPerson検出に対するincident条件が成立せず、incident 生成は0件だった。検証用コンテナは停止・削除済み。
- 2026-09-12追記3: BAは`mdx-raw`だけでなく`mdx-notification`等の設定済みトピックもconsumer初期化時に要求する。公開Kafkaを単独起動する場合は、設定に列挙された全トピックを先に作成する必要がある。本番相当のinfra Composeには既存の`vss-kafka-topics`初期化サービスがあるため、そちらを優先して利用する。
- 2026-09-12追記4: RT-CV localのJSON中間トピック`ds-perception`がinfra Kafka初期化リストに含まれていなかったため、`deploy/docker/services/infra/compose.yml`の2つのトピック定義へ追加した。既存Kafkaを再作成する際に自動作成される。
- 2026-09-12追記5: 既存Kafkaを停止状態から起動した直後にBAを立ち上げると、brokerがhealthyになるまでconsumer割り当てが遅延する。BA側のEOF/FATALはこの初期化競合時に発生し得るため、再現手順ではKafka healthと`vss-kafka-topics`完了を待ってからBA/アダプターを起動する。
- 2026-09-12追記6: 上記の起動順序を`deploy/docker/scripts/start-license-free-behavior-analytics.sh`へ実装した。Kafka health、topic初期化コンテナの終了コードを確認してからCPU版BAとJSONアダプターをCompose起動する。
- 2026-09-12追記7: 起動スクリプトを実行し、既存Kafkaのhealth確認後に`vss-behavior-analytics-local`と`vss-behavior-analytics-json-bridge`が起動し、BA consumerのpartition割り当てを確認した。イメージのビルドは起動時に行わず、事前ビルド済み`vss-behavior-analytics-local:dev`を使用する。
- 2026-09-12追記8: 起動スクリプトで立ち上げた常駐BA経路へ、再起動後のRT-CV localから`konro_inspection.mp4`を登録した。`ds-perception`→`mdx-raw`のoffset進行と、BAログのsensor `konro-live-ba-3`に対するbehavior生成（複数batch）を確認。warehouse 2D設定ではincidentは0件だが、動画入力から分析出力までの主要経路は成立した。
