#!/bin/sh
# Pixel Office bağlayıcı — macOS/Linux kurulumu (V3.md §5.4). Çalıştırmadan önce okuyun:
#
#   curl -fsSL https://<host>/connector/install.sh | sh -s -- --server https://<host> --room <oda> --key <anahtar> --name 'Ad'
#
# 1. python3 >= 3.8 bulur
# 2. <server>/connector/pixel-connector.pyz ve SHA256SUMS dosyalarını indirir, SHA-256'yı doğrular
# 3. ~/.pixel-office/connector/ (0700) içine kurar, ~/.pixel-office/bin/pixel-connector kısayolunu yazar
# 4. `pixel-connector setup …` (connector.json + cihaz anahtarı), ardından `install-service` (systemd kullanıcı servisi
#    ya da launchd). `--no-service` servis adımını atlar.
# Bağlayıcı kendini asla buluttan güncellemez; güncellemek için bu betiği yeniden çalıştırın.
# Ortam: PIXEL_OFFICE_HOME (varsayılan ~/.pixel-office).
set -eu

SERVER=""
NO_SERVICE=0
prev=""
for a in "$@"; do
  if [ "$prev" = "--server" ]; then SERVER="$a"; fi
  case "$a" in
    --server=*) SERVER="${a#--server=}" ;;
    --no-service) NO_SERVICE=1 ;;
  esac
  prev="$a"
done
if [ -z "$SERVER" ]; then
  echo "Hata: --server https://<host> gerekli." >&2
  exit 2
fi
SERVER="${SERVER%/}"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Hata: Python 3.8 ya da daha yenisi gerekli (python3 bulunamadı)." >&2
  exit 1
fi

OFFICE="${PIXEL_OFFICE_HOME:-$HOME/.pixel-office}"
CONN="$OFFICE/connector"
umask 077
mkdir -p "$CONN" "$OFFICE/bin"
chmod 700 "$OFFICE" "$CONN" 2>/dev/null || true

TMP="$(mktemp -d "${TMPDIR:-/tmp}/pixel-connector.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT INT TERM

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    echo "Hata: curl ya da wget bulunamadı." >&2
    return 1
  fi
}

fetch "$SERVER/connector/pixel-connector.pyz" "$TMP/pixel-connector.pyz"
fetch "$SERVER/connector/SHA256SUMS" "$TMP/SHA256SUMS"

EXPECTED="$(grep ' pixel-connector.pyz$' "$TMP/SHA256SUMS" | cut -d' ' -f1)"
ACTUAL="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/pixel-connector.pyz")"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "Hata: SHA-256 doğrulanamadı; dosya bozuk ya da değiştirilmiş. Kurulum durduruldu." >&2
  echo "  beklenen: ${EXPECTED:-(yok)}" >&2
  echo "  inen:     $ACTUAL" >&2
  exit 1
fi

mv "$TMP/pixel-connector.pyz" "$CONN/pixel-connector.pyz"
chmod 600 "$CONN/pixel-connector.pyz"
cat > "$OFFICE/bin/pixel-connector" <<EOF
#!/bin/sh
exec "$PY" "$CONN/pixel-connector.pyz" "\$@"
EOF
chmod 700 "$OFFICE/bin/pixel-connector"

echo "pixel-connector.pyz SHA-256: $ACTUAL"
echo "(README'deki sürüm özetiyle karşılaştırın.)"

# setup gets every argument except --no-service
first=1
for a in "$@"; do
  if [ $first -eq 1 ]; then set --; first=0; fi
  [ "$a" = "--no-service" ] || set -- "$@" "$a"
done
[ $first -eq 1 ] && set --

"$PY" "$CONN/pixel-connector.pyz" setup "$@"

if [ "$NO_SERVICE" -eq 0 ]; then
  "$PY" "$CONN/pixel-connector.pyz" install-service || echo "Uyarı: servis kurulamadı; elle çalıştırın: pixel-connector run" >&2
fi

echo ""
echo "Kurulum tamam. Sıradaki adım (bu tarayıcıyı eşleştirmek için):"
echo "  $OFFICE/bin/pixel-connector pair"
case ":$PATH:" in
  *":$OFFICE/bin:"*) ;;
  *) echo "İpucu: kısayolun her yerde çalışması için PATH'e ekleyin: export PATH=\"$OFFICE/bin:\$PATH\"" ;;
esac
