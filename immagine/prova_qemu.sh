#!/usr/bin/env bash
# Prova vera del lettore costruito: una macchina virtuale che si presenta come un ThinkPad T14 Gen 3 con disco NVMe
# parte DALLA RETE (kernel e sistema dal server del banco), legge, manda la lettura al server e si spegne da sola.
# Il server del banco è quello vero (banco.py); «Supabase» è finto e scrive solo cosa gli arriva.
# Serve: qemu-system-x86_64, qemu-img, python3, banco/immagine/uscita (da costruisci.sh). Circa 3-10 minuti.
set -euo pipefail
QUI="$(cd "$(dirname "$0")" && pwd)"; BANCO="$QUI/.."
T="$(mktemp -d)"; trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
[ -f "$QUI/uscita/live/vmlinuz" ] || { echo "Prima costruisci l'immagine (costruisci.sh)"; exit 1; }

# «Supabase» finto: registra_lettura risponde ok e salva il corpo
cat > "$T/finto.py" <<'EOF'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class G(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        open(sys.argv[1], 'a').write(json.dumps(b) + '\n')
        r = json.dumps({'ok': True, 'lotto': 'L-QEMU', 'letti': 1, 'pezzi': 1}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(r))); self.end_headers(); self.wfile.write(r)
HTTPServer(('127.0.0.1', 54321), G).serve_forever()
EOF
python3 "$T/finto.py" "$T/arrivate.jsonl" &
cat > "$T/banco.json" <<EOF
{"nome": "Banco QEMU", "supabase_url": "http://127.0.0.1:54321", "chiave_pubblica": "sb_publishable_prova",
 "codice_banco": "prova", "porta": 8088, "cartella": "$T/dati", "immagine": "$QUI/uscita/live"}
EOF
MESSINA_BANCO="$T/banco.json" python3 "$BANCO/server/banco.py" avvia &
sleep 2

KVM=""; [ -w /dev/kvm ] && KVM="-enable-kvm -cpu host"

controlla() {   # $1 = file arrivate, $2 = disco, $3 = atteso (json)
python3 - "$1" "$2" "$3" <<'PY'
import json, sys
x = json.loads(open(sys.argv[1]).readlines()[-1])['p_dati']
print(json.dumps({k: x.get(k) for k in ('seriale', 'marca', 'modello', 'codice_modello', 'cpu', 'ram', 'disco', 'disco_salute', 'mac', 'versione')}, ensure_ascii=False, indent=1))
atteso = json.loads(sys.argv[3])
sbagliati = {k: (x.get(k), v) for k, v in atteso.items() if x.get(k) != v}
if sbagliati: print('SBAGLIATI:', sbagliati); sys.exit(1)
with open(sys.argv[2], 'rb') as f:              # il disco del PC non è stato toccato: è ancora tutto a zero
    for _ in range(64):
        if f.read(1 << 20).strip(b'\0'): print('IL DISCO È STATO SCRITTO'); sys.exit(1)
PY
}

# A · kernel dato direttamente (prova il sistema del lettore e il server)
qemu-img create -q -f raw "$T/disco.img" 512000000000          # 512 GB, vuoto (come un PC già formattato)
echo "A · accendo un ThinkPad finto: sistema dal server del banco…"
timeout 900 qemu-system-x86_64 $KVM -m 2048 -smp 2 -no-reboot -nographic \
  -smbios type=1,manufacturer=LENOVO,product=21AH00BWIX,version="ThinkPad T14 Gen 3",serial=PF4QEMU01,sku=LENOVO_MT_21AH \
  -kernel "$QUI/uscita/live/vmlinuz" -initrd "$QUI/uscita/live/initrd.img" \
  -append "boot=live components fetch=http://10.0.2.2:8088/live/filesystem.squashfs messina.server=10.0.2.2:8088 noswap nopersistence console=ttyS0 loglevel=4 hostname=lettore" \
  -drive file="$T/disco.img",if=none,id=d0,format=raw -device nvme,drive=d0,serial=NVME0001 \
  -nic user,model=e1000e,mac=8c:8c:aa:51:52:53 > "$T/console-a.log" 2>&1 || { echo "A: la macchina non si è spenta da sola"; tail -60 "$T/console-a.log"; exit 1; }
[ -s "$T/arrivate.jsonl" ] || { echo "A: non è arrivato niente"; tail -80 "$T/console-a.log"; exit 1; }
controlla "$T/arrivate.jsonl" "$T/disco.img" '{"seriale": "PF4QEMU01", "marca": "Lenovo", "modello": "ThinkPad T14 Gen 3", "codice_modello": "21AH00BWIX", "ram": "2 GB", "disco": "SSD NVMe 512 GB", "mac": "8c:8c:aa:51:52:53"}' \
  || { tail -40 "$T/console-a.log"; exit 1; }
echo "A: ok, lettura giusta, disco non toccato, spento da solo"

# B · avvio dalla rete vero, UEFI: firmware → shim → GRUB firmato → menu del banco → kernel per TFTP → sistema per HTTP
OVMF=/usr/share/OVMF/OVMF_CODE_4M.fd; VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
if [ -f "$OVMF" ] && [ -f "$QUI/uscita/tftp/bootx64.efi" ]; then
  mkdir -p "$T/tftp/live" && cp "$QUI/uscita/tftp/"* "$T/tftp/" && cp "$QUI/uscita/live/vmlinuz" "$QUI/uscita/live/initrd.img" "$T/tftp/live/"
  python3 "$BANCO/server/prepara_rete.py" --scheda eth0 --ip 10.0.2.2 --rete 10.0.2.0/24 --tftp "$T/tftp" --dnsmasq "$T/dnsmasq.conf" >/dev/null
  cp "$VARS" "$T/vars.fd"; qemu-img create -q -f raw "$T/disco2.img" 256060514304
  echo "B · accendo un HP finto col disco vuoto: parte da solo dalla rete…"
  timeout 1200 qemu-system-x86_64 $KVM -m 2048 -smp 2 -no-reboot -nographic \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF" -drive if=pflash,format=raw,file="$T/vars.fd" \
    -smbios type=1,manufacturer=HP,product="HP EliteBook 840 G8 Notebook PC",serial=5CGQEMU02,sku=3C6P1EA#ABZ \
    -drive file="$T/disco2.img",if=none,id=d1,format=raw -device nvme,drive=d1,serial=NVME0002 \
    -netdev user,id=n0,tftp="$T/tftp",bootfile=bootx64.efi -device virtio-net-pci,netdev=n0,mac=aa:bb:cc:51:52:54 \
    > "$T/console-b.log" 2>&1 || { echo "B: la macchina non si è spenta da sola"; tail -60 "$T/console-b.log"; exit 1; }
  [ "$(wc -l < "$T/arrivate.jsonl")" -ge 2 ] || { echo "B: non è arrivato niente"; tail -80 "$T/console-b.log"; exit 1; }
  controlla "$T/arrivate.jsonl" "$T/disco2.img" '{"seriale": "5CGQEMU02", "marca": "HP", "modello": "EliteBook 840 G8", "codice_modello": "3C6P1EA", "disco": "SSD NVMe 256 GB", "mac": "aa:bb:cc:51:52:54"}' \
    || { tail -40 "$T/console-b.log"; exit 1; }
  echo "B: ok, partito da solo dalla rete (UEFI), letto, spento"
else
  echo "B: saltata (manca OVMF o i file firmati di avvio)"
fi
echo "QEMU: tutto ok"
