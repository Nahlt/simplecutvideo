<#
.SYNOPSIS
    Crea la versione portabile di SimpleCutVideo per Windows (cartella + zip).

.DESCRIPTION
    1. crea un ambiente Python dedicato e installa le dipendenze (saltato con -NoVenv)
    2. scarica FFmpeg (build GPL "shared" di BtbN) se non è già presente in .\ffmpeg
    3. esegue i test automatici (saltati con -SkipTests)
    4. compila l'applicazione con PyInstaller
    5. copia FFmpeg, licenze e LEGGIMI nel pacchetto e crea lo zip in .\dist

.PARAMETER NoVenv
    Usa il Python già attivo invece di creare .venv-build (usato da GitHub Actions).

.PARAMETER SkipTests
    Non esegue i test prima della compilazione.
#>
param(
    [switch]$NoVenv,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"   # rende Invoke-WebRequest molto più veloce
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Run([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Comando non riuscito ($LASTEXITCODE): $exe $($arguments -join ' ')" }
}

# ------------------------------------------------------------------ Python
Step "Ambiente Python"
if ($NoVenv) {
    $Python = (Get-Command python).Source
} else {
    $candidates = @("py", "python")
    $base = $null
    foreach ($c in $candidates) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $base = $c; break }
    }
    if (-not $base) {
        throw "Python non trovato. Installa Python 3.12 o 3.13 da https://www.python.org/downloads/ (spunta 'Add python.exe to PATH')."
    }
    if (-not (Test-Path ".venv-build\Scripts\python.exe")) {
        if ($base -eq "py") { Run "py" @("-3", "-m", "venv", ".venv-build") }
        else { Run "python" @("-m", "venv", ".venv-build") }
    }
    $Python = Join-Path $Root ".venv-build\Scripts\python.exe"
}
Run $Python @("-m", "pip", "install", "--upgrade", "pip")
Run $Python @("-m", "pip", "install", "-r", "requirements.txt", "-r", "requirements-build.txt")
$Version = (& $Python -c "import simplecutvideo; print(simplecutvideo.APP_VERSION)").Trim()
Write-Host "Versione: $Version"

# ------------------------------------------------------------------ FFmpeg
Step "FFmpeg"
$FFBin = Join-Path $Root "ffmpeg\bin"
if ((Test-Path "$FFBin\ffmpeg.exe") -and (Test-Path "$FFBin\ffprobe.exe")) {
    Write-Host "FFmpeg già presente in $FFBin"
} else {
    $headers = @{ "User-Agent" = "SimpleCutVideo-build" }
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers $headers
    # Preferisce l'ultima versione stabile (ffmpeg-nX.Y-latest-win64-gpl-shared-X.Y.zip)
    $stable = $release.assets | Where-Object { $_.name -match '^ffmpeg-n(\d+)\.(\d+)-latest-win64-gpl-shared-[\d.]+\.zip$' } |
        Sort-Object -Descending -Property @{ Expression = { [int]($_.name -replace '^ffmpeg-n(\d+)\..*$', '$1') } },
                                           @{ Expression = { [int]($_.name -replace '^ffmpeg-n\d+\.(\d+)-.*$', '$1') } }
    $asset = $stable | Select-Object -First 1
    if (-not $asset) {
        $asset = $release.assets | Where-Object { $_.name -eq "ffmpeg-master-latest-win64-gpl-shared.zip" } | Select-Object -First 1
    }
    if (-not $asset) { throw "Nessuna build FFmpeg per Windows trovata nella release di BtbN." }
    Write-Host "Scarico $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)…"
    $tmp = Join-Path $Root "build\ffmpeg-download"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp $asset.name
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -Headers @{ "User-Agent" = "SimpleCutVideo-build" }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $extracted = Get-ChildItem $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Path $FFBin -Force | Out-Null
    Copy-Item "$($extracted.FullName)\bin\ffmpeg.exe", "$($extracted.FullName)\bin\ffprobe.exe" $FFBin
    Copy-Item "$($extracted.FullName)\bin\*.dll" $FFBin
    Copy-Item "$($extracted.FullName)\LICENSE.txt" (Join-Path $Root "ffmpeg\LICENSE.txt") -ErrorAction SilentlyContinue
    Set-Content -Path (Join-Path $Root "ffmpeg\SOURCE.txt") -Value "FFmpeg: $($asset.name)`r`nhttps://github.com/BtbN/FFmpeg-Builds`r`nCodice sorgente FFmpeg: https://ffmpeg.org/download.html"
    Remove-Item $tmp -Recurse -Force
}
Run "$FFBin\ffmpeg.exe" @("-hide_banner", "-version")

# ------------------------------------------------------------------ test
if (-not $SkipTests) {
    Step "Test"
    $env:QT_QPA_PLATFORM = "offscreen"
    Run $Python @("-m", "pytest", "-q", "tests")
    Remove-Item Env:\QT_QPA_PLATFORM
}

# ------------------------------------------------------------------ build
Step "Compilazione con PyInstaller"
Run $Python @("-m", "PyInstaller", "--noconfirm", "--clean", "SimpleCutVideo.spec")

$Dist = Join-Path $Root "dist\SimpleCutVideo"
New-Item -ItemType Directory -Path "$Dist\ffmpeg" -Force | Out-Null
Copy-Item "$FFBin\*" "$Dist\ffmpeg\" -Force
Copy-Item (Join-Path $Root "ffmpeg\*.txt") "$Dist\ffmpeg\" -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root "THIRD_PARTY_NOTICES.txt") $Dist
Copy-Item (Join-Path $Root "LEGGIMI.txt") $Dist

# ------------------------------------------------------------------ verifica e zip
Step "Verifica del pacchetto"
foreach ($f in @("SimpleCutVideo.exe", "ffmpeg\ffmpeg.exe", "ffmpeg\ffprobe.exe")) {
    if (-not (Test-Path (Join-Path $Dist $f))) { throw "File mancante nel pacchetto: $f" }
}
$size = (Get-ChildItem $Dist -Recurse | Measure-Object -Property Length -Sum).Sum
Write-Host ("Dimensione cartella: {0:N0} MB" -f ($size / 1MB))

$ZipPath = Join-Path $Root "dist\SimpleCutVideo-$Version-win64.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path $Dist -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Host ("Zip: {0} ({1:N0} MB)" -f $ZipPath, ((Get-Item $ZipPath).Length / 1MB)) -ForegroundColor Green
