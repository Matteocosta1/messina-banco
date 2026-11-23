# Messina · banco

Lettore dei PC già formattati (strada «Lettura») e server del banco. È solo la parte generica: nessun dato dell'azienda, nessuna chiave.
Il gestionale e il database stanno nel repository privato. Istruzioni: [LEGGIMI.md](LEGGIMI.md).

A ogni push GitHub costruisce il sistema del lettore (Debian 13) e lo prova in una macchina virtuale che parte dalla rete,
legge, manda la lettura e si spegne (Actions › Lettore). I file pronti restano 30 giorni nell'esecuzione, come «messina-lettore».
