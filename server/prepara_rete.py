#!/usr/bin/env python3
"""Scrive i file per l'avvio dalla rete del banco: dnsmasq in modo «proxy» e i menu di avvio (UEFI e BIOS).

In modo «proxy» dnsmasq NON dà indirizzi (li dà sempre il router del banco) e risponde solo ai PC che chiedono
di partire dalla rete: gli altri dispositivi non se ne accorgono.

Uso: prepara_rete.py --scheda enp2s0 --ip 192.168.50.2 --rete 192.168.50.0 [--porta 8088] [--tftp /srv/messina-banco/tftp] [--dnsmasq /etc/dnsmasq.d/messina-banco.conf]
"""
import argparse, ipaddress, os

def opzioni(ip, porta):
    return (f'boot=live components fetch=http://{ip}:{porta}/live/filesystem.squashfs messina.server={ip}:{porta} '
            'noswap nopersistence quiet loglevel=3 consoleblank=0 hostname=lettore username=lettore')

def scrivi(p, testo):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f: f.write(testo)

def prepara(scheda, ip, rete, porta, tftp, dnsmasq):
    ipaddress.ip_address(ip); ipaddress.ip_network(rete + '/24', strict=False) if '/' not in rete else ipaddress.ip_network(rete, strict=False)
    rete = rete.split('/')[0]
    scrivi(dnsmasq, f'''# Messina · avvio dalla rete del banco (scritto da prepara_rete.py: si rigenera con installa.sh)
# Modo «proxy»: gli indirizzi li dà il router; qui si risponde solo ai PC che chiedono di partire dalla rete.
port=0
interface={scheda}
bind-interfaces
dhcp-range={rete},proxy
dhcp-match=set:efi64,option:client-arch,7
dhcp-match=set:efi64,option:client-arch,9
pxe-prompt="Messina Lettore",1
pxe-service=tag:efi64,x86-64_EFI,"Messina Lettore",bootx64.efi
pxe-service=tag:!efi64,x86PC,"Messina Lettore",pxelinux
enable-tftp
tftp-root={tftp}
log-dhcp
''')
    menu = f'''set timeout=1
set default=0
menuentry "Messina · Lettore" {{
  linux /live/vmlinuz {opzioni(ip, porta)}
  initrd /live/initrd.img
}}
'''
    # il GRUB firmato di Debian cerca il menu in posti diversi a seconda della versione: lo mettiamo in tutti
    for p in ('grub/grub.cfg', 'debian-installer/amd64/grub/grub.cfg', 'grub.cfg'):
        scrivi(os.path.join(tftp, p), menu)
    scrivi(os.path.join(tftp, 'pxelinux.cfg/default'), f'''DEFAULT lettore
TIMEOUT 10
PROMPT 0
LABEL lettore
  KERNEL live/vmlinuz
  APPEND initrd=live/initrd.img {opzioni(ip, porta)}
''')

if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument('--scheda', required=True); a.add_argument('--ip', required=True); a.add_argument('--rete', required=True)
    a.add_argument('--porta', type=int, default=8088); a.add_argument('--tftp', default='/srv/messina-banco/tftp')
    a.add_argument('--dnsmasq', default='/etc/dnsmasq.d/messina-banco.conf')
    x = a.parse_args()
    prepara(x.scheda, x.ip, x.rete, x.porta, x.tftp, x.dnsmasq)
    print('Avvio dalla rete preparato:', x.dnsmasq, x.tftp)
