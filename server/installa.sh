#!/usr/bin/env bash
# Installa il server del banco su un PC con Debian 13 collegato allo switch del banco. Si può rilanciare quando si vuole.
#
#   sudo bash banco/server/installa.sh --scheda enp2s0 [--immagine messina-lettore.zip | --costruisci]
#
#   --scheda      la presa di rete collegata allo switch del banco (ip -br link per vederle)
#   --immagine    lo zip «messina-lettore» scaricato da GitHub (Actions → Lettore), oppure
#   --costruisci  costruisce qui il sistema del lettore (10-15 minuti, serve internet)
#
# Dopo: scrivere /etc/messina-banco/banco.json (URL di Supabase, chiave pubblica, codice del banco) e
#       sudo systemctl restart messina-banco ; poi «banco.py controlla».
set -euo pipefail
QUI="$(cd "$(dirname "$0")" && pwd)"
SCHEDA=""; ZIP=""; COSTRUISCI=no
while [ $# -gt 0 ]; do case "$1" in
  --scheda) SCHEDA="$2"; shift 2;; --immagine) ZIP="$2"; shift 2;; --costruisci) COSTRUISCI=si; shift;;
  *) echo "Opzione sconosciuta: $1"; exit 1;; esac; done
[ "$(id -u)" = 0 ] || { echo "Serve sudo"; exit 1; }
[ -n "$SCHEDA" ] || { echo "Manca --scheda (la presa collegata allo switch del banco):"; ip -br link; exit 1; }
IP="$(ip -4 -o addr show dev "$SCHEDA" | awk '{print $4}' | head -1)"
[ -n "$IP" ] || { echo "La presa $SCHEDA non ha un indirizzo: collegala allo switch del banco"; exit 1; }
RETE="$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$IP")"; IP="${IP%/*}"

echo "· pacchetti"
apt-get update -q
apt-get install -y -q python3 dnsmasq shim-signed grub-efi-amd64-signed pxelinux syslinux-common unzip

echo "· utente e cartelle"
id messina-banco >/dev/null 2>&1 || useradd --system --home /var/lib/messina-banco --shell /usr/sbin/nologin messina-banco
install -d -o messina-banco -g messina-banco -m 750 /var/lib/messina-banco
install -d -m 755 /opt/messina-banco /srv/messina-banco/live /srv/messina-banco/tftp/live
install -d -o root -g messina-banco -m 750 /etc/messina-banco
install -m 755 "$QUI/banco.py" "$QUI/prepara_rete.py" /opt/messina-banco/
[ -f /etc/messina-banco/banco.json ] || install -o root -g messina-banco -m 640 "$QUI/banco.esempio.json" /etc/messina-banco/banco.json

echo "· sistema del lettore"
if [ "$COSTRUISCI" = si ]; then USCITA=/srv/messina-banco/uscita bash "$QUI/../immagine/costruisci.sh"; SORG=/srv/messina-banco/uscita
elif [ -n "$ZIP" ]; then rm -rf /tmp/messina-lettore-zip && unzip -q -o "$ZIP" -d /tmp/messina-lettore-zip && SORG=/tmp/messina-lettore-zip
elif [ -f "$QUI/../immagine/uscita/live/vmlinuz" ]; then SORG="$QUI/../immagine/uscita"
else SORG=""; fi
if [ -n "$SORG" ]; then
  ( cd "$SORG" && sha256sum -c --quiet SHA256SUMS ) || { echo "I file del lettore sono rovinati: riscaricali"; exit 1; }
  install -m 644 "$SORG"/live/vmlinuz "$SORG"/live/initrd.img "$SORG"/live/filesystem.squashfs /srv/messina-banco/live/
  install -m 644 "$SORG"/live/vmlinuz "$SORG"/live/initrd.img /srv/messina-banco/tftp/live/
  [ -f "$SORG/messina-lettore.iso" ] && install -m 644 "$SORG/messina-lettore.iso" /srv/messina-banco/
else
  echo "  ATTENZIONE: manca il sistema del lettore. Rilancia con --immagine <zip> oppure --costruisci."
fi

echo "· avvio dalla rete (UEFI con Secure Boot e BIOS)"
install -m 644 /usr/lib/shim/shimx64.efi.signed /srv/messina-banco/tftp/bootx64.efi          # shim firmato Microsoft
install -m 644 /usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed /srv/messina-banco/tftp/grubx64.efi   # GRUB firmato Debian
install -m 644 /usr/lib/PXELINUX/pxelinux.0 /usr/lib/syslinux/modules/bios/ldlinux.c32 /srv/messina-banco/tftp/
python3 /opt/messina-banco/prepara_rete.py --scheda "$SCHEDA" --ip "$IP" --rete "$RETE"

echo "· servizio"
cat > /etc/systemd/system/messina-banco.service <<UNIT
[Unit]
Description=Messina · server del banco
After=network-online.target
Wants=network-online.target

[Service]
User=messina-banco
ExecStart=/usr/bin/python3 /opt/messina-banco/banco.py avvia
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/messina-banco
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now dnsmasq messina-banco
systemctl restart dnsmasq
systemctl restart messina-banco || true

cat <<FINE

Fatto. Server del banco su $IP (rete $RETE, presa $SCHEDA).
  1. Scrivi URL di Supabase, chiave pubblica e codice del banco in /etc/messina-banco/banco.json
     (sudo nano /etc/messina-banco/banco.json) e poi: sudo systemctl restart messina-banco
  2. Prova il collegamento:  sudo -u messina-banco /opt/messina-banco/banco.py controlla
  3. Stato in diretta:        http://$IP:8088/  (dal browser del server o da un PC degli uffici collegato)
  4. Chiavetta di riserva:     /srv/messina-banco/messina-lettore.iso  (si scrive con balenaEtcher)
Se c'è un firewall: aperti UDP 67, 69, 4011 e 47474, TCP 8088 sulla presa $SCHEDA.
FINE
