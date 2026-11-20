#!/usr/bin/env bash
# PC(WSL) → Raspberry Pi 원격 작업 도구.
# capture/ 동기화, Pi에서 스크립트 실행, 결과를 data/ 아래로 회수하는 일을 명령 하나로 한다.
#
# 설정: 저장소 루트의 .pi.env (git 제외, .pi.env.example 참고) 또는 환경변수
#   PI_HOST  Pi 주소 (WSL2에서는 .local mDNS가 안 되므로 IP 권장)   [필수]
#   PI_USER  Pi 사용자 이름                                            [필수]
#   PI_PORT  SSH 포트 (기본 22)
#   PI_ROOT  Pi 쪽 작업 폴더, 홈 기준 상대 경로 (기본 cctvlog)
#
# 명령:
#   setup                      SSH 키 생성(없으면) + Pi에 공개키 등록 (비밀번호 입력, 1회)
#   agent                      고정 소켓(~/.ssh/cctvlog-agent.sock)으로 ssh-agent를 띄우고 키 등록
#                              (키에 passphrase가 있을 때. WSL을 재시작하면 다시 1회 실행)
#   check                      연결, Pi 모델·OS, picamera2, 카메라 목록, 디스크 확인
#   sync                       capture/ → Pi:$PI_ROOT/capture/
#   run SCRIPT [ARGS...]       sync 후 Pi에서 python3 SCRIPT ARGS 실행 (출력 실시간 표시)
#   job NAME [--dest DIR] SCRIPT [ARGS...]
#                              sync → Pi에서 SCRIPT --out $PI_ROOT/runs/NAME ARGS 실행
#                              → 결과를 DIR(기본 data/pi_runs/NAME)로 회수 + pi_job.json 기록
#   probe [ARGS...]            job YYYYMMDD_HHMMSS --dest data/pi_probe/<같은 이름> pi_probe.py ARGS
#   fetch REMOTE_REL LOCAL_DIR Pi:$PI_ROOT/REMOTE_REL 내용을 LOCAL_DIR로 복사
#   exec CMD...                Pi에서 임의 명령 실행 (홈 디렉터리 기준)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PI_ENV_FILE:-$REPO_ROOT/.pi.env}"
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
PI_PORT="${PI_PORT:-22}"
PI_ROOT="${PI_ROOT:-cctvlog}"
AGENT_SOCK="$HOME/.ssh/cctvlog-agent.sock"
# passphrase 있는 키: 'pi.sh agent'로 띄운 agent를 다른 셸(예: Claude Code)에서도 쓴다
if [[ -S "$AGENT_SOCK" ]] && { [[ -z "${SSH_AUTH_SOCK:-}" ]] || ! ssh-add -l >/dev/null 2>&1; }; then
  export SSH_AUTH_SOCK="$AGENT_SOCK"
fi

die() { echo "pi.sh: $*" >&2; exit 2; }
log() { echo "[pi.sh] $*" >&2; }

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

require_config() {
  [[ -n "${PI_HOST:-}" && -n "${PI_USER:-}" ]] ||
    die "PI_HOST/PI_USER가 없습니다. .pi.env.example을 .pi.env로 복사해 채우세요."
  [[ "$PI_ROOT" != /* && "$PI_ROOT" != *..* ]] || die "PI_ROOT는 홈 기준 상대 경로여야 합니다: $PI_ROOT"
  TARGET="$PI_USER@$PI_HOST"
  mkdir -p "$HOME/.ssh/cm"
  SSH_OPTS=(
    -p "$PI_PORT"
    -o ConnectTimeout=8
    -o StrictHostKeyChecking=accept-new
    -o ServerAliveInterval=15
    -o ControlMaster=auto
    -o "ControlPath=$HOME/.ssh/cm/%r@%h:%p"
    -o ControlPersist=10m
  )
}

remote() { ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$TARGET" "$@"; }

# 원격 명령 문자열을 안전하게 조립 (인자마다 %q 인용)
quote_args() { local out="" a; for a in "$@"; do out+=" $(printf '%q' "$a")"; done; printf '%s' "${out# }"; }

have_remote_rsync() { command -v rsync >/dev/null && remote 'command -v rsync >/dev/null' 2>/dev/null; }

check_name() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || die "이름에는 영문·숫자·._-만 쓸 수 있습니다: $1"; }

cmd_setup() {
  require_config
  local key="$HOME/.ssh/id_ed25519"
  if [[ ! -f "$key" ]]; then
    log "SSH 키 생성: $key (passphrase는 원하면 입력, 비워도 됨)"
    ssh-keygen -t ed25519 -f "$key" -C "cctvlog-pc"
  fi
  log "Pi에 공개키 등록 (Pi 비밀번호 입력)"
  ssh-copy-id -i "$key.pub" -p "$PI_PORT" -o StrictHostKeyChecking=accept-new "$TARGET"
  cmd_check
}

cmd_agent() {
  if [[ -S "$AGENT_SOCK" ]] && SSH_AUTH_SOCK="$AGENT_SOCK" ssh-add -l >/dev/null 2>&1; then
    log "agent 실행 중, 키 등록됨: $AGENT_SOCK"
  else
    if ! SSH_AUTH_SOCK="$AGENT_SOCK" ssh-add -l >/dev/null 2>&1; then
      # 키가 없는 정상 agent는 rc=1, 죽은 소켓은 rc=2
      local rc=0; SSH_AUTH_SOCK="$AGENT_SOCK" ssh-add -l >/dev/null 2>&1 || rc=$?
      if [[ $rc -eq 2 ]]; then
        rm -f "$AGENT_SOCK"
        ssh-agent -a "$AGENT_SOCK" >/dev/null
        log "ssh-agent 시작: $AGENT_SOCK"
      fi
    fi
    log "키 등록 (passphrase 입력)"
    SSH_AUTH_SOCK="$AGENT_SOCK" ssh-add "$HOME/.ssh/id_ed25519"
  fi
  export SSH_AUTH_SOCK="$AGENT_SOCK"
}

cmd_check() {
  require_config
  remote 'bash -s' <<'EOF'
set -u
echo "host:      $(hostname)"
echo "model:     $(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo '?')"
echo "os:        $(. /etc/os-release && echo "$PRETTY_NAME")"
echo "python3:   $(python3 --version 2>&1)"
python3 -c 'import importlib.metadata as m; print("picamera2:", m.version("picamera2"))' 2>/dev/null \
  || echo "picamera2: (pip 메타데이터 없음) $(dpkg-query -W -f='${Version}' python3-picamera2 2>/dev/null || echo 미설치)"
echo "rsync:     $(command -v rsync || echo 없음 — tar로 전송)"
echo "disk:      $(df -h ~ | awk 'NR==2{print $4" free of "$2}')"
echo "--- rpicam-hello --list-cameras"
timeout 20 rpicam-hello --list-cameras 2>&1 | head -20
EOF
}

cmd_sync() {
  require_config
  remote "mkdir -p $(quote_args "$PI_ROOT/capture" "$PI_ROOT/runs")"
  if have_remote_rsync; then
    rsync -a --delete --exclude='__pycache__/' -e "ssh ${SSH_OPTS[*]}" \
      "$REPO_ROOT/capture/" "$TARGET:$PI_ROOT/capture/"
  else
    tar -C "$REPO_ROOT/capture" --exclude='__pycache__' -cf - . |
      remote "tar -C $(quote_args "$PI_ROOT/capture") -xf -"
  fi
  log "synced capture/ → $TARGET:$PI_ROOT/capture/"
}

cmd_fetch() {
  require_config
  [[ $# -eq 2 ]] || die "usage: fetch REMOTE_REL LOCAL_DIR"
  local src="$PI_ROOT/$1" dst="$2"
  [[ "$1" != /* && "$1" != *..* ]] || die "REMOTE_REL은 PI_ROOT 기준 상대 경로여야 합니다"
  mkdir -p "$dst"
  if have_remote_rsync; then
    rsync -a -e "ssh ${SSH_OPTS[*]}" "$TARGET:$src/" "$dst/"
  else
    remote "tar -C $(quote_args "$src") -cf - ." | tar -C "$dst" -xf -
  fi
  log "fetched $TARGET:$src → $dst"
}

cmd_run() {
  [[ $# -ge 1 ]] || die "usage: run SCRIPT [ARGS...]"
  local script="$1"; shift
  [[ "$script" =~ ^[A-Za-z0-9_.-]+\.py$ && -f "$REPO_ROOT/capture/$script" ]] ||
    die "SCRIPT는 capture/ 안의 .py 파일 이름이어야 합니다: $script"
  cmd_sync
  log "run on Pi: $script $*"
  # -t: Ctrl+C가 Pi 쪽 프로세스까지 전달되도록 (터미널이 있을 때만)
  local tty=(); [[ -t 0 && -t 1 ]] && tty=(-t)
  ssh "${SSH_OPTS[@]}" -o BatchMode=yes "${tty[@]}" "$TARGET" \
    "cd $(quote_args "$PI_ROOT/capture") && python3 -u $(quote_args "$script" "$@")"
}

write_job_json() {
  # $1=dest $2=name $3=exit_code $4=started $5=finished, 나머지 = script + args
  local dest="$1" name="$2" rc="$3" t0="$4" t1="$5"; shift 5
  python3 - "$dest/pi_job.json" "$name" "$rc" "$t0" "$t1" "$TARGET" "$PI_ROOT" \
    "$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)" \
    "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no -- capture | wc -l)" "$@" <<'EOF'
import json, sys
path, name, rc, t0, t1, target, root, commit, dirty, script, *args = sys.argv[1:]
json.dump({
    "name": name, "script": script, "args": args, "exit_code": int(rc),
    "started_utc": t0, "finished_utc": t1, "pi": target, "pi_root": root,
    "remote_out": f"{root}/runs/{name}", "git_commit": commit,
    "capture_dirty": int(dirty) > 0,
}, open(path, "w"), indent=2, ensure_ascii=False)
EOF
}

cmd_job() {
  [[ $# -ge 2 ]] || die "usage: job NAME [--dest DIR] SCRIPT [ARGS...]"
  local name="$1"; shift
  check_name "$name"
  local dest="$REPO_ROOT/data/pi_runs/$name"
  if [[ "${1:-}" == "--dest" ]]; then dest="$2"; shift 2; fi
  [[ $# -ge 1 ]] || die "SCRIPT가 필요합니다"
  [[ -e "$dest" ]] && die "로컬 결과 폴더가 이미 있습니다: $dest"
  require_config
  local out="$PI_ROOT/runs/$name"
  remote "test ! -e $(quote_args "$out")" || die "Pi에 이미 같은 이름의 결과가 있습니다: $out"

  local script="$1"; shift
  local t0 rc=0 t1
  t0="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # --out은 홈 기준 경로로 넘긴다 (capture/에서 실행되므로 ../runs/NAME)
  cmd_run "$script" --out "../runs/$name" "$@" || rc=$?
  t1="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if remote "test -d $(quote_args "$out")"; then
    cmd_fetch "runs/$name" "$dest"
    write_job_json "$dest" "$name" "$rc" "$t0" "$t1" "$script" "$@"
    log "result: $dest (exit $rc)"
  else
    log "Pi에 결과 폴더가 생기지 않았습니다: $out (exit $rc)"
  fi
  return "$rc"
}

cmd_probe() {
  local name; name="$(date +%Y%m%d_%H%M%S)"
  cmd_job "$name" --dest "$REPO_ROOT/data/pi_probe/$name" pi_probe.py "$@"
}

cmd_exec() {
  require_config
  [[ $# -ge 1 ]] || die "usage: exec CMD..."
  remote "$@"
}

main() {
  local sub="${1:-help}"; shift || true
  case "$sub" in
    setup) cmd_setup "$@" ;;
    agent) cmd_agent "$@" ;;
    check) cmd_check "$@" ;;
    sync) cmd_sync "$@" ;;
    run) cmd_run "$@" ;;
    job) cmd_job "$@" ;;
    probe) cmd_probe "$@" ;;
    fetch) cmd_fetch "$@" ;;
    exec) cmd_exec "$@" ;;
    help|-h|--help) usage ;;
    *) usage >&2; die "unknown command: $sub" ;;
  esac
}

main "$@"
