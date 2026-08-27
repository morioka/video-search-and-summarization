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
| VSS全体への組み込み | 未完了 | LVS、Agent、検索・要約を含む統合が必要 |
| RTSP、Kafka、音声 | 未実装 | 将来対応として分離 |

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
- [ ] KafkaからLogstash、Elasticsearchまでの実メッセージ登録を確認する。
- [ ] UI登録からRT-VLMキャプション検索までの自動経路を確認する。

本日のDocker検証環境は終了時に停止する。次回はCompose起動前にKafkaデータ、Elasticsearch volume、ホストRedisのポート競合を確認する。

終了処理: 2026-08-27にVSS検証用コンテナ（Agent、LVS、RT-VLM、VIOS、Kafka、Elasticsearch、Kibana、Logstash、UI、ingress等）が停止状態であることを確認した。Dockerコンテナは削除せず、永続ボリュームは保持している。

### フェーズA: VSS全体への統合（最優先）

- [x] `RTVI_VLM_IMAGE=vss-rt-vlm-openai:test`でLVSプロファイルを起動した。
- [x] Compose内のRT-VLM healthcheckがhealthyになることを確認した。
- [x] 復旧後の公開エンドポイント（LVS `38111`、RT-VLM `8018`、UI `3000`）がHTTP 200になることを確認した。
- [x] Agentの互換動画登録APIへ`konro_inspection.mp4`を登録し、VST完了APIが200を返すことを確認した。

AgentのOpenAIチャットAPI（`/v1/chat/completions`）では、動画一覧と`konro_inspection`への質問応答がHTTP 200で動作した。ただし質問応答はAgentの`video_understanding`による直接VLM経路であり、RT-VLMキャプションをElasticsearchから検索した結果ではない。動画中で未観測の電池取り外しまで推定する回答もあり、根拠区間付き検索としては未完了である。

Agentの`POST /api/v1/videos/{sensor_id}/complete`は、現実装ではVSTのタイムライン・動画URL取得後にRTVI-CVとRTVI Embedだけを呼ぶ。RTVI VLMの保存動画キャプション呼び出しやKafkaキャプション発行は含まれないため、RT-VLMを置き換えただけではUI登録から検索用キャプション登録まで自動接続されない。
- [ ] LVS UIから動画を登録し、キャプションが検索用メタデータへ登録されることを確認する（Agent API単体では登録成功、RT-VLM自動実行は未確認）。
- [ ] Agent側LLMをOpenAIへ設定し、検索、質問応答、要約まで通す。
- [x] Agent側LLMをOpenAIへ設定し、動画一覧と直接VLM質問応答を確認した（検索メタデータ経由ではない）。

この直接VLM経路は、RT-VLMのチャンクキャプションを検索して根拠区間を返す経路とは分けて評価する。
- [ ] OpenAI版RT-VLMでは不要なNVIDIA runtimeとGPU予約を、環境に応じて無効化できる構成を検討する。
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

- [ ] 同時実行数と待ち行列へ上限を設ける。
- [ ] OpenAI rate limitと一時障害の再試行方針を定める。
- [ ] アップロードMIMEだけでなく実ファイル形式も検証する。
- [ ] API認証、監査ログ、秘密情報管理を追加する。
- [ ] 資産保存先を永続ボリュームへ固定し、保持期限を実装する。
- [ ] request ID、チャンク時間、OpenAIレイテンシ、トークン数をメトリクス化する。
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
## 2026-08-27: Agent から Elasticsearch 検索までの疎通確認

- VST の `vst_video_list` が 502 になる問題は、`vss-vios-postgres` 停止後に sensor が再起動されていなかったことが原因だった。PostgreSQL 起動後に `vss-vios-sensor` を再起動し、`GET http://127.0.0.1:30888/vst/api/v1/sensor/streams` が 200 で `konro_inspection` を返すことを確認した。
- Elasticsearch の dynamic mapping では `metadata.content_metadata.uuid`、`doc_type`、`camera_id` が `text` + `.keyword` になっていた。`services/agent/src/lib/knowledge/adapters/es_caption.py` の `term` 条件を `.keyword` に修正した。
- 修正後、Agent API に対して `lvs_caption_retrieval` を明示した問い合わせを実行し、Agent が `es_caption` backend 経由で Elasticsearch の 1 件を取得した。取得内容には、青い炎 `[3.733s]`、電池収納部の開閉 `[11.733s-15.233s]`、コンロを消してから電池収納部を開ける安全上の記述が含まれていた。
- これにより、`OpenAI RT-VLM -> Kafka (nv.VisionLLM) -> Logstash -> Elasticsearch -> Agent/lvs_caption_retrieval (es_caption)` の検索経路が実データで確認できた。
- VST sensor は Redis (`127.0.0.1:6379`) への接続失敗を警告しているが、ストリーム一覧 API と今回の検索経路には影響しなかった。`vss-vios-streamprocessing` はイメージ起動時の dpkg/gstreamer パッケージ不整合が残っており、別途課題とする。

### 保存動画の自動接続（実装開始）

- `/api/v1/videos/{sensor_id}/complete` に `RTVI_VLM_BASE_URL` の設定を追加した。
- 完了 API が VST の生成済み動画 URL を取得し、RT-VLM の `/v1/files` へ同じ sensor UUID でアップロードした後、`/v1/generate_captions` を呼び出す処理を追加した。RT-VLM が発行する Kafka protobuf の UUID と VST sensor UUID を一致させる設計である。
- まだ実コンテナで `/complete` からの自動実行は未検証。次は Agent イメージを再ビルドし、動画登録から Elasticsearch 登録までを実行する。
- [x] Agent イメージ `vss-agent-local:3.2.2` で `/complete` の実行を確認した。VST timeline、storage URL、RT-VLM `/v1/files`、`/v1/generate_captions`、Kafka、Logstash、Elasticsearch まで自動接続され、`chunks_processed: 1` と HTTP 200 を得た。
- [x] VST の新しい sensor UUID に対応する `default_5be874ef_...` index が作成され、`blue flame` の検索で 1 件のキャプション（青い炎 1.467 秒、収納部操作 5.200〜13.200 秒）が取得できた。
- streamprocessing は `VST_INSTALL_ADDITIONAL_PACKAGES=false` で再作成し、既存イメージ起動時の壊れた dpkg/gstreamer 追加インストールを回避した。恒久対応ではイメージ修正または package state の修復が必要。
- [x] 動画名だけを指定した Agent 問い合わせでも、`vst_video_list` → VST 名称/UUID 解決 → `lvs_caption_retrieval` → Elasticsearch の自動検索を確認した。重複していた `konro_inspection` は一覧の先頭 UUID を安定して採用し、`default_5be874ef_...` のキャプションを取得できた。
