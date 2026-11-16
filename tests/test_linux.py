"""Test specifici per Linux: impostazioni XDG, ricerca di FFmpeg nell'AppImage, VA-API."""

import sys

import pytest

from simplecutvideo.core import ffmpeg as ff
from simplecutvideo.core.ffmpeg import EncoderInfo, parse_probe
from simplecutvideo.core.planner import ExportSettings, plan_export
from simplecutvideo.core.segments import Range, build_pieces

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="solo Linux")

VAAPI = EncoderInfo("h264_vaapi", "vaapi", "VA-API (Intel/AMD)", "/dev/dri/renderD128")


def _info(hdr: bool = False):
    data = {
        "format": {"duration": "60.0", "size": "60000000", "bit_rate": "8000000",
                   "format_name": "mov,mp4"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "hevc" if hdr else "h264",
             "width": 1920, "height": 1080, "avg_frame_rate": "30/1", "pix_fmt": "yuv420p",
             "bit_rate": "7800000", "color_transfer": "smpte2084" if hdr else "bt709"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "bit_rate": "192000",
             "channels": 2, "sample_rate": "48000"},
        ],
    }
    return parse_probe("/video/in.mp4", data)


def test_settings_in_xdg_config(monkeypatch, tmp_path):
    from simplecutvideo import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "apps" / "SimpleCutVideo.AppImage"))
    (tmp_path / "apps").mkdir()
    path = settings.settings_path()
    assert path == tmp_path / "cfg" / "SimpleCutVideo" / "SimpleCutVideo.ini"
    # nessun file creato accanto all'AppImage
    assert not (tmp_path / "apps" / "SimpleCutVideo.ini").exists()


def test_settings_portable_when_ini_exists(monkeypatch, tmp_path):
    from simplecutvideo import settings

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "SimpleCutVideo.AppImage"))
    portable = tmp_path / "SimpleCutVideo.ini"
    portable.write_text("")
    assert settings.settings_path() == portable


def test_find_ffmpeg_inside_appimage(monkeypatch, tmp_path):
    folder = tmp_path / "ffmpeg"
    folder.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (folder / name).write_text("")
    monkeypatch.delenv("SIMPLECUTVIDEO_FFMPEG", raising=False)
    monkeypatch.setenv("APPDIR", str(tmp_path))
    found = ff.find_ffmpeg()
    assert found is not None
    assert found.ffmpeg == str(folder / "ffmpeg")
    assert found.ffprobe == str(folder / "ffprobe")


def test_subprocess_env_restores_library_path(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    env = ff.subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def _args(info, pieces, encoder=VAAPI, has_tonemap=True):
    jobs = plan_export("ffmpeg", info, [("/out/x.mp4", pieces)], ExportSettings(), encoder,
                       has_tonemap)
    return jobs[0].steps[0].args


def test_vaapi_single_piece():
    info = _info()
    pieces = build_pieces(info.duration_ms, [Range(10_000, 20_000)])
    args = _args(info, [pieces[1]])
    assert args.index("-vaapi_device") < args.index("-i")
    assert args[args.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert args[args.index("-vf") + 1] == "format=nv12,hwupload"
    assert args[args.index("-c:v") + 1] == "h264_vaapi"
    assert "-pix_fmt" not in args


def test_vaapi_hdr_tonemap_before_upload():
    info = _info(hdr=True)
    pieces = build_pieces(info.duration_ms, [Range(10_000, 20_000)])
    args = _args(info, [pieces[1]])
    chain = args[args.index("-vf") + 1]
    assert chain.startswith("zscale=") and chain.endswith(",format=nv12,hwupload")


def test_vaapi_merge_uses_filter_graph():
    info = _info()
    pieces = build_pieces(info.duration_ms, [Range(10_000, 20_000)])
    args = _args(info, [pieces[0], pieces[2]])
    graph = args[args.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in graph and "[vc]format=nv12,hwupload[v]" in graph


def test_x264_unchanged():
    info = _info()
    pieces = build_pieces(info.duration_ms, [Range(10_000, 20_000)])
    args = _args(info, [pieces[1]], encoder=EncoderInfo(*ff.SW_ENCODER))
    assert "-vaapi_device" not in args
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


def test_audio_backend_fallback(tmp_path):
    from simplecutvideo.linux_integration import audio_backend_fallback

    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    assert audio_backend_fallback(env) == "pulseaudio"
    assert env["QT_AUDIO_BACKEND"] == "pulseaudio"

    (tmp_path / "pipewire-0").write_text("")
    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    assert audio_backend_fallback(env) is None and "QT_AUDIO_BACKEND" not in env

    env = {"XDG_RUNTIME_DIR": str(tmp_path / "x"), "QT_AUDIO_BACKEND": "pipewire"}
    assert audio_backend_fallback(env) is None and env["QT_AUDIO_BACKEND"] == "pipewire"
