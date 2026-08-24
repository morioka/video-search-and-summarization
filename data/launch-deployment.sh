#!/usr/bin/env bash
# =============================================================================
#  launch-deployment.sh — run a staged dataset on either deployment.
#
#  Each target owns its own launch configuration, written immediately before it
#  starts, so launching one never reconfigures the other:
#
#    standalone   $RTCV/docker/.env          MODELS_DIR, NUM_CAMS, VIDEO_DIR
#    blueprint    deepstream/configs/        tracker camera map + pub/sub graph
#                 overrides.env              NUM_STREAMS
#
#  The blueprint's two files are tracked — it bind-mounts deepstream/configs
#  directly and has no staging step, so the per-dataset camera map has to live
#  in the repo. Revert with:
#    git checkout -- deploy/docker/industry-profiles/warehouse-operations/
#
#  Run ./setup-data.sh sync <dataset> first: this script launches, it does not
#  stage, and it refuses if the staged camera count does not match the dataset.
#
#  Usage:
#    ./launch-deployment.sh standalone up   <dataset>
#    ./launch-deployment.sh blueprint  up   <dataset> [extra blueprint-deploy.sh flags]
#    ./launch-deployment.sh standalone down
#    ./launch-deployment.sh blueprint  down
#
#  Env — standalone:
#    INPUT_MODE=stream|file   stream (default) pulls RTSP from the testbed; file
#                             plays VIDEO_DIR/<sensor_id>.mp4 once, needing no
#                             testbed and no stream registration.
#    OSD=0|1                  live tiled window (needs a display + `xhost +`)
#    SAVE_VIDEO=0|1           write video-output/grid-view.mkv
#  Env — blueprint:
#    BP_PROFILE=bp_wh_kafka|bp_wh_redis|bp_wh_auto_calib   (default bp_wh_kafka)
#  Env — both:
#    NO_FOLLOW=1              do not tail the perception log at the end
#
#  `blueprint up` always passes -m mv3dt. blueprint-deploy.sh hardcodes MODE=2d
#  when -m is absent — unlike BP_PROFILE and HARDWARE_PROFILE it does NOT fall
#  back to overrides.env — so a forgotten flag silently yields a 2D stack, with
#  the profile cascading to bp_wh and pulling the LLM/VLM NIMs.
# =============================================================================
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

INPUT_MODE="${INPUT_MODE:-stream}"
OSD="${OSD:-0}"
SAVE_VIDEO="${SAVE_VIDEO:-0}"
BP_PROFILE="${BP_PROFILE:-bp_wh_kafka}"
LAUNCHER="$VSS_REPO/deploy/docker/scripts/blueprint-deploy.sh"
DSC="$WHBP_APP/deepstream/configs"

usage() { sed -n '2,/^# ====/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//; s/^#$//'; }

# ─────────────────────────────────────────────────────────────────────────────
#  Shared preflight
# ─────────────────────────────────────────────────────────────────────────────

# preflight <target> <dataset> — resolve the dataset into DS_* / NUM, and prove
# the staged camInfo belongs to it. Both deployments stage camInfo into a single
# per-dataset location that is overwritten in place, so a leftover map from the
# previous dataset is easy to hit, silent, and fatal: streams get matched to the
# wrong projection models and the tracker dies seconds in.
preflight() {
  local tgt="$1" target="$2" caminfo staged
  load_dataset "$target"

  [ -d "$DS_VIDEOS" ] || die "videos not present: $DS_VIDEOS — run ./setup-data.sh sync $target"
  NUM=$(video_stems "$DS_VIDEOS" | grep -c . || true)
  [ "${NUM:-0}" -gt 0 ] || die "no .mp4 files in $DS_VIDEOS"

  case "$tgt" in
    standalone) caminfo="$RTCV/generated/camInfo" ;;
    blueprint)  caminfo="$WHBP_APP/calibration/sample-data/$DS_NAME/camInfo" ;;
  esac
  staged=$(ls -1 "$caminfo"/*.yml 2>/dev/null | wc -l | tr -d ' ')

  # generated/ is a single staging area that sync overwrites per dataset, so
  # after `sync all` it holds whichever dataset came last in the registry. sync
  # also keeps a per-dataset copy under standalone/datasets/<alias>/, so
  # activate that rather than making the user re-sync the one they just named.
  if [ "$tgt" = standalone ] && [ "$staged" != "$NUM" ]; then
    local sd="$STANDALONE/datasets/$DS_ALIAS"
    if [ "$(ls -1 "$sd/camInfo"/*.yml 2>/dev/null | wc -l | tr -d ' ')" = "$NUM" ]; then
      echo "  generated/ held $staged camera(s) — activating $DS_ALIAS"
      mkdir -p "$caminfo"; rm -f "$caminfo"/*.yml
      cp -a "$sd/camInfo/." "$caminfo/"
      [ -f "$sd/pub_sub_info_config.yml" ] \
        && cp "$sd/pub_sub_info_config.yml" "$RTCV/generated/pub_sub_info_config.yml"
      chmod -R o+rX "$RTCV/generated"
      staged="$NUM"
    fi
  fi

  [ "$staged" != 0 ] || die "no camInfo staged — run ./setup-data.sh sync $target"
  [ "$staged" = "$NUM" ] \
    || die "camInfo has $staged camera(s), $DS_NAME has $NUM — run ./setup-data.sh sync $target"
}

# ─────────────────────────────────────────────────────────────────────────────
#  standalone
# ─────────────────────────────────────────────────────────────────────────────

vst() { ( cd "$STANDALONE/pipeline1" && DATASET="$1" ./vst-stack.sh "$2" ); }

standalone_up() {
  preflight standalone "$1"

  # The OSD sink is EGL: it needs a display backed by the GPU. Over SSH X11
  # forwarding it cannot create a GL context, so the pipeline never reaches
  # PAUSED and the app exits — leaving add-streams.sh to wait its full 600s for
  # a container that is already gone. Refuse early instead.
  if [ "$OSD" = 1 ] && [ "${ALLOW_REMOTE_OSD:-0}" != 1 ]; then
    if [ -z "${DISPLAY:-}" ]; then
      die "OSD=1 needs a display but DISPLAY is unset. Drop OSD=1, or use SAVE_VIDEO=1 INPUT_MODE=file for a headless recording (DEPLOY.md §4.3)."
    elif [ -n "${SSH_CONNECTION:-}" ] && [[ "$DISPLAY" == localhost:* ]]; then
      die "OSD=1 with DISPLAY=$DISPLAY is an SSH-forwarded display; the EGL sink cannot render there and perception will exit. Drop OSD=1, or use SAVE_VIDEO=1 INPUT_MODE=file (DEPLOY.md §4.3). Override with ALLOW_REMOTE_OSD=1 if you know this display is GPU-backed."
    fi
  fi

  step "Configuring $DS_NAME ($NUM cameras)"
  local out
  out=$( set_env_key "$RTCV/docker/.env" MODELS_DIR "$APP_DATA/models"
         set_env_key "$RTCV/docker/.env" NUM_CAMS   "$NUM"
         set_env_key "$RTCV/docker/.env" VIDEO_DIR  "$DS_VIDEOS" )
  [ -n "$out" ] && echo "$out" || echo "  docker/.env already correct"

  if [ "$INPUT_MODE" = stream ]; then
    [ -d "$STANDALONE/pipeline1" ] || die "testbed not at $STANDALONE — see DEPLOY.md §2.2"
    step "Starting the synchronized streams (DATASET=$DS_ALIAS)"
    vst "$DS_ALIAS" up
  fi

  step "Staging DeepStream configs"
  ( cd "$RTCV" && NUM_CAMS="$NUM" OSD="$OSD" INPUT_MODE="$INPUT_MODE" \
      SAVE_VIDEO="$SAVE_VIDEO" ${DS_TRACKER:+TRACKER_CONFIG="$DS_TRACKER"} \
      ./scripts/stage-configs.sh )

  step "Launching perception + BEV fusion"
  ( cd "$RTCV/docker" && NUM_CAMS="$NUM" COMPOSE_PROFILES=mosquitto,kafka docker compose up -d )

  if [ "$INPUT_MODE" = stream ]; then
    step "Registering streams"
    # Each stream may be served on its own port — use the printed URLs verbatim.
    vst "$DS_ALIAS" urls | grep -o 'rtsp://[^ ]*' \
      | while read -r u; do echo "${u##*/}=$u"; done > "$RTCV/my-streams.txt"
    echo "  $(wc -l < "$RTCV/my-streams.txt") stream(s) -> my-streams.txt"
    # add-streams.sh waits for ds-ready, which covers the TensorRT engine build.
    ( cd "$RTCV" && ./scripts/add-streams.sh --file my-streams.txt )
  else
    echo "  INPUT_MODE=file — clips play once, no registration; container exits at EOS."
  fi

  grn "
Up. Verify (DEPLOY.md §4.4):
  cd $RTCV && ./scripts/kafka-dump.sh --topic mdx-raw --count 20
  cd $RTCV && BEV_DATASET_PATH=$STANDALONE/datasets/$DS_ALIAS ./scripts/bev-visualizer.sh"
  follow_log
}

standalone_down() {
  step "Stopping the component"
  ( cd "$RTCV/docker" && docker compose --profile "*" down ) || true
  if [ -d "$STANDALONE/pipeline1" ]; then
    step "Stopping the testbed"
    ( cd "$STANDALONE/pipeline1" && ./vst-stack.sh down ) || true
  fi
  grn "Down."
}

# ─────────────────────────────────────────────────────────────────────────────
#  blueprint
# ─────────────────────────────────────────────────────────────────────────────

blueprint_up() {
  local target="$1"; shift
  [ -x "$LAUNCHER" ] || die "launcher not found: $LAUNCHER"
  preflight blueprint "$target"

  step "Configuring the blueprint for $DS_NAME"
  local sdd="$WHBP_APP/calibration/sample-data/$DS_NAME"
  [ -f "$sdd/pub_sub_info_config.yml" ] \
    || die "no pub/sub staged for $DS_NAME — run ./setup-data.sh sync $target"
  cp "$sdd/pub_sub_info_config.yml" "$DSC/pub_sub_info_config.yml"
  local entries; entries=$(video_stems "$DS_VIDEOS" | while read -r c; do
    printf '    %s: /tmp/camInfo/%s.yml\n' "$c" "$c"; done)
  awk -v entries="$entries" '
    /^[[:space:]]+cameraModelFilepath:[[:space:]]*$/ { print; print entries; skip=1; next }
    skip==1 && /^[[:space:]][[:space:]][[:space:]][[:space:]][^[:space:]]/ { next }
    { skip=0; print }
  ' "$DSC/ds-mv3dt-tracker-config.yml" > "$DSC/ds-mv3dt-tracker-config.yml.tmp" \
    && mv "$DSC/ds-mv3dt-tracker-config.yml.tmp" "$DSC/ds-mv3dt-tracker-config.yml"
  set_env_key "$PROFILE/overrides.env" NUM_STREAMS "$NUM" >/dev/null
  # Containers run as a non-root user; a host umask of 027 leaves these
  # group-only and the configurator cannot read them. No sudo: they are ours.
  chmod o+r "$DSC"/*.yml "$DSC"/*.txt 2>/dev/null \
    || ylw "  could not chmod o+r deepstream/configs — the configurator may fail to read them"
  echo "  tracker map + pub/sub -> $NUM camera(s); NUM_STREAMS=$NUM"

  step "Deploying $DS_NAME  (-m mv3dt -p $BP_PROFILE)"
  "$LAUNCHER" up -d warehouse -m mv3dt -p "$BP_PROFILE" -D "$APP_DATA" -s "$DS_NAME" "$@"

  grn "
Up. Verify (DEPLOY.md §5.3):
  docker ps --format 'table {{.Names}}\\t{{.Status}}'
  docker logs vss-configurator-mv3dt 2>&1 | tail -40
  http://\${HOST_IP}:30888/vst/  — VST UI, live streams with 3D overlay"
  follow_log
}

blueprint_down() {
  [ -x "$LAUNCHER" ] || die "launcher not found: $LAUNCHER"
  step "Tearing down"
  "$LAUNCHER" down -D "$APP_DATA" "$@"
  grn "Down."
}

follow_log() {
  [ "${NO_FOLLOW:-0}" = 1 ] && return 0
  step "Perception log (Ctrl-C to stop following)"
  docker logs -f vss-rtvi-cv-mv3dt
}

# ─────────────────────────────────────────────────────────────────────────────

TARGET="${1:-}"; ACTION="${2:-}"
case "$TARGET" in
  ""|-h|--help|help) usage; exit 0 ;;
  standalone|blueprint) ;;
  *) die "unknown target: $TARGET (expected standalone or blueprint)" ;;
esac
shift 2 2>/dev/null || shift 1 2>/dev/null || true

case "$ACTION" in
  up)   [ -n "${1:-}" ] || die "usage: launch-deployment.sh $TARGET up <dataset>"
        "${TARGET}_up" "$@" ;;
  down) "${TARGET}_down" "$@" ;;
  *)    die "unknown action: ${ACTION:-<none>} (expected up or down)" ;;
esac
