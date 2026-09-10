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
