#!/usr/bin/env python3
"""Lettore Messina: legge un PC già formattato e manda i dati al server del banco.

Parte da solo all'avvio del sistema del lettore (rete o chiavetta). Non monta e non scrive mai i dischi del PC:
legge solo quello che il firmware e il kernel espongono (DMI, sysfs, SMART, EDID).

  1. legge seriale, marca, modello, processore, RAM, disco con la salute, batteria (% e cicli), schermo,
     scheda video, Wi-Fi, webcam, password del BIOS, indirizzo di rete e, se lo switch lo dice, la porta
  2. trova il server del banco (indirizzo all'avvio, altrimenti lo chiede sulla rete) e gli manda la lettura
  3. scrive «LETTO» a tutto schermo e spegne il PC; se qualcosa non va lo scrive e riprova, senza spegnersi

Uso:  leggi.py            (sul PC da leggere, come root)
      leggi.py --prova     (stampa la lettura e non manda niente: per provarlo su un PC qualsiasi)
      leggi.py --radice D  (legge da una copia di /sys e /proc in D: per i test)
"""
import glob, json, math, os, re, socket, struct, subprocess, sys, threading, time, urllib.request

VERSIONE = '1.0'
PORTA_SCOPERTA = 47474          # UDP: «c'è un server del banco?»
ATTESA_SPEGNIMENTO = 15         # secondi con «LETTO» a schermo prima di spegnere
RADICE = ''                     # per i test: la cartella con la copia di /sys e /proc

# --------------------------------------------------------------------------- file e comandi

def leggi(percorso, binario=False):
    try:
        with open(RADICE + percorso, 'rb' if binario else 'r', errors=None if binario else 'replace') as f:
            return f.read() if binario else f.read().strip()
    except OSError:
        return None

def comando(*argomenti, tempo=20):
    """Esegue un programma di sola lettura; nei test le uscite stanno in <radice>/comandi/<nome>.txt."""
    if RADICE:
        nome = '_'.join(a.strip('-/').replace('/', '_') for a in argomenti)
        return leggi('/comandi/' + nome + '.txt')
    try:
        r = subprocess.run(argomenti, capture_output=True, text=True, timeout=tempo)
        return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None

def cerca(schema):
    return sorted(p[len(RADICE):] for p in glob.glob(RADICE + schema))

# --------------------------------------------------------------------------- seriale, marca, modello

FINTI = re.compile(r'^(to be filled.*|default string|system serial number|not specified|none|n/?a|0+|1234567890|x+|chassis serial number|invalid)$', re.I)

def pulisci_seriale(s):
    s = re.sub(r'\s+', '', s or '').upper()
    return s if s and not FINTI.match(s) and re.match(r'^[A-Z0-9][A-Z0-9-]{3,39}$', s) else None

def marca_da(vendor):
    v = (vendor or '').lower()
    if 'lenovo' in v: return 'Lenovo'
    if 'hewlett' in v or v.startswith('hp'): return 'HP'
    if 'dell' in v: return 'Dell'
    if 'apple' in v: return 'Apple'
    if 'microsoft' in v: return 'Microsoft'
    if 'fujitsu' in v: return 'Fujitsu'
    if 'acer' in v: return 'Acer'
    if 'asus' in v: return 'ASUS'
    if 'toshiba' in v or 'dynabook' in v: return 'Dynabook'
    v = (vendor or '').strip()
    return v if v and not FINTI.match(v) else None

def identita():
    d = '/sys/class/dmi/id/'
    marca = marca_da(leggi(d + 'sys_vendor'))
    nome, versione, sku = leggi(d + 'product_name') or '', leggi(d + 'product_version') or '', leggi(d + 'product_sku') or ''
    seriale = pulisci_seriale(leggi(d + 'product_serial')) or pulisci_seriale(leggi(d + 'chassis_serial'))
    if marca == 'Lenovo':
        # Lenovo: il nome è il codice (MTM, es. 21AH00BWIX), il modello vero sta nella versione (es. ThinkPad T14 Gen 3)
        modello = versione if versione and not FINTI.match(versione) and not re.match(r'^[0-9A-Z]{4,10}$', versione) else nome
        codice = nome if re.match(r'^[0-9A-Z]{4,10}$', nome) else (sku or None)
    elif marca == 'HP':
        modello = re.sub(r'^(HP|Hewlett-Packard)\s+', '', nome, flags=re.I)
        modello = re.sub(r'\s+(Notebook|Laptop|Mobile Workstation|Desktop|Small Form Factor|Mini)?\s*PC$', '', modello, flags=re.I).strip()
        codice = sku.split('#')[0] if sku and not FINTI.match(sku) else None   # codice prodotto senza la lingua della tastiera
    else:
        modello, codice = nome, (sku if sku and not FINTI.match(sku) else None)
    modello = re.sub(r'\s+', ' ', modello or '').strip() or None
    if modello and FINTI.match(modello): modello = None
    return {'seriale': seriale, 'marca': marca, 'modello': modello, 'codice_modello': codice}

# --------------------------------------------------------------------------- processore e RAM

def processore():
    info = leggi('/proc/cpuinfo') or ''
    m = re.search(r'^model name\s*:\s*(.+)$', info, re.M)
    if not m: return None
    s = m.group(1)
    s = re.sub(r'\((R|TM|tm|r)\)', '', s)
    s = re.sub(r'\b(\d+(st|nd|rd|th) Gen)\s+', '', s)        # «11th Gen Intel Core» → «Intel Core»
    s = re.sub(r'\s+CPU\b', '', s)
    s = re.sub(r'\s*@\s*[\d.]+\s*GHz', '', s)
    s = re.sub(r'\s+with Radeon.*$', '', s, flags=re.I)
    s = re.sub(r'\s+\d+-Core Processor$', '', s, flags=re.I)
    return re.sub(r'\s+', ' ', s).strip() or None

TAGLI_RAM = [2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128]

def ram():
    out = comando('dmidecode', '-t', '17') or ''
    tot, tipo = 0, None
    for blocco in out.split('Memory Device')[1:]:
        m = re.search(r'^\s*Size:\s*(\d+)\s*(MB|GB)', blocco, re.M)
        if m: tot += int(m.group(1)) * (1024 if m.group(2) == 'GB' else 1)
        t = re.search(r'^\s*Type:\s*(DDR\w*|LPDDR\w*)', blocco, re.M)
        if m and t: tipo = t.group(1)
    if tot:
        gb = round(tot / 1024)
    else:   # senza dmidecode: la memoria vista dal sistema, arrotondata alla misura in commercio
        m = re.search(r'^MemTotal:\s*(\d+)\s*kB', leggi('/proc/meminfo') or '', re.M)
        if not m: return None
        vista = int(m.group(1)) / 1024 / 1024
        gb = next((x for x in TAGLI_RAM if x >= vista * 0.97), round(vista))
    return f'{gb} GB' + (f' {tipo}' if tipo else '')

# --------------------------------------------------------------------------- dischi e salute

TAGLI_DISCO = [32, 64, 120, 128, 240, 250, 256, 320, 480, 500, 512, 750, 960, 1000, 1024, 2000, 2048, 4000, 4096]

def misura(byte):
    gb = byte / 1e9
    vicino = min(TAGLI_DISCO, key=lambda x: abs(x - gb))
    gb = vicino if abs(vicino - gb) / vicino < 0.1 else round(gb)
    return f'{gb // 1000} TB' if gb >= 1000 and gb % 1000 in (0, 24, 48, 96) else f'{gb} GB'

def salute(dev):
    """Buono / Da controllare / Guasto e ore di lavoro, da SMART (smartctl, sola lettura)."""
    out = comando('smartctl', '--json', '-a', '/dev/' + dev, tempo=30)
    try: j = json.loads(out or '{}')
    except ValueError: j = {}
    if not j.get('smart_status') and not j.get('nvme_smart_health_information_log') and not j.get('ata_smart_attributes'):
        return None, None, None
    ore = (j.get('power_on_time') or {}).get('hours')
    n = j.get('nvme_smart_health_information_log') or {}
    crudi = {k: n.get(k) for k in ('critical_warning', 'percentage_used', 'available_spare', 'available_spare_threshold', 'media_errors') if k in n}
    crudi.update({f'ata_{a.get("id")}': (a.get('raw') or {}).get('value') for a in (j.get('ata_smart_attributes') or {}).get('table', []) if a.get('id') in (5, 197, 198)})
    crudi['passato'] = (j.get('smart_status') or {}).get('passed')
    stato, ore = _salute(j, ore)
    return stato, ore, crudi

def _salute(j, ore):
    stato = 'Buono'
    if j.get('smart_status', {}).get('passed') is False: return 'Guasto', ore
    n = j.get('nvme_smart_health_information_log') or {}
    if n:
        if n.get('critical_warning'): return 'Guasto', ore
        riserva, soglia = n.get('available_spare'), n.get('available_spare_threshold')
        sotto_soglia = riserva is not None and soglia and riserva < soglia      # soglia 0 o assente: il disco non ne dichiara una
        if (n.get('percentage_used') or 0) >= 90 or (n.get('media_errors') or 0) > 0 or sotto_soglia:
            stato = 'Da controllare'
    for a in (j.get('ata_smart_attributes') or {}).get('table', []):
        crudo = (a.get('raw') or {}).get('value') or 0
        if a.get('id') in (5, 197, 198) and crudo > 0: stato = 'Da controllare'
        if a.get('id') == 5 and crudo > 100: return 'Guasto', ore
        if a.get('when_failed'): return 'Guasto', ore
    return stato, ore

def dischi():
    trovati = []
    for b in cerca('/sys/block/*'):
        nome = b.rsplit('/', 1)[1]
        if re.match(r'^(loop|ram|zram|sr|fd|dm-|md)', nome): continue
        if leggi(b + '/removable') == '1': continue
        reale = os.path.realpath(RADICE + b)
        if '/usb' in reale: continue                          # la chiavetta del lettore non è il disco del PC
        settori = int(leggi(b + '/size') or 0)
        if settori < 2 * 1024 * 1024 * 8: continue            # sotto 8 GB non è un disco (lettori di schede, eMMC di servizio)
        tipo = 'SSD NVMe' if nome.startswith('nvme') else ('HDD' if leggi(b + '/queue/rotational') == '1' else 'SSD')
        if nome.startswith('mmcblk'): tipo = 'eMMC'
        stato, ore, crudi = salute(nome)
        trovati.append({'nome': nome, 'testo': f'{tipo} {misura(settori * 512)}', 'salute': stato, 'ore': ore,
                        'modello': leggi(b + '/device/model'), 'smart': crudi})
    if not trovati: return {'disco': 'Nessun disco', 'disco_salute': None, 'disco_ore': None, 'dischi': []}
    peggio = next((x for x in ('Guasto', 'Da controllare') if any(d['salute'] == x for d in trovati)),
                  'Buono' if all(d['salute'] == 'Buono' for d in trovati) else None)
    return {'disco': ' + '.join(d['testo'] for d in trovati), 'disco_salute': peggio,
            'disco_ore': max((d['ore'] for d in trovati if d['ore'] is not None), default=None), 'dischi': trovati}

# --------------------------------------------------------------------------- batteria

def numero(percorso):
    v = leggi(percorso)
    return int(v) if v and re.match(r'^\d+$', v) else None

def batteria():
    pieno = nuovo = 0; cicli = []
    for b in cerca('/sys/class/power_supply/*'):
        if leggi(b + '/type') != 'Battery' or leggi(b + '/scope') == 'Device': continue   # «Device»: mouse e tastiere senza fili
        f, d = numero(b + '/energy_full'), numero(b + '/energy_full_design')
        if not (f and d): f, d = numero(b + '/charge_full'), numero(b + '/charge_full_design')
        if f and d: pieno += f; nuovo += d
        c = numero(b + '/cycle_count')
        if c: cicli.append(c)                       # 0 = il firmware non lo dice (capita su alcuni HP): resta vuoto
    if not nuovo: return {'batteria_pct': None, 'batteria_cicli': None}
    return {'batteria_pct': min(100, round(pieno / nuovo * 100)), 'batteria_cicli': max(cicli) if cicli else None}

# --------------------------------------------------------------------------- schermo (EDID del pannello interno)

POLLICI = [10.1, 11.6, 12.0, 12.3, 12.5, 13.0, 13.3, 13.4, 13.5, 14.0, 15.0, 15.6, 16.0, 17.0, 17.3, 18.0]

def edid(dati):
    if not dati or len(dati) < 128 or dati[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00': return None
    t = dati[54:72]                                  # primo descrittore: la risoluzione nativa
    if t[0] == 0 and t[1] == 0: return None
    lar = t[2] | ((t[4] & 0xF0) << 4); alt = t[5] | ((t[7] & 0xF0) << 4)
    mm_l = t[12] | ((t[14] & 0xF0) << 4); mm_a = t[13] | ((t[14] & 0x0F) << 8)
    if not mm_l or not mm_a: mm_l, mm_a = dati[21] * 10, dati[22] * 10
    pollici = math.hypot(mm_l, mm_a) / 25.4 if mm_l and mm_a else None
    if pollici:
        vicino = min(POLLICI, key=lambda x: abs(x - pollici))
        pollici = vicino if abs(vicino - pollici) < 0.35 else round(pollici, 1)
    return lar, alt, pollici

def schermo():
    for p in cerca('/sys/class/drm/card*-eDP-*') + cerca('/sys/class/drm/card*-LVDS-*') + cerca('/sys/class/drm/card*-DSI-*'):
        e = edid(leggi(p + '/edid', binario=True))
        if e:
            lar, alt, poll = e
            txt = (f'{poll:g}"' if poll else '') + f' {lar}×{alt}'
            return txt.strip()
    return None                                      # fisso, oppure pannello che non si presenta

# --------------------------------------------------------------------------- scheda video, rete, webcam

def pci(classe):
    """Nomi dei dispositivi PCI di una classe (lspci -mm, sola lettura)."""
    out = comando('lspci', '-mm') or ''
    nomi = []
    for riga in out.splitlines():
        campi = re.findall(r'"([^"]*)"', riga)
        if len(campi) >= 3 and re.search(classe, campi[0], re.I):
            produttore = re.sub(r'\s+(Corporation|Corp\.|Inc\.|Co\., Ltd\.|Semiconductor Co\., Ltd\.)$', '', campi[1]).replace('Advanced Micro Devices, Inc. [AMD/ATI]', 'AMD')
            modello = re.search(r'\[([^\]]+)\]\s*$', campi[2])
            nomi.append(f'{produttore.split()[0]} {modello.group(1) if modello else campi[2]}')
    return nomi

def webcam():
    for p in cerca('/sys/bus/usb/devices/*/bInterfaceClass'):
        if leggi(p) == '0e': return 'Sì'               # classe video USB: la webcam interna
    return 'No' if cerca('/sys/bus/usb/devices/*') else None

def rete():
    wifi = pci(r'network controller')
    return ', '.join(wifi) or None

# --------------------------------------------------------------------------- password del BIOS (Lenovo, Dell, HP recenti)

def password_bios():
    stati = []
    for p in cerca('/sys/class/firmware-attributes/*/authentication/*/is_enabled'):
        tipo = p.split('/')[-2].lower()
        if any(x in tipo for x in ('admin', 'setup', 'supervisor', 'system', 'power-on', 'poweron')):
            stati.append(leggi(p) == '1')
    return any(stati) if stati else None              # None: questo PC non lo dice

# --------------------------------------------------------------------------- accensione dalla rete (solo se il server lo chiede)

PREFERITI_WOL = ['LanWithPxeBoot', 'Boot to Network', 'ACandBattery', 'ACOnly', 'Enable', 'Enabled', 'LanOnly']

def abilita_wol():
    """Attiva nel BIOS l'accensione dalla rete, se c'è l'impostazione e non c'è password. Ritorna com'è andata."""
    if password_bios(): return 'password nel BIOS'
    for p in cerca('/sys/class/firmware-attributes/*/attributes/*'):
        nome = p.rsplit('/', 1)[1].replace(' ', '').lower()
        if nome not in ('wakeonlan', 'wakeonlanbootonnetwork', 'wol'): continue
        possibili = [v.strip() for v in re.split(r'[;,]', leggi(p + '/possible_values') or '') if v.strip()]
        attuale = leggi(p + '/current_value')
        scelta = next((v for v in PREFERITI_WOL if v in possibili), None)
        if not scelta: return 'impostazione senza valori noti'
        if attuale == scelta: return 'già attiva'
        try:
            with open(RADICE + p + '/current_value', 'w') as f: f.write(scelta)
            return 'attivata (' + scelta + ')'
        except OSError as e:
            return 'non riuscita: ' + (e.strerror or 'errore')
    return 'non disponibile su questo PC'

def impostazioni(server):
    try:
        with urllib.request.urlopen(f'http://{server}/impostazioni', timeout=5) as r: return json.loads(r.read() or b'{}')
    except Exception:
        return {}

# --------------------------------------------------------------------------- rete cablata, porta dello switch (LLDP)

def scheda_cablata():
    migliori = []
    for p in cerca('/sys/class/net/*'):
        n = p.rsplit('/', 1)[1]
        if n == 'lo' or os.path.exists(RADICE + p + '/wireless') or not os.path.exists(RADICE + p + '/device'): continue
        mac = (leggi(p + '/address') or '').lower()
        if not re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', mac) or mac == '00:00:00:00:00:00': continue
        migliori.append((leggi(p + '/carrier') == '1', n, mac))
    migliori.sort(reverse=True)
    return (migliori[0][1], migliori[0][2]) if migliori else (None, None)

def lldp(interfaccia, attesa=35):
    """Ascolta lo switch (LLDP) per sapere a quale porta è attaccato il PC. Nessun pacchetto mandato."""
    if not interfaccia or RADICE: return None
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x88CC))
        s.bind((interfaccia, 0)); s.settimeout(attesa)
        fine = time.time() + attesa
        while time.time() < fine:
            pacchetto = s.recv(2048)[14:]
            i, porta, descr = 0, None, None
            while i + 2 <= len(pacchetto):
                tipo, lung = pacchetto[i] >> 1, ((pacchetto[i] & 1) << 8) | pacchetto[i + 1]
                v = pacchetto[i + 2:i + 2 + lung]
                if tipo == 0: break
                if tipo == 2 and lung > 1: porta = v[1:].decode('ascii', 'replace')
                if tipo == 4: descr = v.decode('ascii', 'replace')
                i += 2 + lung
            if porta or descr: return {'porta': porta, 'descrizione': descr}
    except (OSError, socket.timeout):
        return None
    return None

# --------------------------------------------------------------------------- la lettura intera

def lettura(con_lldp=True):
    iface, mac = scheda_cablata()
    porta = {}
    t = None
    if con_lldp:
        t = threading.Thread(target=lambda: porta.update(lldp(iface) or {}), daemon=True); t.start()
    x = identita()
    x.update({'cpu': processore(), 'ram': ram()})
    x.update(dischi())
    x.update(batteria())
    gpu = pci(r'vga|3d|display')
    x.update({'schermo': schermo(), 'gpu': ', '.join(gpu) or None, 'rete': rete(), 'webcam': webcam(),
              'password_bios': password_bios(), 'mac': mac, 'versione': VERSIONE,
              'bios': leggi('/sys/class/dmi/id/bios_version'), 'tipo': tipo_pc()})
    if t: t.join(timeout=40)
    if porta: x['porta_switch'] = porta.get('porta'); x['porta_descrizione'] = porta.get('descrizione')
    return x

def tipo_pc():
    c = numero('/sys/class/dmi/id/chassis_type')
    return 'Portatile' if c in (8, 9, 10, 14, 31, 32) else 'Fisso' if c in (3, 4, 5, 6, 7, 13, 15, 16, 35) else None

# --------------------------------------------------------------------------- il server del banco

def server_da_avvio():
    m = re.search(r'messina\.server=(\S+)', leggi('/proc/cmdline') or '')
    return m.group(1) if m else None

def scopri_server(attesa=3, giri=10):
    """Chiede sulla rete del banco: «c'è un server Messina?» (UDP broadcast). Risponde solo il nostro server."""
    for _ in range(giri):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(attesa); s.sendto(b'MESSINA-BANCO?', ('255.255.255.255', PORTA_SCOPERTA))
            dati, (ip, _) = s.recvfrom(512)
            m = re.match(rb'^MESSINA-BANCO (\d+)', dati)
            if m: return f'{ip}:{m.group(1).decode()}'
        except OSError:
            time.sleep(1)
    return None

def manda(server, x, tempo=20):
    corpo = json.dumps(x).encode()
    r = urllib.request.Request(f'http://{server}/lettura', data=corpo, method='POST', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=tempo) as risposta:
        return json.loads(risposta.read() or b'{}')

# --------------------------------------------------------------------------- lo schermo del PC

class Schermo:
    """Testo grande sulla console del PC (tty1). In --prova scrive solo sul terminale."""
    def __init__(self, vero):
        self.tty = None
        if vero:
            try: self.tty = open('/dev/tty1', 'w')
            except OSError: self.tty = None
    def mostra(self, titolo, righe=(), colore='bianco'):
        sfondo = {'bianco': '\033[47;30m', 'verde': '\033[42;30m', 'rosso': '\033[41;97m', 'giallo': '\033[43;30m'}[colore]
        testo = sfondo + '\033[2J\033[H\n\n   MESSINA · Lettore ' + VERSIONE + '\n\n\n   \033[1m' + titolo + '\033[22m\n\n' + \
                ''.join('   ' + r + '\n' for r in righe) + '\033[0m'
        if self.tty:
            try: self.tty.write(testo); self.tty.flush(); return
            except OSError: pass
        print(titolo, *righe, sep='\n   ', file=sys.stderr)

def riassunto(x):
    return [f"Seriale   {x.get('seriale') or 'NON LETTO'}",
            f"Modello   {' '.join(v for v in (x.get('marca'), x.get('modello')) if v) or '—'}",
            f"CPU       {x.get('cpu') or '—'}",
            f"RAM       {x.get('ram') or '—'}",
            f"Disco     {x.get('disco') or '—'}" + (f"  ({x['disco_salute']})" if x.get('disco_salute') else ''),
            f"Batteria  {str(x['batteria_pct']) + '%' if x.get('batteria_pct') is not None else '—'}" + (f"  · {x['batteria_cicli']} cicli" if x.get('batteria_cicli') else ''),
            f"Schermo   {x.get('schermo') or '—'}",
            f"BIOS      {'password presente' if x.get('password_bios') else 'senza password' if x.get('password_bios') is False else '—'}"]

def spegni():
    subprocess.run(['systemctl', 'poweroff'], check=False)

def principale():
    global RADICE
    argv = sys.argv[1:]
    prova = '--prova' in argv
    if '--radice' in argv: RADICE = argv[argv.index('--radice') + 1].rstrip('/')
    sc = Schermo(vero=not prova and not RADICE)
    sc.mostra('Leggo il PC…', ['Non toccare niente: ci vuole un minuto o due.'])
    x = lettura(con_lldp=not prova and not RADICE)
    if prova or RADICE:
        print(json.dumps(x, ensure_ascii=False, indent=2)); return 0
    if not x.get('seriale'):
        sc.mostra('SERIALE NON LEGGIBILE', ['Questo PC non dice il suo seriale.', 'Spegnilo e caricalo dall’app con la foto dell’etichetta.', ''] + riassunto(x), 'giallo')
        return 2
    server = server_da_avvio()
    tentativo = 0
    while True:
        tentativo += 1
        server = server or scopri_server()
        if not server:
            sc.mostra('CERCO IL SERVER DEL BANCO…', [f'Tentativo {tentativo}. Controlla che il cavo di rete sia attaccato.', ''] + riassunto(x), 'giallo')
            time.sleep(5); continue
        if 'wol' not in x and impostazioni(server).get('accendi_dalla_rete'):
            x['wol'] = abilita_wol()                    # dalla prossima volta il server lo può accendere dalla rete
        try:
            r = manda(server, x)
        except Exception as e:                        # rete giù, server spento: si riprova, il PC non si spegne
            sc.mostra('NON RIESCO A MANDARE LA LETTURA', [f'Riprovo da solo (tentativo {tentativo}). {type(e).__name__}', ''] + riassunto(x), 'giallo')
            server = server_da_avvio(); time.sleep(10); continue
        if not r.get('ok'):
            sc.mostra('LETTURA RIFIUTATA', [r.get('errore') or 'Il server non l’ha accettata.', 'Spegni il PC e avvisa l’ufficio.', ''] + riassunto(x), 'rosso')
            return 3
        conto = f"Lotto {r['lotto']} · {r['letti']} letti" + (f" di {r['pezzi']}" if r.get('pezzi') else '') if r.get('lotto') else \
                'Ricevuto dal server: lo manda al gestionale appena c’è internet.' if r.get('in_coda') else 'Senza lotto: lo sceglie l’ufficio.'
        for s in range(ATTESA_SPEGNIMENTO, 0, -1):
            sc.mostra('LETTO  ✓', [conto, ''] + riassunto(x) + ['', f'Si spegne da solo tra {s} secondi.'], 'verde')
            time.sleep(1)
        spegni(); return 0

if __name__ == '__main__':
    sys.exit(principale())
