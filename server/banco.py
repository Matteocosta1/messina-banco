#!/usr/bin/env python3
"""Server del banco Messina: riceve le letture dei PC, le tiene al sicuro e le manda al gestionale.

Gira su un PC qualsiasi collegato allo switch del banco (Debian 12). Solo Python, niente librerie da installare.

  · ascolta i PC del banco: «c'è un server?» (UDP 47474) e POST /lettura (HTTP, porta 8088)
  · ogni lettura si salva prima sul disco del server (SQLite) e solo dopo si risponde «ricevuto»:
    se internet cade (Starlink) non si perde niente, si manda dopo da sola
  · la manda a Supabase con registra_lettura(): entra con il codice del banco, mai con una persona
  · dà ai PC i file del lettore per partire dalla rete (/live/...)
  · accende i PC già conosciuti dalla rete con un solo comando (sveglia), dalla seconda volta

Comandi:
  banco.py avvia                  il servizio (lo fa partire systemd)
  banco.py stato                  letture di oggi, in coda, ultime arrivate
  banco.py sveglia [L-031]        accende dalla rete i PC già letti (di un lotto, o tutti quelli del banco)
  banco.py controlla              prova il collegamento a Supabase con il codice del banco
Impostazioni: /etc/messina-banco/banco.json (modello in banco.esempio.json). Il codice del banco non va mai in chat.
"""
import datetime, html, json, os, re, socket, sqlite3, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSIONE = '1.0'
PORTA_SCOPERTA = 47474
IMPOSTAZIONI = os.environ.get('MESSINA_BANCO', '/etc/messina-banco/banco.json')
C = {}                      # impostazioni lette all'avvio
DB_LOCK = threading.Lock()
SVEGLIA_SERVER = threading.Event()   # sveglia il mandatario quando arriva qualcosa in coda

def carica_impostazioni(percorso=None):
    with open(percorso or IMPOSTAZIONI) as f: c = json.load(f)
    c.setdefault('porta', 8088); c.setdefault('cartella', '/var/lib/messina-banco'); c.setdefault('immagine', '/srv/messina-banco/live')
    c.setdefault('postazioni', {}); c.setdefault('accendi_dalla_rete', False); c.setdefault('nome', 'Banco')
    for k in ('supabase_url', 'chiave_pubblica', 'codice_banco'):
        if not c.get(k): raise SystemExit(f'Manca «{k}» in {percorso or IMPOSTAZIONI}')
    if not c['chiave_pubblica'].startswith('sb_publishable_'):
        raise SystemExit('La chiave deve essere quella pubblica (sb_publishable_…): la chiave segreta non va mai sul banco')
    c['supabase_url'] = c['supabase_url'].rstrip('/')
    return c

# --------------------------------------------------------------------------- la coda sul disco del server

def db():
    os.makedirs(C['cartella'], exist_ok=True)
    x = sqlite3.connect(os.path.join(C['cartella'], 'letture.sqlite'), timeout=30, check_same_thread=False)
    x.execute('pragma journal_mode=wal'); x.execute('pragma synchronous=full')
    x.execute('''create table if not exists letture (
      id integer primary key, ricevuta_il text not null, seriale text, mac text, ip text, dati text not null,
      stato text not null default 'In coda',          -- In coda · Inviata · Rifiutata
      risposta text, errore text, tentativi integer not null default 0, inviata_il text)''')
    x.execute('create index if not exists letture_stato on letture (stato, id)')
    return x

DB = None

def adesso(): return datetime.datetime.now().astimezone().isoformat(timespec='seconds')

def salva(dati, ip):
    with DB_LOCK:
        cur = DB.execute('insert into letture (ricevuta_il, seriale, mac, ip, dati) values (?, ?, ?, ?, ?)',
                         (adesso(), dati.get('seriale'), dati.get('mac'), ip, json.dumps(dati, ensure_ascii=False)))
        DB.commit(); return cur.lastrowid

def segna(id_, stato, risposta=None, errore=None):
    with DB_LOCK:
        DB.execute('update letture set stato = ?, risposta = ?, errore = ?, tentativi = tentativi + 1, inviata_il = case when ? = \'Inviata\' then ? end where id = ?',
                   (stato, json.dumps(risposta) if risposta is not None else None, errore, stato, adesso(), id_))
        DB.commit()

# --------------------------------------------------------------------------- Supabase

class Rifiutata(Exception):
    """Supabase ha detto no per un motivo che non passa riprovando (seriale non valido, banco non riconosciuto)."""

def rpc(nome, corpo, tempo=10):
    r = urllib.request.Request(f"{C['supabase_url']}/rest/v1/rpc/{nome}", data=json.dumps(corpo).encode(), method='POST',
                               headers={'apikey': C['chiave_pubblica'], 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(r, timeout=tempo) as x: return json.loads(x.read() or b'null')
    except urllib.error.HTTPError as e:
        try: err = json.loads(e.read() or b'{}')
        except ValueError: err = {}
        if 400 <= e.code < 500 and e.code not in (408, 429):
            raise Rifiutata(err.get('message') or f'Errore {e.code}')
        raise

def manda_a_supabase(dati):
    return rpc('registra_lettura', {'p_token': C['codice_banco'], 'p_dati': dati})

def mandatario():
    """Manda quello che è in coda; se internet non c'è riprova da solo (dopo 15 s, poi sempre più radi fino a 2 minuti)."""
    base = C.get('pausa', 15); pausa = base
    while True:
        SVEGLIA_SERVER.wait(pausa); SVEGLIA_SERVER.clear()
        with DB_LOCK:
            righe = DB.execute("select id, dati from letture where stato = 'In coda' order by id limit 50").fetchall()
        if not righe: pausa = base; continue
        for id_, dati in righe:
            try:
                segna(id_, 'Inviata', manda_a_supabase(json.loads(dati))); pausa = base
            except Rifiutata as e:
                segna(id_, 'Rifiutata', errore=str(e))
            except Exception as e:
                with DB_LOCK:
                    DB.execute('update letture set tentativi = tentativi + 1, errore = ? where id = ?', (f'{type(e).__name__}: {e}', id_)); DB.commit()
                pausa = min(120, pausa * 2); break

# --------------------------------------------------------------------------- dai PC del banco

def postazione(dati):
    """Porta dello switch → numero di postazione (dalla tabella delle impostazioni, altrimenti il numero della porta)."""
    porta = dati.get('porta_switch') or dati.get('porta_descrizione')
    if not porta: return None
    if porta in C['postazioni']: return C['postazioni'][porta]
    m = re.search(r'(\d+)\s*$', porta)
    return int(m.group(1)) if m and C.get('postazione_dal_numero_porta', True) else None

class Gestore(BaseHTTPRequestHandler):
    server_version = 'MessinaBanco/' + VERSIONE
    def log_message(self, *a): pass

    def rispondi(self, codice, corpo, tipo='application/json; charset=utf-8'):
        b = corpo if isinstance(corpo, bytes) else (json.dumps(corpo, ensure_ascii=False).encode() if not isinstance(corpo, str) else corpo.encode())
        self.send_response(codice); self.send_header('Content-Type', tipo); self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store'); self.end_headers()
        if self.command != 'HEAD': self.wfile.write(b)

    def do_POST(self):
        if self.path != '/lettura': return self.rispondi(404, {'ok': False, 'errore': 'Indirizzo sbagliato'})
        n = int(self.headers.get('Content-Length') or 0)
        if not 0 < n <= 65536: return self.rispondi(413, {'ok': False, 'errore': 'Lettura troppo grande'})
        try: dati = json.loads(self.rfile.read(n))
        except ValueError: return self.rispondi(400, {'ok': False, 'errore': 'Lettura illeggibile'})
        if not isinstance(dati, dict): return self.rispondi(400, {'ok': False, 'errore': 'Lettura illeggibile'})
        p = postazione(dati)
        if p: dati['postazione'] = p
        id_ = salva(dati, self.client_address[0])          # prima al sicuro sul disco, poi tutto il resto
        try:
            r = manda_a_supabase(dati); segna(id_, 'Inviata', r)
            return self.rispondi(200, {**(r or {}), 'ok': True, 'postazione': p})
        except Rifiutata as e:
            segna(id_, 'Rifiutata', errore=str(e))
            return self.rispondi(200, {'ok': False, 'errore': str(e)})
        except Exception:
            SVEGLIA_SERVER.set()                             # internet giù: resta in coda, il PC può spegnersi
            return self.rispondi(200, {'ok': True, 'in_coda': True, 'postazione': p})

    def do_GET(self):
        if self.path == '/impostazioni': return self.rispondi(200, {'accendi_dalla_rete': bool(C['accendi_dalla_rete']), 'versione': VERSIONE})
        if self.path.startswith('/live/'): return self.file(self.path[len('/live/'):])
        if self.path in ('/', '/stato'): return self.rispondi(200, pagina_stato(), 'text/html; charset=utf-8')
        return self.rispondi(404, {'ok': False})
    do_HEAD = do_GET

    def file(self, nome):
        """I file del lettore per l'avvio dalla rete (kernel, initrd, sistema). Solo quelli: niente percorsi strani."""
        if not re.match(r'^[A-Za-z0-9._-]+$', nome): return self.rispondi(404, {'ok': False})
        p = os.path.join(C['immagine'], nome)
        if not os.path.isfile(p): return self.rispondi(404, {'ok': False})
        size = os.path.getsize(p)
        self.send_response(200); self.send_header('Content-Type', 'application/octet-stream'); self.send_header('Content-Length', str(size)); self.end_headers()
        if self.command == 'HEAD': return
        with open(p, 'rb') as f:
            while True:
                b = f.read(1 << 20)
                if not b: break
                self.wfile.write(b)

def conti():
    oggi = datetime.date.today().isoformat()
    with DB_LOCK:
        tot = DB.execute('select stato, count(*) from letture where substr(ricevuta_il, 1, 10) = ? group by stato', (oggi,)).fetchall()
        coda = DB.execute("select count(*) from letture where stato = 'In coda'").fetchone()[0]
        ultime = DB.execute('select ricevuta_il, seriale, stato, coalesce(errore, ""), dati from letture order by id desc limit 30').fetchall()
    return dict(tot), coda, ultime

def pagina_stato():
    t, coda, ultime = conti()
    righe = ''
    for q, ser, st, err, d in ultime:
        try: x = json.loads(d)
        except ValueError: x = {}
        righe += (f'<tr><td>{html.escape(q[11:19])}</td><td><b>{html.escape(ser or "—")}</b></td><td>{html.escape(" ".join(v for v in (x.get("marca"), x.get("modello")) if v) or "—")}</td>'
                  f'<td>{html.escape(str(x.get("postazione") or "—"))}</td><td>{html.escape(st)}</td><td>{html.escape(err)}</td></tr>')
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5"><title>Banco · {html.escape(C['nome'])}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f2f3f5;color:#111214;margin:0;padding:24px}}h1{{font-size:22px;margin:0 0 16px}}
.g{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}}.c{{background:#fff;border-radius:14px;padding:14px 18px;min-width:140px}}.c b{{font-size:28px;display:block}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:14px;overflow:hidden}}td,th{{padding:8px 10px;border-bottom:1px solid rgba(17,18,20,.09);text-align:left;font-size:14px}}
th{{color:#5b5e66;font-weight:600}}</style></head><body><h1>Banco · {html.escape(C['nome'])}</h1><div class="g">
<div class="c">Letti oggi<b>{sum(t.values())}</b></div><div class="c">Inviati<b>{t.get("Inviata", 0)}</b></div>
<div class="c">In coda<b>{coda}</b></div><div class="c">Rifiutati<b>{t.get("Rifiutata", 0)}</b></div></div>
<table><tr><th>Ora</th><th>Seriale</th><th>Modello</th><th>Postazione</th><th>Stato</th><th>Nota</th></tr>{righe or '<tr><td colspan="6">Nessuna lettura.</td></tr>'}</table></body></html>'''

# --------------------------------------------------------------------------- «c'è un server?»

def scoperta():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', PORTA_SCOPERTA))
    while True:
        try:
            dati, da = s.recvfrom(64)
            if dati.strip() == b'MESSINA-BANCO?': s.sendto(f"MESSINA-BANCO {C['porta']}".encode(), da)
        except OSError:
            time.sleep(1)

# --------------------------------------------------------------------------- accensione dalla rete (dalla seconda volta)

def pacchetto_magico(mac):
    b = bytes.fromhex(mac.replace(':', '').replace('-', ''))
    if len(b) != 6: raise ValueError(mac)
    return b'\xff' * 6 + b * 16

def sveglia(lotto=None):
    macs = set()
    try:
        for r in rpc('banco_conosciuti', {'p_token': C['codice_banco'], 'p_lotto': lotto}) or []:
            if r.get('mac'): macs.add(r['mac'])
    except Exception as e:
        print('Supabase non raggiungibile, uso le letture salvate qui:', e)
        with DB_LOCK:
            for mac, risposta in DB.execute("select mac, risposta from letture where mac is not null and stato = 'Inviata'"):
                if not lotto or (json.loads(risposta or '{}') or {}).get('lotto') == lotto: macs.add(mac)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for m in sorted(macs):
        for porta in (9, 7):
            s.sendto(pacchetto_magico(m), (C.get('broadcast', '255.255.255.255'), porta))
        time.sleep(0.2)                                   # uno alla volta: 60 PC che partono insieme non stressano la corrente
    print(f'Accensione mandata a {len(macs)} PC' + (f' del lotto {lotto}' if lotto else '') +
          '. Partono solo quelli con l\'accensione dalla rete attiva nel BIOS e la corrente attaccata.')
    return len(macs)

# --------------------------------------------------------------------------- avvio

def principale(argv):
    global C, DB
    cmd = argv[0] if argv else 'avvia'
    C = carica_impostazioni(); DB = db()
    if cmd == 'avvia':
        threading.Thread(target=scoperta, daemon=True).start()
        threading.Thread(target=mandatario, daemon=True).start()
        SVEGLIA_SERVER.set()
        srv = ThreadingHTTPServer(('0.0.0.0', C['porta']), Gestore); srv.daemon_threads = True
        print(f"Banco «{C['nome']}» pronto sulla porta {C['porta']}", flush=True)
        srv.serve_forever()
    elif cmd == 'stato':
        t, coda, ultime = conti()
        print(f"Oggi: {sum(t.values())} letti · {t.get('Inviata', 0)} inviati · {coda} in coda · {t.get('Rifiutata', 0)} rifiutati")
        for q, ser, st, err, _ in ultime[:15]: print(f'  {q[11:19]}  {ser or "—":<14} {st:<10} {err}')
    elif cmd == 'sveglia':
        sveglia(argv[1] if len(argv) > 1 else None)
    elif cmd == 'controlla':
        try:
            rpc('banco_conosciuti', {'p_token': C['codice_banco'], 'p_lotto': '—'}); print('Collegamento a Supabase: ok, il banco è riconosciuto.')
        except Rifiutata as e: print('Supabase risponde, ma:', e); return 1
        except Exception as e: print('Supabase non raggiungibile:', e); return 1
    else:
        print(__doc__); return 1
    return 0

if __name__ == '__main__':
    sys.exit(principale(sys.argv[1:]))
