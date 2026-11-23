#!/usr/bin/env python3
"""Prova del lettore con tre PC finti (Lenovo, HP, Dell) costruiti come li vede Linux: /sys, /proc e le uscite
di dmidecode, smartctl e lspci. Uso: python3 banco/lettore/prova_lettore.py"""
import json, os, subprocess, sys, tempfile

QUI = os.path.dirname(os.path.abspath(__file__))

def scrivi(radice, percorso, contenuto):
    p = os.path.join(radice, percorso.lstrip('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb' if isinstance(contenuto, bytes) else 'w') as f: f.write(contenuto)

def edid(larghezza, altezza, mm_l, mm_a):
    e = bytearray(128)
    e[0:8] = b'\x00\xff\xff\xff\xff\xff\xff\x00'
    e[21], e[22] = mm_l // 10, mm_a // 10
    t = bytearray(18)
    t[0], t[1] = 0x1D, 0x36                           # frequenza di pixel qualsiasi, diversa da zero
    t[2] = larghezza & 0xFF; t[4] = (larghezza >> 8) << 4
    t[5] = altezza & 0xFF;  t[7] = (altezza >> 8) << 4
    t[12] = mm_l & 0xFF; t[13] = mm_a & 0xFF; t[14] = ((mm_l >> 8) << 4) | (mm_a >> 8)
    e[54:72] = t
    return bytes(e)

DMI_RAM = '''Handle 0x0040, DMI type 17, 92 bytes
Memory Device
	Size: {a}
	Type: {t}
Handle 0x0041, DMI type 17, 92 bytes
Memory Device
	Size: {b}
	Type: {t}
'''

def nvme_sano(ore):
    return json.dumps({'smart_status': {'passed': True}, 'power_on_time': {'hours': ore},
                       'nvme_smart_health_information_log': {'critical_warning': 0, 'percentage_used': 3, 'media_errors': 0,
                                                             'available_spare': 100, 'available_spare_threshold': 10}})

def pc(radice, m):
    d = '/sys/class/dmi/id/'
    for k in ('sys_vendor', 'product_name', 'product_version', 'product_sku', 'product_serial', 'chassis_type', 'bios_version'):
        if k in m: scrivi(radice, d + k, m[k] + '\n')
    scrivi(radice, '/proc/cpuinfo', 'processor\t: 0\nmodel name\t: ' + m['cpu'] + '\n')
    scrivi(radice, '/proc/cmdline', 'boot=live fetch=http://10.0.0.2:8088/live/filesystem.squashfs\n')
    scrivi(radice, '/comandi/dmidecode_t_17.txt', DMI_RAM.format(a=m['ram'][0], b=m['ram'][1], t=m['ram'][2]))
    for nome, (settori, rot, smart) in m['dischi'].items():
        scrivi(radice, f'/sys/block/{nome}/size', str(settori)); scrivi(radice, f'/sys/block/{nome}/removable', '0')
        scrivi(radice, f'/sys/block/{nome}/queue/rotational', rot); scrivi(radice, f'/comandi/smartctl_json_a_dev_{nome}.txt', smart)
    # la chiavetta del lettore (non va contata come disco)
    scrivi(radice, '/sys/block/sdz/size', str(31260672)); scrivi(radice, '/sys/block/sdz/removable', '1')
    for i, b in enumerate(m['batterie']):
        p = f'/sys/class/power_supply/BAT{i}/'
        scrivi(radice, p + 'type', 'Battery')
        for k, v in b.items(): scrivi(radice, p + k, str(v))
    scrivi(radice, '/sys/class/power_supply/AC/type', 'Mains')
    scrivi(radice, '/sys/class/power_supply/hidpp_battery_0/type', 'Battery')        # il mouse senza fili: non conta
    scrivi(radice, '/sys/class/power_supply/hidpp_battery_0/scope', 'Device')
    scrivi(radice, '/sys/class/power_supply/hidpp_battery_0/energy_full', '10'); scrivi(radice, '/sys/class/power_supply/hidpp_battery_0/energy_full_design', '100')
    scrivi(radice, '/sys/class/drm/card1-eDP-1/edid', edid(*m['schermo']))
    scrivi(radice, '/sys/class/drm/card1-HDMI-A-1/edid', b'')
    scrivi(radice, '/comandi/lspci_mm.txt', m['lspci'])
    scrivi(radice, '/sys/bus/usb/devices/1-5:1.0/bInterfaceClass', '0e\n')
    for nome, mac, cavo in m['reti']:
        scrivi(radice, f'/sys/class/net/{nome}/address', mac + '\n'); scrivi(radice, f'/sys/class/net/{nome}/carrier', cavo)
        scrivi(radice, f'/sys/class/net/{nome}/device/vendor', '0x8086')
        if nome.startswith('wl'): scrivi(radice, f'/sys/class/net/{nome}/wireless/x', '')
    for driver, tipo, stato in m.get('bios', []):
        scrivi(radice, f'/sys/class/firmware-attributes/{driver}/authentication/{tipo}/is_enabled', stato)
    for driver, nome, possibili, attuale in m.get('wol', []):
        p = f'/sys/class/firmware-attributes/{driver}/attributes/{nome}/'
        scrivi(radice, p + 'possible_values', possibili); scrivi(radice, p + 'current_value', attuale)

LENOVO = {
    'sys_vendor': 'LENOVO', 'product_name': '21AH00BWIX', 'product_version': 'ThinkPad T14 Gen 3', 'product_sku': 'LENOVO_MT_21AH_BU_Think_FM_ThinkPad T14 Gen 3',
    'product_serial': 'PF4ABC12', 'chassis_type': '10', 'bios_version': 'R1YET65W (1.40 )',
    'cpu': '12th Gen Intel(R) Core(TM) i5-1245U', 'ram': ('8 GB', '8 GB', 'DDR4'),
    'dischi': {'nvme0n1': (1000215216, '0', nvme_sano(3120))},
    'batterie': [{'energy_full': 45120000, 'energy_full_design': 52500000, 'cycle_count': 212}],
    'schermo': (1920, 1200, 301, 188),
    'lspci': '00:02.0 "VGA compatible controller" "Intel Corporation" "Alder Lake-UP3 GT2 [Iris Xe Graphics]" -r0c "Lenovo" "Device 22e4"\n'
             '00:14.3 "Network controller" "Intel Corporation" "Alder Lake-P PCH CNVi WiFi [Wi-Fi 6E AX211]" -r01 "Intel Corporation" "Device 0094"\n',
    'reti': [('enp0s31f6', '8C:8C:AA:01:02:03', '1'), ('wlp0s20f3', 'a0:b1:c2:d3:e4:f5', '0')],
    'bios': [('thinklmi', 'Admin', '0'), ('thinklmi', 'System', '0'), ('thinklmi', 'HDD', '0')],
    'wol': [('thinklmi', 'WakeOnLAN', 'Disable;ACOnly;ACandBattery;Enable', 'Disable')],
}
HP = {
    'sys_vendor': 'HP', 'product_name': 'HP EliteBook 840 G8 Notebook PC', 'product_sku': '3C6P1EA#ABZ', 'product_serial': '5CG1234XYZ',
    'chassis_type': '10', 'cpu': '11th Gen Intel(R) Core(TM) i5-1145G7 @ 2.60GHz', 'ram': ('16 GB', 'No Module Installed', 'DDR4'),
    'dischi': {'nvme0n1': (500118192, '0', json.dumps({'smart_status': {'passed': True}, 'power_on_time': {'hours': 15020},
               'nvme_smart_health_information_log': {'critical_warning': 0, 'percentage_used': 93, 'media_errors': 0}}))},
    'batterie': [{'energy_full': 30210000, 'energy_full_design': 53000000, 'cycle_count': 0}],     # HP: cicli a zero = non detti
    'schermo': (1920, 1080, 309, 174),
    'lspci': '00:02.0 "VGA compatible controller" "Intel Corporation" "TigerLake-LP GT2 [Iris Xe Graphics]" -r01 "Hewlett-Packard Company" "Device 880d"\n',
    'reti': [('eno1', 'aa:bb:cc:00:11:22', '1')],
    'bios': [('hp-bioscfg', 'Setup Password', '1')],
}
DELL = {
    'sys_vendor': 'Dell Inc.', 'product_name': 'Latitude 5420', 'product_sku': '0A23', 'product_serial': '7XQ1ZB3', 'chassis_type': '10',
    'cpu': 'Intel(R) Core(TM) i7-1185G7 @ 3.00GHz', 'ram': ('16384 MB', '16384 MB', 'DDR4'),
    'dischi': {'sda': (488397168, '0', json.dumps({'smart_status': {'passed': True}, 'power_on_time': {'hours': 9100},
               'ata_smart_attributes': {'table': [{'id': 5, 'raw': {'value': 8}}, {'id': 197, 'raw': {'value': 0}}]}})),
               'sdb': (1953525168, '1', json.dumps({'smart_status': {'passed': False}, 'power_on_time': {'hours': 30500}}))},
    'batterie': [{'charge_full': 3021000, 'charge_full_design': 3500000, 'cycle_count': 340}],
    'schermo': (1366, 768, 309, 174),
    'lspci': '00:02.0 "VGA compatible controller" "Intel Corporation" "TigerLake-LP GT2 [Iris Xe Graphics]" -r01 "Dell" "Device 0a23"\n'
             '01:00.0 "3D controller" "NVIDIA Corporation" "TU117M [GeForce MX450]" -ra1 "Dell" "Device 0a23"\n',
    'reti': [('enp0s31f6', 'de:11:22:33:44:55', '1')],
}
VUOTO = {'sys_vendor': 'To be filled by O.E.M.', 'product_name': 'To be filled by O.E.M.', 'product_serial': 'To be filled by O.E.M.',
         'cpu': 'AMD Ryzen 5 PRO 5650U with Radeon Graphics', 'ram': ('8 GB', '8 GB', 'DDR4'), 'dischi': {}, 'batterie': [],
         'schermo': (1920, 1080, 344, 194), 'lspci': '', 'reti': []}

ATTESI = {
    'lenovo': {'seriale': 'PF4ABC12', 'marca': 'Lenovo', 'modello': 'ThinkPad T14 Gen 3', 'codice_modello': '21AH00BWIX', 'cpu': 'Intel Core i5-1245U',
               'ram': '16 GB DDR4', 'disco': 'SSD NVMe 512 GB', 'disco_salute': 'Buono', 'disco_ore': 3120, 'batteria_pct': 86, 'batteria_cicli': 212,
               'schermo': '14" 1920×1200', 'gpu': 'Intel Iris Xe Graphics', 'rete': 'Intel Wi-Fi 6E AX211', 'webcam': 'Sì', 'password_bios': False,
               'mac': '8c:8c:aa:01:02:03', 'tipo': 'Portatile'},
    'hp': {'seriale': '5CG1234XYZ', 'marca': 'HP', 'modello': 'EliteBook 840 G8', 'codice_modello': '3C6P1EA', 'cpu': 'Intel Core i5-1145G7',
           'ram': '16 GB DDR4', 'disco': 'SSD NVMe 256 GB', 'disco_salute': 'Da controllare', 'batteria_pct': 57, 'batteria_cicli': None,
           'schermo': '14" 1920×1080', 'password_bios': True, 'mac': 'aa:bb:cc:00:11:22'},
    'dell': {'seriale': '7XQ1ZB3', 'marca': 'Dell', 'modello': 'Latitude 5420', 'codice_modello': '0A23', 'cpu': 'Intel Core i7-1185G7', 'ram': '32 GB DDR4',
             'disco': 'SSD 250 GB + HDD 1 TB', 'disco_salute': 'Guasto', 'disco_ore': 30500, 'batteria_pct': 86, 'batteria_cicli': 340,
             'schermo': '14" 1366×768', 'gpu': 'Intel Iris Xe Graphics, NVIDIA GeForce MX450', 'password_bios': None},
    'vuoto': {'seriale': None, 'marca': None, 'modello': None, 'cpu': 'AMD Ryzen 5 PRO 5650U', 'disco': 'Nessun disco', 'batteria_pct': None,
              'schermo': '15.6" 1920×1080', 'mac': None},
}

def main():
    errori = 0
    for nome, m in (('lenovo', LENOVO), ('hp', HP), ('dell', DELL), ('vuoto', VUOTO)):
        with tempfile.TemporaryDirectory() as radice:
            pc(radice, m)
            r = subprocess.run([sys.executable, os.path.join(QUI, 'leggi.py'), '--radice', radice], capture_output=True, text=True)
            if r.returncode != 0: print(nome, 'ERRORE', r.stderr); errori += 1; continue
            x = json.loads(r.stdout)
            for k, v in ATTESI[nome].items():
                if x.get(k) != v: print(f'{nome}: {k} = {x.get(k)!r}, atteso {v!r}'); errori += 1
            if nome == 'lenovo':   # accensione dalla rete: si attiva solo senza password, con un valore che il BIOS conosce
                sys.path.insert(0, QUI); import leggi as L
                L.RADICE = radice
                esito = L.abilita_wol(); scritto = L.leggi('/sys/class/firmware-attributes/thinklmi/attributes/WakeOnLAN/current_value')
                if not (esito.startswith('attivata') and scritto == 'ACandBattery'): print('lenovo: wol', esito, scritto); errori += 1
                L.RADICE = ''
            print(f'{nome}: {"ok" if not errori else "…"}  ' + ', '.join(f'{k}={x.get(k)}' for k in ('seriale', 'modello', 'disco', 'disco_salute', 'batteria_pct', 'schermo')))
    print('LETTORE: tutto ok' if not errori else f'LETTORE: {errori} errori'); return 1 if errori else 0

if __name__ == '__main__':
    sys.exit(main())
