# SimpleCutVideo

Applicazione desktop per Windows 10/11 che taglia file video con una o più selezioni ed esporta le parti in **MP4** (video e audio) oppure **MP3** (solo audio).

Funziona in versione **portabile**: si scompatta lo zip in una cartella qualsiasi, anche su una chiavetta USB, e si avvia `SimpleCutVideo.exe`. Non servono installazione né permessi di amministratore, e FFmpeg è già incluso.

## Funzioni

- **Apertura** dal menu, con il pulsante o trascinando il file nella finestra. I formati supportati sono MP4, M4V, MOV, MKV, AVI, WebM, TS/MTS/M2TS, WMV, MPG e 3GP.
- **Barra di riproduzione** con play/pausa, stop, ±5 secondi, avanzamento di un fotogramma e volume. Il tempo è mostrato nel formato `hh:mm:ss.mmm`.
- **Barra delle selezioni** con selezioni multiple. Le maniglie si trascinano, il corpo della selezione si sposta, e un doppio clic su un'area vuota crea una nuova selezione.
- **Campi Inizio e Fine** modificabili, con i pulsanti "Da posizione corrente" (tasti `I` e `O`) e il campo **Fine = Inizio + N secondi**.
- **Anteprima** della sola selezione attiva (tasto `P`) ed elenco delle selezioni.
- **Esportazione**:
  - con una selezione il file si divide in tre parti (prima, selezione, dopo); con N selezioni le parti sono 2N+1;
  - si scelgono le parti con le caselle di spunta, oppure con le scorciatoie *Solo selezioni*, *Solo parti esterne*, *Tutte*;
  - l'opzione **Unisci in un unico file** permette, per esempio, di eliminare dei pezzi dal video tenendo solo le parti esterne;
  - formato **MP4** in due modalità:
    - **Precisa**: ricodifica al fotogramma, usando la scheda grafica NVIDIA, Intel o AMD se presente, altrimenti la CPU;
    - **Veloce**: copia senza ricodifica, con inizio sul fotogramma chiave più vicino, indicato con ≈ nella colonna Inizio;
  - formato **MP3** con bitrate a scelta da 96 a 320 kbps;
  - **stima della dimensione** di ogni file e del totale;
  - nomi dei file proposti ma modificabili, avviso prima di sovrascrivere, barra di avanzamento con Annulla e pulsante "Apri cartella" alla fine.
- **Video HDR** (es. iPhone): in modalità precisa i colori vengono convertiti automaticamente in SDR.
- **Lingue**: italiano, inglese, francese, spagnolo e tedesco, selezionabili dal menu *Lingua*. Al primo avvio viene usata la lingua di Windows.
- **Impostazioni** (lingua, volume, ultime scelte di esportazione) salvate in `SimpleCutVideo.ini` accanto all'eseguibile. Se la cartella è in sola lettura vengono salvate in `%APPDATA%\SimpleCutVideo`.

## Avvio da sorgente (sviluppo)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-build.txt
python -m simplecutvideo
```

FFmpeg viene cercato in quest'ordine:
1. nella cartella indicata dalla variabile d'ambiente `SIMPLECUTVIDEO_FFMPEG`;
2. in `ffmpeg\bin` dentro il progetto (la crea `build.bat`);
3. nel `PATH` di sistema.

Test: `python -m pytest -q tests`. I test di esportazione usano FFmpeg reale su video generati al volo.

## Struttura

```
simplecutvideo/
  core/         logica senza interfaccia (testabile)
    timecode.py   conversioni hh:mm:ss.mmm
    segments.py   selezioni multiple, vincoli, parti in output, nomi file
    ffmpeg.py     ricerca di FFmpeg, analisi file (ffprobe), keyframe, encoder disponibili
    planner.py    comandi FFmpeg per ogni modalità e stime delle dimensioni
  exporter.py   esecuzione dei comandi con avanzamento, annullamento e ripiego su CPU
  ui/           finestra principale, barra delle selezioni, finestra di esportazione, tema
  translations.py  testi nelle 5 lingue
tools/build.ps1   build portabile (usata da build.bat e da GitHub Actions)
.github/workflows/build-windows.yml
tests/
```

## Note

- **Primo avvio e SmartScreen**: l'eseguibile non ha una firma digitale, quindi Windows può mostrare "Windows ha protetto il PC". Clicca su *Ulteriori informazioni → Esegui comunque*. Sui PC aziendali le policy potrebbero richiedere l'autorizzazione dell'IT.
- **Dimensione**: la cartella portabile occupa circa 350–450 MB, lo zip circa 150–200 MB. La parte più pesante è FFmpeg.
- **Licenze**: vedi `THIRD_PARTY_NOTICES.txt`. FFmpeg incluso è in versione GPL e Qt/PySide6 è LGPL. Per uso personale o interno non ci sono vincoli particolari.

   Copyright (C) 2026 Nahlt

   Questo programma è software libero: puoi ridistribuirlo e/o modificarlo
   secondo i termini della GNU General Public License come pubblicata dalla
   Free Software Foundation, versione 3 della licenza.
   Questo programma è distribuito nella speranza che sia utile, ma SENZA
   ALCUNA GARANZIA. Vedi il file LICENSE per i dettagli.
