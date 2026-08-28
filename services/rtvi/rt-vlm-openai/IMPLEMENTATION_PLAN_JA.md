# OpenAI版RT-VLM 互換実装の変更内容と今後の計画

最終更新: 2026-08-26

## 1. 目的

NVIDIA Video Search and Summarization（VSS）が参照するRT-VLMを、NVIDIA配布モデルやNGC上の非公開実装に依存せず、OpenAIまたはOpenAI互換のマルチモーダルAPIで置き換える。

本実装の当面の対象は、保存済み動画を受け取り、時間チャンクごとにフレームを抽出し、VLMキャプションをVSS互換形式で返す経路である。

## 2. 背景と判断

元のRT-VLMコンテナは、リポジトリ内のソースだけで完結していない。DockerfileはNGCのベースイメージへ一部ソースを重ねる構成であり、依存関係、起動処理、動画ランタイムの一部はベースイメージ側に含まれる。そのため、元コンテナをソースレベルで完全再現することはできない。

一方、LVSから利用される保存動画向けAPI契約はリポジトリ内の呼び出しコードから確認できる。そこで、内部実装の複製ではなく、外部契約を満たす独立サービスとして再実装した。

## 3. 現在の到達点

現在は「保存動画向けRT-VLM単体互換」の段階まで完了している。

| 項目 | 状態 | 備考 |
| --- | --- | --- |
| 動画ファイルのアップロード | 完了 | ローカル資産ストアへ保存 |
| 動画メタデータ取得 | 完了 | `ffprobe`を使用 |
| 時間チャンク分割 | 完了 | チャンク重複に対応 |
| フレーム抽出 | 完了 | `ffmpeg`で等間隔JPEG抽出 |
| OpenAI画像入力 | 完了 | Chat Completionsの`image_url`を使用 |
| 通常JSON応答 | 完了 | VSS互換の`chunk_responses`を返す |
| SSE応答 | 完了 | usageイベントと`[DONE]`を返す |
| Dockerイメージ | 完了 | 非root、FFmpeg、curlを搭載 |
| 実OpenAI API検証 | 完了 | `gpt-4.1-mini`で指定動画を検証 |
| VSS全体への組み込み | 保存動画で完了 | LVS、Agent、検索・要約まで実動画で確認 |
| RTSP | 最小実装 | `/v1/streams/add`でFFmpegチャンク処理。再接続・音声・全API互換は未完了 |
| Kafka | 実装済み | 任意設定時にVisionLLM protobufを発行 |
| 音声 | 未実装 | OpenAI互換サービスでは無効化 |

## 4. 実装した構成

処理フローは次のとおり。

1. `POST /v1/files`で動画を受信する。
2. 動画本体とメタデータJSONを資産ディレクトリへ保存する。
3. `POST /v1/generate_captions`で対象ファイル、プロンプト、チャンク条件を受け取る。
4. `ffprobe`で動画時間と解像度を取得する。
5. 指定時間範囲をチャンクへ分割する。
6. 各チャンクから指定枚数のJPEGフレームを等間隔抽出する。
7. フレームをBase64の`data:image/jpeg`としてOpenAI互換Chat Completionsへ送信する。
8. モデル応答、時間範囲、レイテンシ、フレーム数、トークン数を`chunk_responses`へ変換する。
9. 通常JSONまたはSSEとしてLVS側へ返す。

主要ファイル:

| ファイル | 役割 |
| --- | --- |
| `src/rt_vlm_openai/app.py` | FastAPI、互換エンドポイント、JSON/SSE応答 |
| `src/rt_vlm_openai/assets.py` | アップロード資産とメタデータの保存・削除 |
| `src/rt_vlm_openai/video.py` | `ffprobe`、チャンク分割、フレーム抽出 |
| `src/rt_vlm_openai/openai_backend.py` | OpenAI互換マルチモーダル要求の生成 |
| `src/rt_vlm_openai/models.py` | 入出力モデルと入力検証 |
| `src/rt_vlm_openai/config.py` | 環境変数からの設定読込 |
| `scripts/e2e.py` | 実動画のアップロードから削除までを行うE2Eクライアント |
| `Dockerfile` | 独立してビルド可能な実行イメージ |

## 5. 対応API

実装済み:

- `GET /v1/health/ready`
- `GET /v1/health/live`
- `GET /v1/models`
- `POST /v1/files`
- `GET /v1/files`
- `DELETE /v1/files/{id}`
- `POST /v1/generate_captions`
- `POST /v1/streams/add`、`GET /v1/streams/get-stream-info`、`DELETE /v1/streams/delete/{id}`（最小RTSP/fileワーカー）

`generate_captions`では、少なくとも次の入力を処理する。

- `id`
- `prompt`
- `model`
- `stream`
- `chunk_duration`
- `chunk_overlap_duration`
- `system_prompt`
- `max_tokens`
- `temperature`
- `top_p`
- `seed`
- `response_format`
- `stream_options.include_usage`
- `media_info.start_offset`
- `media_info.end_offset`
- `num_frames_per_second_or_fixed_frames_chunk`
- `use_fps_for_chunking`
- `vlm_input_width`
- `vlm_input_height`

## 6. DockerとComposeの変更

`deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`に、次の変更を加えた。

- `RTVI_VLM_IMAGE`でRT-VLMイメージを差し替え可能にした。
- `RTVI_OPENAI_ASSET_DIR`を渡せるようにした。

独立イメージ側では、次を実施している。

- Python 3.13 slimを使用する。
- `uv`を`0.8.15`へ固定する。
- アプリを非editable installする。
- FFmpeg、ffprobe、Compose healthcheck用curlを導入する。
- UID 1001の非rootユーザーで起動する。
- `/var/lib/rt-vlm-openai/assets`を標準資産保存先とする。

## 7. 設定

主要な環境変数:

| 変数 | 必須 | 用途 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 必須 | OpenAI APIキー。`VIA_VLM_API_KEY`より優先する |
| `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` | 必須 | 使用する画像入力対応モデル |
| `VIA_VLM_ENDPOINT` | 任意 | OpenAI互換APIのベースURL。公式OpenAIでは不要 |
| `RTVI_OPENAI_ASSET_DIR` | 任意 | アップロード資産の保存先 |
| `RTVI_OPENAI_DEFAULT_CHUNK_DURATION` | 任意 | 標準チャンク秒数 |
| `RTVI_OPENAI_DEFAULT_FRAMES_PER_CHUNK` | 任意 | 標準フレーム枚数 |
| `RTVI_OPENAI_MAX_FRAMES_PER_CHUNK` | 任意 | チャンク当たりの最大フレーム枚数 |
| `RTVI_OPENAI_REQUEST_TIMEOUT_SECONDS` | 任意 | OpenAI要求のタイムアウト |
| `VLM_MAX_GENERATION_TOKENS` | 任意 | 最大出力トークン数 |

ルートの`env`ファイルはGit除外済みで、権限を`600`に設定している。APIキーをログ、文書、Git差分へ出力しないこと。

## 8. テストと検証結果

自動テストは7件あり、次を確認している。

- 重複を含むチャンク範囲
- 単一チャンク
- 実FFmpegによるテスト動画のprobeとJPEG抽出
- ファイルのupload、list、delete
- 通常応答とSSE契約
- 不正な音声指定とチャンク重複の拒否
- OpenAI互換マルチモーダル要求の内容
- OpenAI接続障害時のJSON 502応答

実動画検証:

- 動画: `/home/morioka/temp/Video-to-SOP-Generator/Videos/konro_inspection.mp4`
- 形式: H.264、1280×720、30 fps
- 長さ: 15.234秒
- サイズ: 3,750,574 bytes
- モデル: `gpt-4.1-mini`
- 最終条件: 1チャンク、8フレーム、SSE
- VLMレイテンシ: 約5.94秒
- チャンク全体レイテンシ: 約6.78秒
- 入力トークン: 12,128
- 出力トークン: 238

品質評価では、初期プロンプトが「電池を取り出す」「交換の準備をする」という未観測動作を生成した。実動画の後半を0.5秒間隔で確認すると、実際は収納部を開き、電池を指さし、収納部を閉じており、電池は取り出していない。

この問題に対し、共通プロンプトへ次の制約を追加した。

- 入力画像は連続映像ではなく、疎な観測であることを明示する。
- 画像で直接確認できる物体と動作だけを記述する。
- 不明な物体へ候補名を付けず、見た目と操作だけを記述する。
- フレーム間の未観測動作、意図、目的を推定しない。
- 開閉、挿入、取り外しなどの状態遷移は、前後状態が画像で確認できる場合だけ記述する。
- 指さした、触れた、見えなくなっただけの物体を「取り外した」と記述しない。

改善後は、点火、電池収納部を開く、電池を確認する、収納部を閉じる、という実映像に沿った説明になった。ただし、消火操作の再現率や電池本数などの細部は、引き続き評価対象である。

## 9. 既知の制約

現時点で未対応または不十分な点:

- RTSPライブストリーム入力
- KafkaおよびNvSchema形式のイベント配信
- 音声入力と音声認識
- URLからの動画取得
- 画像単体のキャプション生成
- 埋め込み生成
- RTVI-Embedの置換（Gemma 4系を含むマルチモーダル埋め込みモデルの評価）
- 複数動画・複数要求の負荷制御
- API認証と利用者ごとの認可
- OpenAIのrate limitに対するキュー制御
- 永続ボリュームを含む本番資産管理
- OpenTelemetryメトリクスと分散トレース
- NVIDIA版RT-VLM固有の再接続、GOP最適化、GPUデコード

また、OpenAIへ渡しているのは動画そのものではなく、時間チャンクから抽出した静止画である。フレーム間でのみ起きた短い動作は観測できない。フレーム数を増やすと再現率は上がるが、画像入力トークン、料金、レイテンシも増える。

## 9.1 LLM/VLMの実行場所

LLMとVLMは独立して実行場所を選択できる。`LLM_MODE`/`VLM_MODE`は`local`、`local_shared`、`remote`に対応し、`LLM_MODEL_TYPE`/`VLM_MODEL_TYPE`で`nim`または`openai`系の設定を選ぶ。`--use-remote-llm`、`--use-remote-vlm`を指定した場合は、それぞれの`*_ENDPOINT_URL`とモデル名を使う。

現在の検証環境は、Agent LLMが`remote + openai + gpt-4o-mini`、保存動画RT-VLMもOpenAI互換remoteである。ローカルLlama/Nemotron/Qwen系NIMへ戻す場合は、対応するComposeプロファイル、GPU、モデルキャッシュが必要になる。LLMだけremote、VLMだけlocal、またはその逆の混在も可能である。

## 9.2 VST動画アップロードの根拠と再現手順

VSTの公開ドキュメントが手元にない場合でも、リポジトリ内のUI実装
`services/ui/packages/common/lib-src/utils/chunkedUpload.ts`および
`videoUpload.ts`が実際に使用するプロトコルを定義している。したがって、今後はVSTバイナリに対する推測的な試行錯誤ではなく、この実装とテストを基準にする。

1. Agentの`POST /api/v1/videos`へ`{"filename":"<name>"}`を送り、VSTのアップロードURLを得る。
2. ファイルを既定10MiB（`10 * 1024 * 1024`）に分割し、各チャンクを順番にPOSTする。multipartフィールドは`mediaFile`、`filename`、`metadata`（`{"timestamp":"2025-01-01T00:00:00"}`）である。
3. 各POSTに`nvstreamer-chunk-number`（1始まり）、`nvstreamer-total-chunks`、`nvstreamer-is-last-chunk`、`nvstreamer-identifier`（UUID）、`nvstreamer-file-name`を付ける。最後の応答に含まれる`sensorId`がVSTの動画IDになる。
4. Agentの`POST /api/v1/videos/{sensorId}/complete`へ最後のVST応答と`filename`をJSONで渡し、後処理（RT-VLM、Kafka、Elasticsearch）を開始する。

この形式は実動画でHTTP 200を確認済みであり、UIの単一チャンク実装と複数チャンク実装の双方に一致する。VSTの正式な外部仕様書が入手できた場合は、ヘッダー名・番号規則・応答フィールドを照合し、差分があればUI実装を優先せず修正する。

## 9.3 リアルタイムRTSPの互換範囲

Agentには`POST /api/v1/rtsp-streams/add`があり、VSTで得たRTSP URLを`POST /v1/streams/add`の`liveStreamUrl`としてRTVI-VLMへ登録する契約がある。OpenAI版RT-VLMにはこの契約に合わせた最小ストリームワーカーを実装した。

OpenAI版ではRTSPをFFmpegで一定時間のローカルチャンクへ切り出し、既存のフレーム抽出・OpenAI推論・Kafka発行をバックグラウンドで繰り返す。`/v1/streams/add`、一覧、`/v1/streams/delete/{id}`を実装し、`konro_inspection.mp4`の`file://`疑似入力で複数チャンク生成と停止を確認した。固定2秒リトライ、音声、完全なNVIDIA互換、`/v1/generate_captions/{id}`停止APIは今後の課題である。

## 9.4 アラート発報の契約境界

Alertサービスの設定では、Incident protobufの入力トピックは`alert-bridge-incidents`、VLM検証結果の保存トピック／インデックスは`mdx-vlm-incidents`、message typeは`Incident`である。RT-VLMが現在発行する`VisionLLM`キャプションprotobufとは別スキーマであり、キャプション中の危険語を単純にIncidentへ変換してはいけない。次段階では、`services/alert`の`convert_incident_to_protobuf_incident`と既存sinkの必須フィールドを参照し、明示的なアラートルールまたはAlert側の検証経路を通してから発報する。

既存のサンプル変換では、少なくとも`timestamp`、`end`、`sensorId`、`objectIds`、`isAnomaly`、`category`、`place`がIncident本体に設定され、VLM説明などは`info` mapへ文字列として格納される。RT-VLM側にこのprotobuf依存を直接追加するのではなく、まずAlert側の`vlm_enhanced_sink`またはHTTP投入経路を再利用する方針とする。

AlertのHTTP入口は`POST /api/v1/incidents`で、JSONの場合は少なくとも`id`、`timestamp`、`sensorId`を受け付け、Alert側でIncident protobufへ変換して`alert-bridge-incidents`へ発行する。したがって、将来のアラート接続はこのHTTP入口を利用するのが最小依存となる。

キーワード方式の任意Alert bridgeを追加した。`RTVI_OPENAI_ALERT_ENDPOINT`と`RTVI_OPENAI_ALERT_KEYWORDS`（カンマ区切り）の両方を設定した場合だけ、ストリームキャプションに一致語があるチャンクを`/api/v1/incidents`へ送信する。既定は無効であり、保存動画経路やKafkaキャプションには影響しない。これは暫定的な体験用ルールで、検知品質・重複抑制・本番認証は未対応である。

## 10. 今後の計画

### 2026-08-26 実施済みのフェーズA準備

- [x] Agent、LVS、UIのローカルイメージをビルドした。
- [x] VIOS基盤、sensor、streamprocessing、NVStreamer、ingressをローカルビルドした。
- [x] LVSプロファイルを単一ストリームの直接VIOS経路に切り替え、SDR controllerを必須構成から外した。
- [x] Composeの主要イメージを環境変数で差し替え可能にした。
- [x] 全イメージがローカルの場合にNGCログインを省略する`SKIP_NGC_LOGIN=true`を追加した。
- [x] 依存イメージ取得とCompose構成生成を確認した。
- [x] GPUなしの単体Composeで保存動画E2Eを実行した。アップロード、8フレーム抽出、OpenAI推論、SSE、削除を確認した。
- [x] WSL2へNVIDIA Container ToolkitとCDIを設定し、Dockerコンテナ内からRTX 3060を認識できることを確認した。
- [x] GPU対応のLVSプロファイルを実起動し、LVS、RT-VLM、VIOS、Agent、UI、Kafka、PostgreSQL、Elasticsearchの起動を確認した。
- [x] ホストRedisとの6379番ポート競合を解消し、Compose Redisの`PONG`応答を確認した。
- [x] WSL再起動後に不足していたElasticsearchのnamed volumeを再作成し、`1000:1000`所有権へ修正してhealthyへ復旧した。

WSL再起動後の復旧では、`elasticsearch`の`mdx_mdx-elastic-data`と`mdx_mdx-elastic-logs`をUID/GID `1000:1000`へ変更する必要があった。ホストRedisサービスが自動起動するとCompose Redisと6379番ポートが再び競合するため、継続利用時はホスト側の`redis-server`を停止・無効化するか、Compose側ポートを変更する。

今回の実起動で取得された公開依存イメージは、Redis、PostgreSQL、Kafka、Elasticsearch、Kibana、Logstash、HAProxy、Phoenixである。これらはVLM/LLM本体ではなく、キュー、DB、検索、入口、観測用の基盤サービスである。

### 2026-08-26 終了時点の検証結果

- [x] AgentのOpenAIチャットAPIで動画一覧と動画質問応答を確認した。
- [x] Agentの動画登録APIで`konro_inspection.mp4`をVSTへ登録し、完了APIを確認した。
- [x] `nv.VisionLLM` protobuf生成とKafka発行の任意機能をOpenAI版RT-VLMへ追加した。
- [x] Kafka障害時にキャプションAPIを巻き込まないbest-effort発行へ変更した。
- [x] Kafka対応イメージをLVSへ反映し、RT-VLMとKafkaのhealthcheckを確認した。
- [x] KafkaからLogstash、Elasticsearchまでの実メッセージ登録を確認した。
- [x] Agent API の動画登録完了からRT-VLMキャプション検索までの自動経路を確認した。

本日のDocker検証環境は終了時に停止する。次回はCompose起動前にKafkaデータ、Elasticsearch volume、ホストRedisのポート競合を確認する。

終了処理: 2026-08-27にVSS検証用コンテナ（Agent、LVS、RT-VLM、VIOS、Kafka、Elasticsearch、Kibana、Logstash、UI、ingress等）が停止状態であることを確認した。Dockerコンテナは削除せず、永続ボリュームは保持している。

### フェーズA: VSS全体への統合（最優先）

- [x] `RTVI_VLM_IMAGE=vss-rt-vlm-openai:test`でLVSプロファイルを起動した。
- [x] Compose内のRT-VLM healthcheckがhealthyになることを確認した。
- [x] 復旧後の公開エンドポイント（LVS `38111`、RT-VLM `8018`、UI `3000`）がHTTP 200になることを確認した。
- [x] Agentの互換動画登録APIへ`konro_inspection.mp4`を登録し、VST完了APIが200を返すことを確認した。

AgentのOpenAIチャットAPI（`/v1/chat/completions`）では、動画一覧と`konro_inspection`への質問応答がHTTP 200で動作した。ただし質問応答はAgentの`video_understanding`による直接VLM経路であり、RT-VLMキャプションをElasticsearchから検索した結果ではない。動画中で未観測の電池取り外しまで推定する回答もあり、根拠区間付き検索としては未完了である。

Agentの`POST /api/v1/videos/{sensor_id}/complete`は、現実装ではVSTのタイムライン・動画URL取得後にRTVI-CVとRTVI Embedだけを呼ぶ。RTVI VLMの保存動画キャプション呼び出しやKafkaキャプション発行は含まれないため、RT-VLMを置き換えただけではUI登録から検索用キャプション登録まで自動接続されない。
- [x] LVS UI相当の Agent 動画登録完了 API から動画を登録し、キャプションを検索用メタデータへ登録した（ブラウザUI操作自体は未実施）。
- [x] Agent側LLMをOpenAIへ設定し、キャプション検索の質問応答まで通した。要約専用経路は未評価。
- [x] Agent側LLMをOpenAIへ設定し、動画一覧と直接VLM質問応答を確認した（検索メタデータ経由ではない）。

`lvs_caption_retrieval`の要約専用経路は、`LVS_CAPTION_GENERATE_SUMMARY=true`で有効化できる設定を追加した。既定値はfalseで、検索結果の引用区間を保持する従来動作を優先する。実コンテナでの要約品質と追加レイテンシは未評価。

この直接VLM経路は、RT-VLMのチャンクキャプションを検索して根拠区間を返す経路とは分けて評価する。
- [x] OpenAI版RT-VLMでは不要なNVIDIA runtimeとGPU予約を、環境に応じて無効化できる構成を追加した。`rtvi-vlm-openai-no-gpu.override.yml`をComposeへ重ねると、RT-VLMサービスだけCPU実行になる（VIOS/LVSなど他サービスのGPU要件は別）。
- [ ] RTVI-Embedは当面既存構成を使い、将来Gemma 4系などへ置換する場合は動画・画像・テキスト同一空間、API互換、ベクトル次元、正規化、Elasticsearch再構築を評価する。

完了条件:

- UIまたはLVS APIから`konro_inspection.mp4`を登録できる。
- 動画内容を検索できる。
- 点火と電池確認の手順について質問し、根拠動画区間付きの回答を得られる。
- NVIDIA RT-VLMコンテナを起動せずに一連の処理が完了する。

### フェーズB: キャプション品質評価

- [ ] 代表動画と正解アクション列を10件以上用意する。
- [ ] 4、8、12フレームの精度、料金、レイテンシを比較する。
- [ ] 15秒、30秒、60秒チャンクの動作再現率を比較する。
- [ ] 幻覚率、動作再現率、時系列順序、物体同定精度を記録する。
- [ ] OpenAIおよびOpenAI互換VLMを同じ評価セットで比較する。
- [ ] プロンプトだけでは抑止できない誤りを、後段検証または複数パスで補正するか判断する。

完了条件:

- 採用モデル、フレーム数、チャンク長の根拠を数値で説明できる。
- 安全手順に影響する未観測動作の追加が、定めた許容値以下になる。

### フェーズC: 運用性とセキュリティ

- [x] 同時実行数の上限を追加した。`RTVI_OPENAI_MAX_CONCURRENT_REQUESTS`（既定2）で、ストリーミング・通常JSONのチャンク処理を共有Semaphoreで制限する。待ち行列の拒否・優先度制御は未実装。
- [x] OpenAI SDKの再試行回数を`RTVI_OPENAI_MAX_RETRIES`で設定可能にした（既定3、負数は0へ補正）。指数バックオフ等の詳細はSDK既定動作に委ねる。
- [x] 動画アップロード直後に`ffprobe`で実ファイル形式を検証し、動画ストリームまたは有効なdurationがない場合は保存資産を削除してHTTP 422を返す。
- [ ] API認証、監査ログ、秘密情報管理を追加する（元実装もサービスAPIのBearer認証は持たず、NGC/OpenAI等の資格情報を環境変数で渡す方式。今回の置換で必要な外部APIキーの環境変数渡しは実装済み。公開運用時のみ要対応）。
- [x] RT-VLM既定のアップロード資産保存先にnamed volume `rtvi-openai-assets`を追加し、コンテナ再作成後も保持する構成にした。保持期限・削除ポリシーは未実装。
- [x] API middlewareで`x-request-id`（受信値またはUUID）をレスポンスへ返し、HTTPメソッド、パス、ステータス、総レイテンシをログへ出力する。チャンクごとのOpenAIレイテンシ・トークン数は既存の`chunk_responses`へ含まれる。Prometheus形式のメトリクス出力は未実装。
- [ ] 異常終了後の孤立ファイルを回収する。

### フェーズD: 必要に応じた機能拡張

- [ ] RTSP入力の要否を確認する。
- [ ] Kafka/NvSchema互換出力の要否を確認する。
- [ ] 音声、URL入力、画像単体入力の優先順位を決める。
- [ ] NVIDIA版と完全互換にする範囲を明示する。

## 11. 再開手順

ブランチ:

```text
codex/openai-rt-vlm
```

主要コミット:

```text
ae3764cbf Add OpenAI-backed RT-VLM compatibility service
e3e3ebdea Fix OpenAI RT-VLM container packaging
36dc04e15 Add stored-video RT-VLM end-to-end check
295935020 Harden OpenAI video caption grounding
```

テスト:

```bash
cd services/rtvi/rt-vlm-openai
UV_CACHE_DIR=/tmp/vss-rt-vlm-openai-uv-cache uv run pytest -q
UV_CACHE_DIR=/tmp/vss-rt-vlm-openai-uv-cache uv run ruff check .
```

Dockerビルド:

```bash
docker build -t vss-rt-vlm-openai:test services/rtvi/rt-vlm-openai
```

E2Eクライアント:

```bash
uv run --extra dev python scripts/e2e.py \
  --base-url http://127.0.0.1:8018 \
  --video /path/to/video.mp4 \
  --chunk-duration 30 \
  --frames-per-chunk 8 \
  --stream
```

## 12. 現時点の結論

保存動画向けRT-VLMのOpenAI置換は実行可能であり、API、Docker、実動画、実OpenAIまで動作を確認した。したがって、非公開NGCコンテナがなくても、この範囲は独立実装で代替できる。

ただし、これはNVIDIA RT-VLM全機能の再現ではない。次の技術的な節目は、VSS全体へ組み込み、LVSの検索登録とAgentの質問応答まで通すことである。品質面では、静止画サンプリングに起因する未観測動作の見落としとVLMの推定を、評価セットに基づいて管理する必要がある。

### 目的の優先順位（再確認）

本作業の第一目的は、NVIDIAのライセンスが必要なコンテナイメージやモデルを使わず、VSSの主要経路を動作させることである。具体的には、保存動画登録、RT-VLMによるキャプション生成、Kafka/Elasticsearch登録、Agent検索・要約、Alert検証を対象とする。API認証、監査、安全性強化、推論品質評価、RTSP長時間運用、Embedding置換は、主要経路の代替動作を妨げない範囲で後回しにする。
## 2026-08-27: Agent から Elasticsearch 検索までの疎通確認

- VST の `vst_video_list` が 502 になる問題は、`vss-vios-postgres` 停止後に sensor が再起動されていなかったことが原因だった。PostgreSQL 起動後に `vss-vios-sensor` を再起動し、`GET http://127.0.0.1:30888/vst/api/v1/sensor/streams` が 200 で `konro_inspection` を返すことを確認した。
- Elasticsearch の dynamic mapping では `metadata.content_metadata.uuid`、`doc_type`、`camera_id` が `text` + `.keyword` になっていた。`services/agent/src/lib/knowledge/adapters/es_caption.py` の `term` 条件を `.keyword` に修正した。
- 修正後、Agent API に対して `lvs_caption_retrieval` を明示した問い合わせを実行し、Agent が `es_caption` backend 経由で Elasticsearch の 1 件を取得した。取得内容には、青い炎 `[3.733s]`、電池収納部の開閉 `[11.733s-15.233s]`、コンロを消してから電池収納部を開ける安全上の記述が含まれていた。
- これにより、`OpenAI RT-VLM -> Kafka (nv.VisionLLM) -> Logstash -> Elasticsearch -> Agent/lvs_caption_retrieval (es_caption)` の検索経路が実データで確認できた。
- VST sensor は Redis (`127.0.0.1:6379`) への接続失敗を警告しているが、ストリーム一覧 API と今回の検索経路には影響しなかった。`vss-vios-streamprocessing` はイメージ起動時の dpkg/gstreamer パッケージ不整合が残っており、別途課題とする。

### 保存動画の自動接続（実装開始）

- `/api/v1/videos/{sensor_id}/complete` に `RTVI_VLM_BASE_URL` の設定を追加した。
- 完了 API が VST の生成済み動画 URL を取得し、RT-VLM の `/v1/files` へ同じ sensor UUID でアップロードした後、`/v1/generate_captions` を呼び出す処理を追加した。RT-VLM が発行する Kafka protobuf の UUID と VST sensor UUID を一致させる設計である。
- `/complete` からの自動実行は `vss-agent-local:3.2.2` 系イメージで検証済み。最新の名前解決修正は `Dockerfile.local-overlay` で `vss-agent-local:3.2.3` に反映した。
- [x] Agent イメージ `vss-agent-local:3.2.2` で `/complete` の実行を確認した。VST timeline、storage URL、RT-VLM `/v1/files`、`/v1/generate_captions`、Kafka、Logstash、Elasticsearch まで自動接続され、`chunks_processed: 1` と HTTP 200 を得た。
- [x] VST の新しい sensor UUID に対応する `default_5be874ef_...` index が作成され、`blue flame` の検索で 1 件のキャプション（青い炎 1.467 秒、収納部操作 5.200〜13.200 秒）が取得できた。
- streamprocessing は `VST_INSTALL_ADDITIONAL_PACKAGES=false` で再作成し、既存イメージ起動時の壊れた dpkg/gstreamer 追加インストールを回避した。恒久対応ではイメージ修正または package state の修復が必要。
- [x] 動画名だけを指定した Agent 問い合わせでも、`vst_video_list` → VST 名称/UUID 解決 → `lvs_caption_retrieval` → Elasticsearch の自動検索を確認した。重複していた `konro_inspection` は一覧の先頭 UUID を安定して採用し、`default_5be874ef_...` のキャプションを取得できた。
- [x] `Dockerfile.local-overlay` で既存 Agent イメージへ変更ファイルだけを重ねる軽量ビルドを追加した。`vss-agent-local:3.2.3` を作成し、コンテナ再作成後も動画名からの Elasticsearch 検索を確認した。フルビルドはリリース前の別検証とする。
- `streamprocessing` の apt 失敗は Ubuntu 24.04 の `libmjpegutils-2.1-0t64` への移行に対して追加インストールスクリプトが旧パッケージ名 `libmjpegutils-2.1-0` を含んでいたことが原因。`user_additional_install.sh` を t64 名へ修正し、gstreamer 導入前に `--reinstall` する修復処理を追加した。次回 `VST_INSTALL_ADDITIONAL_PACKAGES=true` で実起動確認する。
- `es_caption` の `.keyword` 条件に合わせて Agent の unit test 期待値を更新した。ホスト側に Agent 用 pytest 環境がないため、構文チェックと実コンテナ E2E を検証に用いる。
- [x] Agent のフル Dockerfile から `vss-agent-local:3.2.4` をビルドした。codec ライブラリ検査は `OK: no patent-encumbered codec libraries in image` だった。フルイメージで Agent を再作成し、動画名から `lvs_caption_retrieval` で Elasticsearch を検索する smoke test（HTTP 200）を確認した。
- [x] `vss-agent-ui` を起動し、UI は HTTP 200、`__ENV.js` は VST/Agent を HAProxy 外部ポート `7777` に設定していることを確認した。HAProxy ingress 起動後、`GET /vst/api/v1/sensor/streams` が 200 を返すことを確認した。ブラウザ上のファイル選択操作は未実施。
- [x] UI相当のnvstreamerチャンクアップロードを実動画で確認した。原因はstreamprocessing (UID 1000)のVST保存先bind mountがroot所有だったことと、libav/GStreamer依存ファイルの欠損だった。保存先をUID 1000へ修正し、追加依存をインストールして再起動後、VSTチャンクHTTP 200、Agent `/complete` HTTP 200、RT-VLMキャプション生成まで確認した。
- [x] VSTの恒久化を追加した。`user_additional_install.sh`でlibav/GStreamer依存（`libswscale7`、`libpostproc57`を含む）を導入し、UID 1000の保存先権限を毎回修正する。`vst.env`の`VST_INSTALL_ADDITIONAL_PACKAGES=true`で新規コンテナにも適用する。

### 2026-08-27 中断時点

- UIと同じnvstreamerチャンク形式で`konro_inspection.mp4`をVSTへ登録し、Agentの`/complete`、OpenAI版RT-VLMキャプション生成、Kafka/Elasticsearch登録までHTTP 200で確認した。
- VST失敗の原因は、streamprocessingの保存先bind mount所有権（UID 1000で書けない）と、イメージ内のlibav/GStreamer依存欠損だった。起動スクリプトとCompose環境変数へ恒久化修正を反映済み。
- 次回は、コンテナを起動する前に`VST_INSTALL_ADDITIONAL_PACKAGES=true`、保存先volumeの所有者、streamprocessingの`LD_LIBRARY_PATH`/`GST_PLUGIN_PATH`を確認する。起動後はVSTチャンクuploadを1回、Agent `/complete`を1回だけ検証すればよい。
- 2026-08-27再開時は、streamprocessingのHTTP待受前に一時的な503が発生したが、コンテナ再起動後に復旧した。`konro_resume4.mp4`でVSTチャンクHTTP 200、Agent `/complete` HTTP 200、RT-VLMキャプション生成を再確認した。
- 要約経路は一時的に`generate_summary=true`でAgentを起動し、`openai_llm`の解決（`summarize=True`）まで確認した。質問応答は別の直接動画ツールを選択したため、`es_caption`の要約処理呼び出し自体は未確認。設定は既定のfalseへ戻した。
- 再開後の`konro_resume4`でも`default_9c54c1f9_b05b_4457_ba10_36db22339cb9`に1件のRT-VLMキャプション文書が登録されていることを確認した。キャプション内容の品質評価は行わない。
- Agentへ「直接動画解析を使わず`lvs_caption_retrieval`だけを使う」と指示し、`collection=konro_resume4`、`query=blue flame`でElasticsearch検索を実行、1.667秒の根拠区間付き回答を得た。保存動画登録→RT-VLM→Kafka/Elasticsearch→Agent検索回答の本質経路を再確認した。
- `generate_summary=true`を一時有効化し、同じ`lvs_caption_retrieval`検索でOpenAI要約（`Summary:`）が付与された回答を確認した。確認後は設定をfalseへ戻した。
- 品質評価（代表動画10件、フレーム数・チャンク長比較）は後回し。要約経路は設定済みだが、実測は未実施。
- フォーク: `codex/openai-rt-vlm`、最終同期コミットはこのメモ更新後のもの。
- VST `vst-storage.json`の`video_path`をComposeのbind mount先`/home/vst/vst_release/streamer_videos/`へ修正した。初期失敗はVSTバイナリのAPI形式ではなく、streamprocessingイメージ内のlibav/GStreamer依存欠損とbind mount所有権不一致だった。イメージ再作成時にも依存インストールとUID 1000書き込み権限を維持する必要がある。
- `true` 起動の再検証では、コンテナ内 `/var/lib/apt/lists` が書き込み不可で apt が再試行ループになった。`vst.env` の既定値を `false` に変更し、runtime apt を opt-in にした。VST timeline は追加 apt 無効で正常動作しており、恒久的な codec 更新はイメージビルド時に行う。
- Docker LVSの`config.yml`と`config_rag.yml`で`openai_vlm`にも`base_url: ${VLM_BASE_URL}/v1`を明示した。これによりOpenAI本体だけでなく、Qwen/vLLM等のOpenAI互換VLMを`VLM_BASE_URL`と`VLM_NAME`だけで選択でき、`openai_llm`との個別切り替えも設定上そろった。
- [x] Alertサービスについて、NGCのdistroless最終イメージに依存しない`services/alert/Dockerfile.local`を追加した。`python:3.13-slim`上でrequirementsを導入するローカル検証用イメージで、`vss-alert-bridge:local`のビルドと主要依存（FastAPI、confluent-kafka、PyYAML）のimportを確認した。
- ローカルAlertイメージ用Compose overrideは`deploy/docker/services/alert/alert-local.override.yml`に追加した。これは元のVLM検証・Kafka連携の代替実行基盤であり、NVIDIA NGCイメージとの完全な機能同一性は保証しない。次段階でKafka/Redis/Elasticsearchを接続したAlert HTTP E2Eを行う。
- [x] ローカルAlertイメージをAlert付属Composeへoverrideして起動した。Redisを同時起動し、`GET /health` がHTTP 200、`POST /api/v1/incidents` がHTTP 202（Kafka producer初期化・ワーカー処理開始）を返すことを確認した。VLMバックエンド未接続でも受理経路は動作する。
- [x] ローカルoverrideへ`VLM_WARMUP_ENABLED`と`FASTAPI_PORT`の環境変数を追加した。NIMを使わない検証では`VLM_WARMUP_ENABLED=false`をコンテナ内へ確実に渡し、不要な起動待ちを防ぐ。
- [x] AlertのオンデマンドVLM検証をRTVIへ接続するため、RTVIへ`POST /v1/chat/completions`互換層を追加した。Alertの`video_url`を一時ファイルへ取得し、既存のフレーム抽出・OpenAI backendでcaptionを生成してChat Completions形式へ変換する。実動画でRTVI HTTP 200、Alert側`VLM response received`（約9.7秒）を確認した。`localhost`はRTVIブリッジコンテナ自身を指すため、コンテナから到達可能なホストアドレス（例: `172.17.0.1`）を使用する必要がある。
- [x] 上記オンデマンド検証の結果がElasticsearch `mdx-vlm-incidents-2026-08-27`へ保存されることを確認した。成功文書には`verificationResponseCode=200`、`verificationResponseStatus=OK`、`info.reasoning`、Incident ID/sensor/placeが含まれ、失敗試行も`verdict=verification-failed`として記録された。既存Alertの永続化契約と接続できている。
- Alertの`vlm_enhanced_sink`は現在のローカル設定では`elastic`のため、Kafkaの`mdx-vlm-incidents`には出力されない。元実装にはKafka sink実装が存在し、`vlm_enhanced_sink.type: kafka`と`incident.kafka.topic`/`alert.kafka.topic`を設定すれば再発行を選択できる。Kafka出力の実ブローカー検証は次段階とする。
- [x] `ALERT_VLM_ENHANCED_SINK_TYPE=kafka`の環境変数overrideをメイン処理・FastAPI側の両設定ローダーへ反映した。実動画のオンデマンド検証で`VLMEnhancedKafkaSink`が`mdx-vlm-incidents`へ送信し、Kafka partition 1のoffsetが0から1へ増加することを確認した。既定のElastic出力とKafka再発行を設定で選択できる。
- 2026-08-27終了時点: 保存動画検索、OpenAI版RT-VLM、AlertのChat互換VLM検証、Elasticsearch保存、Kafka再発行まで実動作を確認した。検証用の`alert-bridge`、`alert-bridge-redis`、ローカルRTVIコンテナは停止済み。次回はRTSP長時間安定性または元仕様のIncident検証経路から再開する。
- 2026-08-28再開: RTSP/fileストリームワーカーの失敗リトライを固定2秒から指数バックオフ（2→4→8…最大30秒）へ変更し、成功時に2秒へリセットするようにした。実動画を含むRTVIテストは14件すべて成功。長時間RTSPの実測は未完了。
- 切断相当（存在しないfile URL）でもストリームが登録状態を維持し、`inference_active=true`のまま再試行し、削除APIで停止できるテストを追加した。RTVIテストは15件すべて成功。実RTSP長時間試験は引き続き未実施。
- MediaMTX + FFmpegで疑似RTSPを起動し、コンテナからのRTSP接続自体は確認した。一方、接続直後の短い切り出しでは終了コード0でも空MP4になる事象を再現したため、RTSPの切り出し最低時間を10秒、`analyzeduration`/`probesize`を10Mへ変更した。修正後の実推論成功確認は次回へ持ち越す。
- 2026-08-28方針変更: RTSPの実推論・長時間安定性検証は後回しとする。現在の主経路（保存動画のVST登録 -> OpenAI版RT-VLM -> Kafka/Elasticsearch -> Agent検索、およびAlertオンデマンド検証）は確認済みであり、次の作業はこの経路の残課題を優先する。RTSP修正（最低10秒切り出し、`analyzeduration`/`probesize`、指数バックオフ）は保持し、後日まとめて再検証する。
- 2026-08-28回帰確認: OpenAI版RT-VLMの手書きコードのruff警告（Chat互換層のimport・長行）を修正し、生成protobufをruff対象外に設定した。`ruff check .` とpytest 15件が成功した。RTSPの実行検証は行っていない。
- 同日、`vss-rt-vlm:local` のDockerイメージを再ビルドし、依存解決を含むフルビルドが成功した。RTSPコンテナは起動せず、保存動画・Alert経路で利用可能なイメージ成果物のみ更新した。
- 要約E2Eの準備としてRT-VLM単体のready応答を確認したが、稼働中のLVSコンテナは`generate_summary=false`かつ別ホストIPのRTVI URLを使用していたため、既存基盤を再構成せず保留した。単体RT-VLMコンテナは停止済み。次回はLVS再作成時に要約設定とRTVI URLを同時に切り替えて一度だけ実測する。
- LVS Composeに`LVS_CAPTION_GENERATE_SUMMARY`（既定false）と`LVS_CAPTION_SUMMARY_MODEL`の環境変数渡しを追加した。YAML構文は確認済み。サービス単体のCompose検証は、親Composeで定義されるNIM依存サービスが不足するため`config --quiet`まで完走しなかった。要約E2Eは開発プロファイル全体を再作成できるタイミングで実施する。
- READMEを現実の実装に合わせて更新した。Alert向け`/v1/chat/completions`互換層が提供済みであること、LLM/VLMを個別にOpenAI互換エンドポイントへ切り替える設定、要約フラグの既定値を明記した。
- 要約E2E準備を実施。既存のローカルLVSイメージを`--no-deps --force-recreate`で再作成し、`LVS_CAPTION_GENERATE_SUMMARY=true`、`LVS_CAPTION_SUMMARY_MODEL=openai_llm`、RTVI URL反映を確認した。RT-VLMはhost networkの8018番でready、LVSの接続成功まで確認した。ただし稼働中にAgentコンテナが存在せず、`lvs_caption_retrieval`からの要約呼び出しは実測できなかった。検証後はRT-VLM停止、LVSを`generate_summary=false`へ復元した。
- Agentを一時的に8001番で起動して要約E2Eを再試行した。Agent起動・health・`/v1/chat`応答は成功したが、VST/NVStreamerが停止中でストリーム一覧APIが502となり、`konro_resume4`のコレクション名をElasticsearchインデックスへ解決できなかったため、`lvs_caption_retrieval`は空結果になった。要約ロジックの失敗ではなく、VST登録済みストリーム解決の前提不足である。検証用Agentは停止済み。
- VST内部URLの原因を切り分け、NVStreamerは31000番で直接待受し、Agentが30888番の旧Nginx経路を参照していたことを確認した。`VST_INTERNAL_URL`を`${HOST_IP}:${NVSTREAMER_HTTP_PORT}`へ変更し、AgentコンテナからVST APIがHTTP 200（空一覧）になることを確認した。既存の保存済みストリームメタデータがないため要約E2Eは未完了。検証用Agent/NVStreamerは停止済み。
- 正式VST構成ではstreamprocessingがタイムラインAPIを提供することを確認した。NVStreamer単体の一覧が空でも、`storage/timelines`のUUIDを自己対応として返すフォールバックをAgentのVSTユーティリティへ追加し、UUID指定の既存ESコレクション検索を可能にした。Agent依存テストはホストにCairo開発ライブラリがなく`pycairo`ビルドで停止したため、構文チェックまで確認済み。
- AgentローカルオーバーレイDockerfileのCOPYパス誤り（`agent/src`）を`services/agent/src`へ修正し、`vss-agent-local:3.2.5`のビルドに成功した。これでUUIDフォールバックを含むAgentイメージを再現できる。
- `vss-agent-local:3.2.5`を8001番で起動し、正式streamprocessingのタイムラインUUIDを使った`lvs_caption_retrieval`を実測した。VSTストリーム一覧が空でもUUIDフォールバックでElasticsearchから1件を取得し、`LVS_CAPTION_GENERATE_SUMMARY=true`時にはOpenAI `openai_llm`による`Summary:`付き応答（青い炎 1.467秒）まで成功した。検証後は要約フラグをfalseへ戻し、Agent/NVStreamerを停止した。
- 起動順序の手動ミスを防ぐため、`scripts/preflight.py`を追加した。VST（31000番）→RT-VLM ready→LVS ready→任意Agent healthの順で検査し、`--require-stream`指定時はVST一覧に対象名がなければ終了する。Python構文とヘルプ表示を確認済み。
- preflightのユニットテストを追加し、正常系・対象ストリーム欠落・サービス接続失敗を検証した。RT-VLMテストは18件成功、ruffも成功した。
- preflightに`--check-timeline-api`を追加した。保存動画の`/complete`に必須の`/vst/api/v1/storage/timelines`が404の場合、Agent検索へ進む前に検出できる。フォークへ`c785e04c9`として同期済み。
- 動画再登録を試行し、NVStreamerへの単一チャンクuploadはHTTP 200で`sensorId`を取得できた。しかしローカルNVStreamer単体構成では`/vst/api/v1/storage/timelines`と`/vst/api/v1/storage/file/{sensor}/url`が404となり、Agentの`/complete`はタイムライン取得で502になった。起動順序の問題ではなく、storage adaptor/streamprocessingを含むVST構成が必要な制約である。推測でAgentに代替タイムラインを実装せず、正式VST構成または既存検証データを使う方針とする。検証用コンテナは停止済み。
- 2026-08-28 Alert回帰確認: `vss-alert-bridge:local`を再ビルドし、Redisとともに起動して`GET /health` HTTP 200を確認した。`POST /api/v1/verification/ondemand`は入力不足時にHTTP 400、正常なIncidentではHTTP 202を返した。
- 実動画`konro_inspection.mp4`をHTTP配信し、Alertのオンデマンド検証からOpenAI互換RT-VLM（`vss-rt-vlm:local`）へ接続した。`VLM response received (direct video)`（約4.7秒）とElasticsearch `mdx-vlm-incidents-2026-08-28`への保存を確認した。疑似URLではRT-VLMのffprobe失敗も`verification-failed`文書として保存され、失敗時の永続化契約も維持された。
- Alertの`vlm.base_url`は現状環境変数ではなく`config.yaml`が優先されるため、NIMを使わない実行では設定ファイルをOpenAI互換URLへ切り替える必要がある。`rtvi_vlm.base_url`用の`RTVI_VLM_BASE_URL`とは別設定である。
- Alertの設定ロードに`VLM_BASE_URL`、`VLM_MODEL`、`VLM_API_KEY`の環境変数上書きを追加した。NIM、ローカルvLLM/Ollama、OpenAI/Qwen等の外部OpenAI互換APIを同じイメージで切り替えられる。
- LLM側は既に`LLM_MODEL_TYPE`、`LLM_BASE_URL`、`LLM_NAME`、`OPENAI_API_KEY`で独立切替できることを確認した。VLM側の`VLM_MODEL_TYPE`/`VLM_BASE_URL`と組み合わせ、LLMだけ外部、VLMだけローカルなどの構成を選べる。READMEにも構成表を追加した。
- 将来のRTVI-Embed置換候補として、EmbeddingGemmaはテキスト専用の可能性があるため、Qwen3-VL-EmbeddingをvLLMのOpenAI互換APIで提供する案を記録する。性能評価より先に、画像・動画・テキストの同一埋め込み空間、ベクトル次元・正規化、モデルおよび派生物のライセンス条件を確認する。現行のキャプション全文検索経路は変更しない。
