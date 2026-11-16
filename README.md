# SimpleCutVideo

Applicazione desktop per **Windows 10/11** e **Linux** (Linux Mint 21/22, Ubuntu 22.04 e successive) che taglia file video o audio con una o più selezioni ed esporta le parti in **MP4** (video e audio) oppure **MP3** (solo audio).

Funziona in versione **portabile**, senza installazione né permessi di amministratore, con FFmpeg già incluso:

- **Windows**: si scompatta lo zip in una cartella qualsiasi, anche su una chiavetta USB, e si avvia `SimpleCutVideo.exe`;
- **Linux**: si scarica l'AppImage, la si rende eseguibile e la si avvia con un doppio clic (vedi [Linux](#linux)).

## Funzioni

- **Apertura** dal menu, con il pulsante o trascinando il file nella finestra. I formati supportati sono:
  - video: MP4, M4V, MOV, MKV, AVI, WebM, TS/MTS/M2TS, WMV, MPG e 3GP;
  - solo audio: MP3 (anche con copertina), M4A, AAC, WAV, FLAC, OGG, Opus e WMA. Al posto del video compare il nome del file, e l'esportazione è disponibile solo in MP3.
- **Barra di riproduzione** con play/pausa, stop, ±5 secondi, avanzamento di un fotogramma e volume. Il tempo è mostrato nel formato `hh:mm:ss.mmm`.
- **Barra delle selezioni** con selezioni multiple. Le maniglie si trascinano, il corpo della selezione si sposta, e un doppio clic su un'area vuota crea una nuova selezione.
- **Campi Inizio e Fine** modificabili, con i pulsanti "Da posizione corrente" (tasti `I` e `O`) e il campo **Fine = Inizio + N secondi**.
- **Anteprima** della sola selezione attiva (tasto `P`) ed elenco delle selezioni.
- **Esportazione**:
  - con una selezione il file si divide in tre parti (prima, selezione, dopo); con N selezioni le parti sono 2N+1;
  - si scelgono le parti con le caselle di spunta, oppure con le scorciatoie *Solo selezioni*, *Solo parti esterne*, *Tutte*;
  - l'opzione **Unisci in un unico file** permette, per esempio, di eliminare dei pezzi dal video tenendo solo le parti esterne;
  - formato **MP4** in due modalità:
    - **Precisa**: ricodifica al fotogramma, usando la scheda grafica NVIDIA, Intel o AMD se presente (su Linux tramite NVENC o VA-API), altrimenti la CPU;
    - **Veloce**: copia senza ricodifica, con inizio sul fotogramma chiave più vicino, indicato con ≈ nella colonna Inizio;
  - formato **MP3** con bitrate a scelta da 96 a 320 kbps. Se il file di partenza è già un MP3, l'opzione **Senza ricompressione** (attiva di default) taglia l'audio senza perdita di qualità, con precisione di circa 1/40 di secondo;
  - **stima della dimensione** di ogni file e del totale;
  - nomi dei file proposti ma modificabili, avviso prima di sovrascrivere, barra di avanzamento con Annulla e pulsante "Apri cartella" alla fine.
- **Video HDR** (es. iPhone): in modalità precisa i colori vengono convertiti automaticamente in SDR.
- **Lingue**: italiano, inglese, francese, spagnolo e tedesco, selezionabili dal menu *Lingua*. Al primo avvio viene usata la lingua del sistema.
- **Impostazioni** (lingua, volume, ultime scelte di esportazione):
  - Windows: `SimpleCutVideo.ini` accanto all'eseguibile, oppure `%APPDATA%\SimpleCutVideo` se la cartella è in sola lettura;
  - Linux: `~/.config/SimpleCutVideo/SimpleCutVideo.ini`, oppure un file `SimpleCutVideo.ini` accanto all'AppImage, se esiste (modalità portabile).

## Linux

### Uso

1. Scarica `SimpleCutVideo-<versione>-x86_64.AppImage` dalla pagina **Releases**.
2. Tasto destro → *Proprietà* → *Permessi* → *Consenti l'esecuzione del file come programma* (oppure `chmod +x SimpleCutVideo-*.AppImage`).
3. Doppio clic per avviarla.

Per avere la voce nel menu delle applicazioni e in "Apri con" di Nemo, esegui una volta:

```bash
./SimpleCutVideo-<versione>-x86_64.AppImage --install-desktop
```

`--uninstall-desktop` rimuove la voce. Se l'AppImage non parte, installa `libfuse2t64` (Mint 22) o `libfuse2` (Mint 21), oppure usa l'archivio `.tar.gz`, che non richiede FUSE: estrailo e avvia `./SimpleCutVideo`.

### Build

- **GitHub Actions**: il workflow `Build Linux` (`.github/workflows/build-linux.yml`) gira su Ubuntu 22.04 accanto a quello Windows, con gli stessi trigger. Con un tag `v*` l'AppImage e il `.tar.gz` vengono allegati alla stessa Release dello zip Windows.
- **Sul tuo PC** (Mint o Ubuntu):

  ```bash
  sudo apt install python3-venv libxcb-cursor0 curl
  bash tools/build_linux.sh         # opzioni: --skip-tests, --no-venv
  ```

  Risultato in `dist/`: `SimpleCutVideo-<versione>-x86_64.AppImage` e `SimpleCutVideo-<versione>-linux-x86_64.tar.gz`.

### Avvio da sorgente

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt
sudo apt install ffmpeg libxcb-cursor0     # oppure FFmpeg in ./ffmpeg
python -m simplecutvideo
```

## Come ottenere l'eseguibile Windows

L'eseguibile Windows va creato su Windows. Le strade sono due.

### A. GitHub Actions (consigliata: non serve installare nulla)

1. Su GitHub crea un repository **privato**, ad esempio `simplecutvideo`.
2. Carica tutti i file di questa cartella. Il modo più semplice è *Add file → Upload files* trascinando il contenuto dello zip, **compresa la cartella `.github`**. Se Esplora risorse la nasconde, abilita la visualizzazione dei file nascosti.
3. Il caricamento sul ramo `main` avvia la compilazione. Apri la scheda **Actions → Build Windows** e attendi circa 10–15 minuti. L'esecuzione lancia anche i test automatici su Windows.
4. In fondo alla pagina dell'esecuzione, nella sezione **Artifacts**, scarica `SimpleCutVideo-win64`.
5. Per una versione "ufficiale", crea un tag, ad esempio `v1.1.0` (*Releases → Draft a new release*). Lo zip viene allegato automaticamente alla Release.

Puoi rilanciare la compilazione in qualsiasi momento da *Actions → Build Windows → Run workflow*.

### B. Sul tuo PC con `build.bat`

Requisiti: Python 3.12 o 3.13 da [python.org](https://www.python.org/downloads/), con l'opzione *Add python.exe to PATH* spuntata, e una connessione Internet.

1. Scompatta il progetto in una cartella.
2. Fai doppio clic su `build.bat`.

Lo script esegue questi passi:
- crea un ambiente Python dedicato (`.venv-build`);
- scarica le dipendenze e FFmpeg;
- esegue i test;
- compila con PyInstaller.

Il risultato è in `dist\SimpleCutVideo\` (cartella portabile) e in `dist\SimpleCutVideo-<versione>-win64.zip`.

Opzioni: `build.bat -SkipTests` salta i test.

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
    ffmpeg.py     ricerca di FFmpeg, analisi file (ffprobe), keyframe, encoder disponibili (NVENC, QSV, AMF, VA-API)
    planner.py    comandi FFmpeg per ogni modalità e stime delle dimensioni
  exporter.py   esecuzione dei comandi con avanzamento, annullamento e ripiego su CPU
  ui/           finestra principale, barra delle selezioni, finestra di esportazione, tema
  translations.py  testi nelle 5 lingue
  linux_integration.py  voce di menu e "Apri con" su Linux (--install-desktop)
packaging/linux/  AppRun, file .desktop e icona dell'AppImage
tools/build.ps1   build portabile Windows (usata da build.bat e da GitHub Actions)
tools/build_linux.sh  build AppImage per Linux
.github/workflows/build-windows.yml
.github/workflows/build-linux.yml
tests/
```

## Note

- **Primo avvio e SmartScreen**: l'eseguibile non ha una firma digitale, quindi Windows può mostrare "Windows ha protetto il PC". Clicca su *Ulteriori informazioni → Esegui comunque*. Sui PC aziendali le policy potrebbero richiedere l'autorizzazione dell'IT.
- **Dimensione**: la cartella portabile occupa circa 350–450 MB, lo zip circa 150–200 MB. La parte più pesante è FFmpeg.
- **Licenze**: vedi `THIRD_PARTY_NOTICES.txt`. FFmpeg incluso è in versione GPL e Qt/PySide6 è LGPL. Per uso personale o interno non ci sono vincoli particolari.

Copyright (C) 2026 Nahlt

Questo programma è software libero: puoi ridistribuirlo e/o modificarlo secondo i termini della GNU General Public License come pubblicata dalla Free Software Foundation, versione 3 della licenza. Questo programma è distribuito nella speranza che sia utile, ma SENZA ALCUNA GARANZIA. Vedi il file LICENSE per i dettagli.
