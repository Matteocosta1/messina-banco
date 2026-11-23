#!/usr/bin/env bash
# Costruisce il sistema del lettore (Debian 13 «trixie», solo software libero più il firmware delle schede di rete).
# Esce in banco/immagine/uscita/:
#   live/vmlinuz, live/initrd.img, live/filesystem.squashfs   per l'avvio dalla rete (li dà il server del banco)
#   messina-lettore.iso                                       per la chiavetta di riserva (si scrive con dd o balenaEtcher)
# Serve Debian (o un container debian:trixie) come root, con internet: circa 10-15 minuti e 4 GB di disco.
# La stessa cosa la fa GitHub da solo (Actions → Lettore), e il server del banco con installa.sh.
set -euo pipefail
QUI="$(cd "$(dirname "$0")" && pwd)"
LAVORO="${LAVORO:-/tmp/messina-lettore}"
USCITA="${USCITA:-$QUI/uscita}"
[ "$(id -u)" = 0 ] || { echo "Serve root (sudo)"; exit 1; }
command -v lb >/dev/null || { apt-get update -q && apt-get install -y -q live-build; }

rm -rf "$LAVORO" && mkdir -p "$LAVORO" "$USCITA/live" && cd "$LAVORO"
lb config \
  --mode debian --distribution trixie --architectures amd64 \
  --archive-areas "main contrib non-free-firmware non-free" \
  --binary-images iso-hybrid --bootloaders "grub-efi,syslinux" \
  --debian-installer none --memtest none --apt-recommends false --security true --updates true \
  --iso-volume "MESSINA LETTORE" --iso-application "Messina Lettore" --iso-publisher "Messina Corporation" \
  --bootappend-live "boot=live components toram noswap nopersistence quiet loglevel=3 consoleblank=0 hostname=lettore username=lettore" \
  --linux-flavours amd64

cp -r "$QUI/config/." config/
install -D -m 755 "$QUI/../lettore/leggi.py" config/includes.chroot/usr/local/bin/leggi.py   # -D: git non tiene le cartelle vuote
lb build 2>&1 | tee build.log | grep -E '^(P:|E:|W:)' || true

ISO="$(ls -1 "$LAVORO"/*.iso | head -1)"
[ -f "$ISO" ] || { echo "Costruzione non riuscita: vedi $LAVORO/build.log"; tail -40 build.log; exit 1; }
cp "$ISO" "$USCITA/messina-lettore.iso"
cp binary/live/vmlinuz* "$USCITA/live/vmlinuz"
cp binary/live/initrd.img* "$USCITA/live/initrd.img"
cp binary/live/filesystem.squashfs "$USCITA/live/filesystem.squashfs"
# i programmi di avvio dalla rete, firmati per il Secure Boot (Debian): li usa il server del banco
apt-get install -y -q shim-signed grub-efi-amd64-signed pxelinux syslinux-common >/dev/null
mkdir -p "$USCITA/tftp"
install -m 644 /usr/lib/shim/shimx64.efi.signed "$USCITA/tftp/bootx64.efi"
install -m 644 /usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed "$USCITA/tftp/grubx64.efi"
install -m 644 /usr/lib/PXELINUX/pxelinux.0 /usr/lib/syslinux/modules/bios/ldlinux.c32 "$USCITA/tftp/"
( cd "$USCITA" && sha256sum messina-lettore.iso live/* tftp/* > SHA256SUMS )
echo "Fatto: $USCITA"; ls -la "$USCITA" "$USCITA/live"
