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
