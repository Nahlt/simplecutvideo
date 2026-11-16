"""File solo audio (MP3, WAV…): analisi, taglio senza ricompressione e ricodifica."""

import json
import os
import subprocess
import tempfile

import pytest

from simplecutvideo.core.ffmpeg import (
    ProbeError, detect_capabilities, find_ffmpeg, parse_probe, probe,
)
from simplecutvideo.core.planner import (
    FMT_MP3, FMT_MP4, MODE_FAST, ExportSettings, estimate_bytes, needs_temp_dir, plan_export,
    uses_mp3_copy,
)
from simplecutvideo.core.segments import Range, build_pieces

FF = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FF is None, reason="ffmpeg non disponibile")


# ------------------------------------------------------------ analisi (senza ffmpeg)
def _mp3_probe(with_cover: bool = True) -> dict:
    streams = [{"index": 0, "codec_type": "audio", "codec_name": "mp3", "bit_rate": "192000",
                "channels": 2, "sample_rate": "44100"}]
    if with_cover:
        streams.append({"index": 1, "codec_type": "video", "codec_name": "png",
                        "disposition": {"attached_pic": 1}})
    return {"format": {"duration": "180.5", "size": "4332000", "bit_rate": "192000",
                       "format_name": "mp3"}, "streams": streams}


def test_parse_mp3_with_cover_is_audio_only():
    info = parse_probe("/m/song.mp3", _mp3_probe())
    assert info.audio_only and not info.has_video and info.v_index == -1
    assert info.has_audio and info.a_index == 0 and info.acodec == "mp3"
    assert info.duration_ms == 180_500 and info.a_bitrate == 192_000
    assert info.mp3_copy_ok and not info.video_copy_ok


def test_parse_audio_bitrate_from_format():
    data = _mp3_probe(with_cover=False)
    del data["streams"][0]["bit_rate"]
    assert parse_probe("/m/song.mp3", data).a_bitrate == 192_000


def test_parse_no_streams():
    with pytest.raises(ProbeError, match="no_video"):
        parse_probe("/m/x.bin", {"format": {"duration": "10"}, "streams": []})


def test_mp3_copy_settings_and_estimates():
    info = parse_probe("/m/song.mp3", _mp3_probe())
    copy = ExportSettings(fmt=FMT_MP3, mp3_copy=True, merge=True)
    reenc = ExportSettings(fmt=FMT_MP3, mp3_copy=False, mp3_bitrate=128, merge=True)
    assert uses_mp3_copy(info, copy) and not uses_mp3_copy(info, reenc)
    assert needs_temp_dir(info, copy) and not needs_temp_dir(info, reenc)
    assert not needs_temp_dir(info, ExportSettings(fmt=FMT_MP3, mp3_copy=True, merge=False))
    assert estimate_bytes(info, 10_000, copy) == 192_000 // 8 * 10 + 1024
    assert estimate_bytes(info, 10_000, reenc) == 128_000 // 8 * 10 + 1024


def test_mp3_copy_command():
    info = parse_probe("/m/song.mp3", _mp3_probe())
    pieces = build_pieces(info.duration_ms, [Range(10_000, 20_000)])
    jobs = plan_export("ffmpeg", info, [("/out/a.mp3", [pieces[1]])],
                       ExportSettings(fmt=FMT_MP3), None, False)
    args = jobs[0].steps[0].args
    assert args[args.index("-c:a") + 1] == "copy" and "libmp3lame" not in args
    assert args[args.index("-map") + 1] == "0:0"


def test_wav_is_reencoded():
    data = _mp3_probe(with_cover=False)
    data["streams"][0]["codec_name"] = "pcm_s16le"
    info = parse_probe("/m/rec.wav", data)
    assert not info.mp3_copy_ok
    assert not uses_mp3_copy(info, ExportSettings(fmt=FMT_MP3, mp3_copy=True))


def test_video_source_unchanged():
    # un video con audio MP3 continua a usare MP4 o la ricodifica MP3 come prima
    assert not needs_temp_dir(
        parse_probe("/m/v.mp4", {
            "format": {"duration": "10", "size": "1000"},
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}],
        }), ExportSettings(fmt=FMT_MP4, merge=True))
    assert needs_temp_dir(None, ExportSettings(fmt=FMT_MP4, mode=MODE_FAST, merge=True))


# ------------------------------------------------------------ esportazione reale
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture(scope="module")
def caps():
    return detect_capabilities(FF.ffmpeg)


@pytest.fixture(scope="module")
def mp3_with_cover(tmp_path_factory):
    folder = tmp_path_factory.mktemp("audio")
    plain = str(folder / "plain.mp3")
    cover = str(folder / "cover.png")
    song = str(folder / "brano d'esempio.mp3")
    run = [FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(run + ["-f", "lavfi", "-i", "sine=frequency=440:d=20:sample_rate=44100",
                          "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", plain], check=True)
    subprocess.run(run + ["-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-frames:v", "1",
                          cover], check=True)
    subprocess.run(run + ["-i", plain, "-i", cover, "-map", "0", "-map", "1", "-c", "copy",
                          "-id3v2_version", "3", "-disposition:v:0", "attached_pic",
                          "-metadata", "title=Prova", song], check=True)
    return song


def _stream(path):
    out = subprocess.run(
        [FF.ffprobe, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,bit_rate:format=duration", "-of", "json", path],
        capture_output=True, check=True).stdout
    data = json.loads(out)
    return data["streams"][0], float(data["format"]["duration"])


def _export(qapp, caps, info, outputs, settings, temp_dir=None):
    from PySide6.QtCore import QEventLoop
    from simplecutvideo.exporter import Exporter

    def plan(enc):
        return plan_export(FF.ffmpeg, info, outputs, settings, enc, caps.has_tonemap, temp_dir)

    result, loop = {}, QEventLoop()
    exp = Exporter(plan, caps.software, None, temp_dir)
    exp.finished.connect(lambda ok, c, created, d: (result.update(ok=ok, d=d), loop.quit()))
    exp.start()
    loop.exec()
    assert result["ok"], result["d"]


@needs_ffmpeg
def test_probe_real_mp3_with_cover(mp3_with_cover):
    info = probe(FF.ffprobe, mp3_with_cover)
    assert info.audio_only and info.acodec == "mp3" and abs(info.duration_ms - 20_000) < 100


@needs_ffmpeg
def test_mp3_copy_cut_and_merge(qapp, caps, mp3_with_cover, tmp_path):
    info = probe(FF.ffprobe, mp3_with_cover)
    pieces = build_pieces(info.duration_ms, [Range(2_000, 6_000)])

    single = str(tmp_path / "sel.mp3")
    _export(qapp, caps, info, [(single, [pieces[1]])], ExportSettings(fmt=FMT_MP3))
    stream, duration = _stream(single)
    assert stream["codec_name"] == "mp3" and abs(duration - 4.0) < 0.06
    assert abs(int(stream["bit_rate"]) - 192_000) < 2_000   # nessuna ricompressione

    temp_dir = tempfile.mkdtemp(dir=str(tmp_path))
    merged = str(tmp_path / "esterne.mp3")
    _export(qapp, caps, info, [(merged, [pieces[0], pieces[2]])],
            ExportSettings(fmt=FMT_MP3, merge=True), temp_dir)
    stream, duration = _stream(merged)
    assert stream["codec_name"] == "mp3" and abs(duration - 16.0) < 0.1
    assert not os.path.exists(temp_dir)


@needs_ffmpeg
def test_mp3_reencode_bitrate(qapp, caps, mp3_with_cover, tmp_path):
    info = probe(FF.ffprobe, mp3_with_cover)
    pieces = build_pieces(info.duration_ms, [Range(5_000, 15_000)])
    out = str(tmp_path / "128.mp3")
    _export(qapp, caps, info, [(out, [pieces[1]])],
            ExportSettings(fmt=FMT_MP3, mp3_copy=False, mp3_bitrate=128))
    stream, duration = _stream(out)
    assert abs(int(stream["bit_rate"]) - 128_000) < 2_000 and abs(duration - 10.0) < 0.1


@needs_ffmpeg
def test_wav_to_mp3(qapp, caps, tmp_path):
    wav = str(tmp_path / "rec.wav")
    subprocess.run([FF.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=d=8", wav], check=True)
    info = probe(FF.ffprobe, wav)
    assert info.audio_only and not info.mp3_copy_ok
    pieces = build_pieces(info.duration_ms, [Range(1_000, 4_000)])
    out = str(tmp_path / "rec.mp3")
    _export(qapp, caps, info, [(out, [pieces[1]])], ExportSettings(fmt=FMT_MP3))
    stream, duration = _stream(out)
    assert stream["codec_name"] == "mp3" and abs(duration - 3.0) < 0.1
