# Banco · strada «Lettura»

PC già formattati: si leggono al banco **senza cancellare niente**, e il lotto ha il suo Excel.
Deciso con Alessandro il 23 novembre 2026. Il gestionale e l'app sono una **proposta da approvare** (ramo `lettura`).

## Come funziona

1. In Corporation › Lotti › **Nuovo** si apre il lotto con la strada **Lettura** (le altre sono **Banco**, cioè formattazione con Certus, e **Pronto**).
2. I PC vanno sul banco col cavo. L'operatore preme **solo il tasto di accensione**: col disco vuoto il PC passa da solo alla rete e carica il lettore dal server del banco.
3. Il lettore legge seriale, marca, modello, codice del modello, processore, RAM, disco con la salute e le ore, batteria (% e cicli), schermo, scheda video, Wi-Fi, webcam, password del BIOS e indirizzo di rete. **Non monta e non scrive mai il disco**, e il Windows del PC non parte.
4. Manda la lettura al server, scrive «LETTO» a tutto schermo e si spegne. Se qualcosa non va lo scrive in giallo o in rosso e **non** si spegne.
5. Il server salva la lettura sul suo disco e poi la manda al gestionale. Se internet cade la tiene e la manda dopo, da sola.
6. In Stage Computer › **Lettura** si vede l'avanzamento («letti 37 di 60, caricati 12, 2 da guardare»). In Lotti, «Excel» scarica il file del lotto: una riga per PC.
7. Nell'app: foto del seriale, la scheda è già piena con i dati veri del PC, poi grado, prezzo, bancale e posto. Un bancale senza posto non si conferma.

Dalla seconda volta il server può accendere tutti i PC già letti con un comando solo (`banco.py sveglia L-031`). Funziona se il BIOS ha l'accensione dalla rete e il PC ha la corrente attaccata. Il lettore la attiva da solo solo se in `banco.json` c'è `"accendi_dalla_rete": true` e il BIOS non ha password.

## Le parti

| Parte | Dove | Cosa fa |
|---|---|---|
| Lettore | `lettore/leggi.py` | Gira sul PC da leggere (Python, solo lettura) |
| Sistema del lettore | `immagine/` | Debian 13 per l'avvio dalla rete e per la chiavetta di riserva |
| Server del banco | `server/banco.py` | Riceve, tiene in coda, manda a Supabase, pagina di stato, accensione dalla rete |
| Avvio dalla rete | `server/prepara_rete.py`, `server/installa.sh` | dnsmasq in modo «proxy» (gli indirizzi li dà sempre il router) e menu di avvio UEFI e BIOS |
| Database | `supabase/migrations/20261123000100_lettura.sql` (repository privato) | strada del lotto, banchi, letture, Excel, posto obbligatorio |

## Installare il server del banco

Su un PC con **Debian 13**, collegato con il cavo allo switch del banco:

```bash
sudo bash server/installa.sh --scheda <presa del banco> --costruisci
```

Al posto di `--costruisci` (10-15 minuti, serve internet) si può usare `--immagine messina-lettore.zip`, lo zip che costruisce GitHub (Actions › Lettore).
Poi, in `/etc/messina-banco/banco.json`, vanno scritti l'URL di Supabase, la chiave **pubblica** (`sb_publishable_…`) e il **codice del banco**. Il codice lo dà una volta sola il gestionale (`nuovo_banco`). **Non va mai in chat.**

## Prove

| Prova | Comando | Dove |
|---|---|---|
| Lettore con PC finti (Lenovo, HP, Dell, PC senza seriale) | `python3 lettore/prova_lettore.py` | Passata qui |
| Banco completo sul database di prova (scoperta, lotto giusto, internet che cade, rifiuto, stato, accensione dalla rete) | `python3 banco/prova_banco.py` (repository privato) | Passata qui |
| Database (permessi, regole, Excel) | `bash supabase/tests/run.sh` (repository privato) | Passata qui |
| Sistema del lettore costruito e acceso in una macchina virtuale: avvio dalla rete UEFI, lettura, spegnimento, disco non toccato | `immagine/prova_qemu.sh` | **Da fare**: GitHub Actions è fermo per il limite di spesa dell'account. Si fa sul server del banco o quando GitHub riparte |
| Installazione del server (`installa.sh`) e avvio dalla rete con dnsmasq | sul PC del banco | **Da fare**: da qui i pacchetti Debian non si scaricano |
| PC veri: un Lenovo, un HP e un Dell già formattati | sul banco | **Da fare** |

## Da verificare sul banco (non scritto come certo)

- Che Lenovo, Dell e HP col disco vuoto passino **da soli** alla rete. Se non lo fanno, la riserva è la chiavetta (`messina-lettore.iso`).
- Il percorso del menu di avvio del GRUB firmato Debian: il menu si mette in tre posti, per sicurezza.
- I cicli della batteria: su alcuni HP il firmware non li dice (restano vuoti).
- La password del BIOS: si legge su Lenovo, Dell e HP recenti, ma non su tutti i modelli (resta vuota).
- La postazione: arriva dalla porta dello switch solo se lo switch la annuncia (LLDP). Serve il modello dello switch.
