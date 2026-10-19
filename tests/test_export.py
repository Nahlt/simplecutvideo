"""Test di integrazione: tagli reali con FFmpeg su un video generato al volo."""

import json
import os
import subprocess
import tempfile

import pytest

from simplecutvideo.core.ffmpeg import detect_capabilities, find_ffmpeg, probe, read_keyframes
from simplecutvideo.core.planner import (
    ExportSettings, FMT_MP3, MODE_FAST, MODE_PRECISE, plan_export,
)
from simplecutvideo.core.segments import Range, build_pieces

FF = find_ffmpeg()
pytestmark = pytest.mark.skipif(FF is None, reason="ffmpeg non disponibile")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """Video 20 s, 30 fps, keyframe ogni 5 s, con audio."""
    path = str(tmp_path_factory.mktemp("src") / "sample video's.mkv")
    subprocess.run(
        [FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
         "-f", "lavfi", "-i", "sine=frequency=440:d=20",
         "-c:v", "libx264", "-g", "150", "-keyint_min", "150", "-sc_threshold", "0",
         "-c:a", "libvorbis", path],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def caps():
    return detect_capabilities(FF.ffmpeg)


def _duration(path, stream="v"):
    out = subprocess.run(
        [FF.ffprobe, "-v", "error", "-select_streams", f"{stream}:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,duration:format=duration",
         "-of", "json", path],
        capture_output=True, check=True,
    ).stdout
    return json.loads(out)


def _run(qapp, info, outputs, settings, caps, temp_dir=None, keyframes=None):
    from PySide6.QtCore import QEventLoop
    from simplecutvideo.exporter import Exporter

    result = {}
    loop = QEventLoop()

    def plan(enc):
        return plan_export(FF.ffmpeg, info, outputs, settings, enc, caps.has_tonemap, temp_dir,
                           keyframes)

    exp = Exporter(plan, caps.software, None, temp_dir)

    def done(ok, cancelled, created, details):
        result.update(ok=ok, cancelled=cancelled, created=created, details=details)
        loop.quit()

    exp.finished.connect(done)
    exp.start()
    loop.exec()
    assert result["ok"], result["details"]
    return result


def test_precise_cut_frame_accurate(qapp, sample, caps, tmp_path):
    info = probe(FF.ffprobe, sample)
    assert not info.audio_copy_ok  # vorbis: non copiabile in MP4
    pieces = build_pieces(info.duration_ms, [Range(3_250, 7_750)])
    out = str(tmp_path / "precise.mp4")
    _run(qapp, info, [(out, [pieces[1]])], ExportSettings(mode=MODE_PRECISE), caps)
    data = _duration(out)
    frames = int(data["streams"][0]["nb_read_frames"])
    assert abs(frames - 135) <= 1   # 4.5 s × 30 fps


def test_precise_merge_removes_middle(qapp, sample, caps, tmp_path):
    info = probe(FF.ffprobe, sample)
    pieces = build_pieces(info.duration_ms, [Range(5_000, 15_000)])
    outside = [p for p in pieces if not p.selected]
    out = str(tmp_path / "merged.mp4")
    _run(qapp, info, [(out, outside)], ExportSettings(mode=MODE_PRECISE, merge=True), caps)
    data = _duration(out)
    assert abs(int(data["streams"][0]["nb_read_frames"]) - 300) <= 2
    adur = float(_duration(out, "a")["format"]["duration"])
    assert abs(adur - 10.0) < 0.15


def test_fast_cut_and_merge(qapp, sample, caps, tmp_path):
    info = probe(FF.ffprobe, sample)
    pieces = build_pieces(info.duration_ms, [Range(5_000, 10_000), Range(15_000, 18_000)])
    sel = [p for p in pieces if p.selected]
    keyframes = read_keyframes(FF.ffprobe, sample)
    assert [round(k) for k in keyframes] == [0, 5, 10, 15]
    single = str(tmp_path / "fast.mp4")
    _run(qapp, info, [(single, [sel[0]])], ExportSettings(mode=MODE_FAST), caps,
         keyframes=keyframes)
    streams = _duration(single)
    assert abs(float(streams["format"]["duration"]) - 5.0) < 0.1

    temp_dir = tempfile.mkdtemp(dir=str(tmp_path))
    merged = str(tmp_path / "fast_merged.mp4")
    _run(qapp, info, [(merged, sel)], ExportSettings(mode=MODE_FAST, merge=True), caps, temp_dir,
         keyframes)
    assert abs(float(_duration(merged)["format"]["duration"]) - 8.0) < 0.15
    assert not os.path.exists(temp_dir)


def test_mp3_separate_and_merged(qapp, sample, caps, tmp_path):
    info = probe(FF.ffprobe, sample)
    pieces = build_pieces(info.duration_ms, [Range(2_000, 6_000)])
    outs = [(str(tmp_path / f"p{p.number}.mp3"), [p]) for p in pieces]
    res = _run(qapp, info, outs, ExportSettings(fmt=FMT_MP3, mp3_bitrate=128), caps)
    assert len(res["created"]) == 3
    durations = [float(_duration(p, "a")["format"]["duration"]) for p, _ in outs]
    assert [round(d) for d in durations] == [2, 4, 14]

    merged = str(tmp_path / "m.mp3")
    _run(qapp, info, [(merged, [pieces[0], pieces[2]])],
         ExportSettings(fmt=FMT_MP3, mp3_bitrate=320, merge=True), caps)
    assert abs(float(_duration(merged, "a")["format"]["duration"]) - 16.0) < 0.2


def test_hdr_tonemap(qapp, caps, tmp_path):
    if not caps.has_tonemap:
        pytest.skip("zscale/tonemap non disponibili")
    src = str(tmp_path / "hdr.mp4")
    subprocess.run(
        [FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3",
         "-pix_fmt", "yuv420p10le", "-c:v", "libx265",
         # metadati colore scritti nel flusso HEVC: da FFmpeg 9 le opzioni
         # -color_trc ecc. non arrivano più a libx265 in questo caso
         "-x265-params", "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
         src],
        check=True,
    )
    info = probe(FF.ffprobe, src)
    if not info.is_hdr:
        pytest.skip("impossibile generare un video HDR di prova con questa versione di FFmpeg")
    assert not info.has_audio
    pieces = build_pieces(info.duration_ms, [Range(500, 2_500)])
    out = str(tmp_path / "sdr.mp4")
    _run(qapp, info, [(out, [pieces[1]])], ExportSettings(), caps)
    meta = json.loads(subprocess.run(
        [FF.ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt,color_transfer", "-of", "json", out],
        capture_output=True, check=True).stdout)["streams"][0]
    assert meta["pix_fmt"] == "yuv420p" and meta["color_transfer"] == "bt709"


def test_cancel_removes_partial(qapp, sample, caps, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from simplecutvideo.exporter import Exporter

    info = probe(FF.ffprobe, sample)
    pieces = build_pieces(info.duration_ms, [])
    out = str(tmp_path / "cancel.mp4")

    def plan(enc):
        return plan_export(FF.ffmpeg, info, [(out, pieces)], ExportSettings(), enc, False)

    exp = Exporter(plan, caps.software, None)
    result = {}
    loop = QEventLoop()
    exp.finished.connect(lambda ok, c, created, d: (result.update(ok=ok, c=c), loop.quit()))
    exp.start()
    QTimer.singleShot(150, exp.cancel)
    loop.exec()
    assert result == {"ok": False, "c": True}
    assert not os.path.exists(out)


def test_cover_art_and_surround_audio(qapp, caps, tmp_path):
    """File con copertina (seconda traccia video), audio 5.1 a 96 kHz: MP4 e MP3 devono funzionare."""
    base = str(tmp_path / "base.mp4")
    cover = str(tmp_path / "cover.png")
    src = str(tmp_path / "with_cover.mp4")
    subprocess.run([FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=4",
                    "-f", "lavfi", "-i", "sine=frequency=300:d=4:sample_rate=96000",
                    "-filter_complex", "[1:a]pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", base], check=True)
    subprocess.run([FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1", cover], check=True)
    subprocess.run([FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", cover, "-i", base,
                    "-map", "0", "-map", "1", "-c", "copy", "-disposition:v:0", "attached_pic", src],
                   check=True)
    info = probe(FF.ffprobe, src)
    assert info.vcodec == "h264" and info.a_channels == 6 and info.a_sample_rate == 96000
    pieces = build_pieces(info.duration_ms, [Range(1_000, 3_000)])
    mp4 = str(tmp_path / "out.mp4")
    _run(qapp, info, [(mp4, [pieces[1]])], ExportSettings(), caps)
    meta = json.loads(subprocess.run([FF.ffprobe, "-v", "error", "-select_streams", "v:0",
                                      "-show_entries", "stream=width", "-of", "json", mp4],
                                     capture_output=True, check=True).stdout)
    assert meta["streams"][0]["width"] == 320
    mp3 = str(tmp_path / "out.mp3")
    _run(qapp, info, [(mp3, [pieces[1]])], ExportSettings(fmt=FMT_MP3), caps)
    assert abs(float(_duration(mp3, "a")["format"]["duration"]) - 2.0) < 0.1
