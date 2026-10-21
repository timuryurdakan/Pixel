#!/bin/sh
# Pixel Office — macOS/Linux bootstrap (CLOUD.md §7.5). Read it before running it:
#
#   curl -fsSL https://<host>/hook/install.sh | sh -s -- --server https://<host> --room <roomId> --key <writeKey> --name "Timur"
#
# 1. finds python3 >= 3.8 (else pwsh, else prints manual instructions)
# 2. downloads pixel_hook.py (and pixel_agent.py, pixel_antigravity.py) from --server into ~/.pixel-office/
# 3. runs `pixel_hook.py install …` (writes config.json, merges hooks into ~/.claude/settings.json with a
#    backup, sends a test character).
# Env: PIXEL_OFFICE_HOME (default ~/.pixel-office), PIXEL_OFFICE_CLAUDE_SETTINGS (default ~/.claude/settings.json).
#
# Phase 16 (docs/SECURITY_AUDIT.md S16-05): everything runs inside main(), called on the last line, so a download cut
# off during `curl … | sh` runs nothing. --server must be https://, or plain http:// only for a local-mode server on
# this machine or the LAN (loopback, RFC 1918 addresses, *.local / *.lan names), as in CLOUD.md §16.
set -eu

main() {

SERVER=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--server" ]; then SERVER="$a"; fi
  case "$a" in --server=*) SERVER="${a#--server=}" ;; esac
  prev="$a"
done
if [ -z "$SERVER" ]; then
  echo "Hata: --server https://<host> gerekli." >&2
  exit 2
fi
SERVER="${SERVER%/}"
case "$SERVER" in
  https://?*) ;;
  http://127.*|http://localhost|http://localhost:*|http://\[::1\]*|http://10.*|http://192.168.*) ;;
  http://172.1[6-9].*|http://172.2[0-9].*|http://172.3[01].*) ;;
  http://*.local|http://*.local:*|http://*.lan|http://*.lan:*) ;;
  http://?*)
    host="${SERVER#http://}"
    host="${host%%[:/]*}"
    case "$host" in
      *.*|"")   # a public DNS name over plain http: a network attacker could swap the downloaded hook
        echo "Hata: --server https:// ile başlamalı (http:// yalnızca bu bilgisayardaki ya da yerel ağdaki sunucu için)." >&2
        exit 2
        ;;
    esac
    ;;
  *)
    echo "Hata: --server https:// ile başlamalı (http:// yalnızca bu bilgisayardaki ya da yerel ağdaki sunucu için)." >&2
    exit 2
    ;;
esac
HOME_DIR="${PIXEL_OFFICE_HOME:-$HOME/.pixel-office}"
mkdir -p "$HOME_DIR"
chmod 700 "$HOME_DIR" 2>/dev/null || true

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$2.tmp"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2.tmp" "$1"
  else
    echo "Hata: curl ya da wget bulunamadı." >&2
    return 1
  fi
  mv "$2.tmp" "$2"
}

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"
    break
  fi
done

if [ -n "$PY" ]; then
  fetch "$SERVER/hook/pixel_hook.py" "$HOME_DIR/pixel_hook.py"
  fetch "$SERVER/hook/pixel_agent.py" "$HOME_DIR/pixel_agent.py" || true
  fetch "$SERVER/hook/pixel_antigravity.py" "$HOME_DIR/pixel_antigravity.py" || true
  exec "$PY" "$HOME_DIR/pixel_hook.py" install "$@"
fi

if command -v pwsh >/dev/null 2>&1; then
  fetch "$SERVER/hook/pixel_hook.ps1" "$HOME_DIR/pixel_hook.ps1"
  # translate --long-options to PowerShell -Parameters
  set -- install "$@"
  ARGS=""
  for a in "$@"; do
    p=""
    case "$a" in
      --server) p="-Server" ;; --room) p="-Room" ;; --key) p="-Key" ;; --name) p="-Name" ;;
      --color) p="-Color" ;; --settings-path) p="-SettingsPath" ;; --no-test) p="-NoTest" ;;
      --command-style) p="-CommandStyle" ;; --force-powershell) continue ;;
    esac
    if [ -n "$p" ]; then
      ARGS="$ARGS $p"                                   # parameter names must stay unquoted
    else
      ARGS="$ARGS '$(printf '%s' "$a" | sed "s/'/''/g")'"
    fi
  done
  PS1_PATH="$(printf '%s' "$HOME_DIR/pixel_hook.ps1" | sed "s/'/''/g")"
  exec pwsh -NoProfile -NonInteractive -Command "& '$PS1_PATH' $ARGS"
fi

cat >&2 <<'EOF'
Hata: python3 (>= 3.8) ya da pwsh bulunamadı.
Python 3 kurun (macOS: `xcode-select --install` veya python.org; Linux: paket yöneticiniz) ve komutu tekrar çalıştırın.
Kurulum yapamıyorsanız sayfadaki "Gelişmiş" sekmesindeki HTTP kancası seçeneğine bakın (gizlilik ödünleşimini okuyun).
EOF
exit 1
}

main "$@"
