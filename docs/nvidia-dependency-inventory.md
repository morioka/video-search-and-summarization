# NVIDIA依存インベントリ

このリポジトリには、元のNVIDIA構成とライセンスフリー検証構成が併存する。
依存を削除するのではなく、実行経路ごとに区別して管理する。

## ライセンスフリー経路で使用しないもの

- `nvcr.io/nvidia/vss-core/*` のVSSアプリケーションイメージ
- `nvcr.io/nim/*` のNIMイメージとNVIDIA NIM Operator
- `services/vios/prebuilts/x86_64/*.so` のDeepStream/VIOS配布バイナリ
- `services/alert/deploy_docker-compose.yml` の配布Alertイメージ
- `services/video-summarization/docker/Dockerfile` が参照するVIA/NVIDIAベースイメージ

## ライセンスフリー経路で使用するもの

- `vss-rt-vlm-openai:local`: OpenAI互換APIを使うローカル実装
- `vss-vst-storage-local:dev`: VST Storage互換のローカル実装
- `vss-video-analytics-api-local:dev`: Alerts API互換のローカル実装
- `vss-alert-bridge:local`: Alert Bridgeのローカルビルド
- 公開イメージのKafka、Elasticsearch、Redis

起動は `deploy/docker/scripts/start-license-free-lvs.sh`、検証は
`deploy/docker/scripts/verify-license-free-lvs.sh` を使用する。後者には稼働中VLM
イメージがNVIDIA NGC由来でないことの検査が含まれる。

## 注意

Apache-2.0のソースコードであっても、実行時にNVIDIA配布バイナリやNVIDIA限定
イメージを必要とする場合は、ライセンスフリー経路とは扱わない。

## 2026-09-10 監査結果

現在の`dev-profile-lvs`は、アプリケーション機能を次のように分離している。

- 完全に汎用ベース: `vss-rt-vlm-openai`、`vss-nvstreamer-local`、
  `vss-vst-storage-local`、`vss-video-analytics-api-local`
- ローカルタグだがNVIDIAベース層を継承: `vss-agent-local`、`vss-lvs-local`、
  `vss-ui-local`

後者はコードの差し替え・設定上書きにより検証経路を動かせるが、ベースイメージ
由来のNVIDIAランタイム依存を含む。完全なライセンス非依存化には、Agent/LVS/UI
それぞれを`python:3.x`または`node:22`等の汎用ベースから再構築する作業が残る。
現時点では、VLM推論、NVStreamer、VST Storage、Alerts API/Bridgeの主要経路を
NVIDIA配布アプリイメージなしで再現できることを優先している。

Agentについては、`services/agent/docker/Dockerfile`の`RUNTIME_BASE`をbuild引数化
し、`python:3.13-slim-bookworm`を指定した汎用イメージのビルドを確認した。
依存233パッケージの導入、codecライブラリ検査、`vss_agents`・FastAPI・LiteLLMの
importは成功している。実サービスとしてのAgent起動置換は、次段階で疎通検証する。

その後、license-free overrideへAgentの自動ビルドを組み込み、
`vss-agent-generic:dev`でhealth・Alerts・Storageを含む全体スモークテストに成功した。
UIも`UI_RUNTIME_BASE=node:22-bookworm-slim`でビルド・起動でき、3000番のHTTP応答を
確認済みである。

LVS（`services/video-summarization`）は単純なbase image置換の対象外である。
`start_via.sh`は`via-engine/via_server.py`、`/opt/nvidia/via`の実行環境、VIA/CUDA
ランタイムを前提とし、DockerfileもNVIDIA Ubuntu builderと`via-engine-base`を
必要とする。`vss-ctx-rag`を公開Python依存へ置き換えるだけでは起動経路を再現
できないため、現行license-free構成ではLVSを主要起動経路から分離している。
完全非依存化する場合は、保存動画のチャンク処理・要約APIを公開Python/FFmpegで
機能限定再実装する方針が現実的である。
