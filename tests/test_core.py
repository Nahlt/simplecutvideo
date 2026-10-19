from simplecutvideo.core.ffmpeg import parse_probe
from simplecutvideo.core.planner import (
    ExportSettings, FMT_MP3, MODE_FAST, estimate_bytes, human_size, snap_start,
    video_target_bitrate,
)
from simplecutvideo.core.segments import (
    MIN_RANGE_MS, Range, SelectionModel, build_pieces, ensure_extension, piece_filename,
    sanitize_filename,
)
from simplecutvideo.core.timecode import format_compact, format_ms, parse_time


# ------------------------------------------------------------ timecode
def test_format_and_parse_roundtrip():
    for ms in (0, 999, 61_001, 3_723_004):
        assert parse_time(format_ms(ms)) == ms


def test_parse_variants():
    assert parse_time("90") == 90_000
    assert parse_time("90,5") == 90_500
    assert parse_time("1:30") == 90_000
    assert parse_time("1:30.250") == 90_250
    assert parse_time("01:02:03.004") == 3_723_004
    assert parse_time("1:75") is None
    assert parse_time("abc") is None
    assert parse_time("") is None


def test_format_compact():
    assert format_compact(90_000) == "01m30s"
    assert format_compact(3_723_000) == "1h02m03s"


# ------------------------------------------------------------ selezioni
def test_single_selection_gives_three_pieces():
    pieces = build_pieces(60_000, [Range(10_000, 20_000)])
    assert [(p.start, p.end, p.selected) for p in pieces] == [
        (0, 10_000, False), (10_000, 20_000, True), (20_000, 60_000, False)
    ]
    assert [p.number for p in pieces] == [1, 2, 3]


def test_selection_from_zero_gives_two_pieces():
    pieces = build_pieces(60_000, [Range(0, 20_000)])
    assert len(pieces) == 2 and pieces[0].selected and pieces[0].sel_number == 1


def test_multiple_selections():
    pieces = build_pieces(100_000, [Range(50_000, 60_000), Range(10_000, 20_000)])
    assert [(p.start, p.selected) for p in pieces] == [
        (0, False), (10_000, True), (20_000, False), (50_000, True), (60_000, False)
    ]
    assert [p.sel_number for p in pieces if p.selected] == [1, 2]


def test_model_constraints_no_overlap():
    m = SelectionModel()
    m.reset(100_000, full_range=False)
    a = m.add_at(10_000, 10_000)
    b = m.add_at(40_000, 10_000)
    assert (a, b) == (0, 1)
    # la fine della prima non può superare l'inizio della seconda
    assert m.set_end(0, 90_000) == 40_000
    # l'inizio della seconda non può scendere sotto la fine della prima
    assert m.set_start(1, 0) == 40_000
    # durata minima
    assert m.set_start(0, 39_990) == 40_000 - MIN_RANGE_MS
    # aggiunta dentro una selezione esistente
    assert m.add_at(45_000) is None


def test_model_add_limited_by_next():
    m = SelectionModel()
    m.reset(100_000, full_range=False)
    m.add_at(20_000, 10_000)
    idx = m.add_at(15_000, 10_000)
    assert m.ranges[idx].start == 15_000 and m.ranges[idx].end == 20_000
    assert [r.start for r in m.ranges] == [15_000, 20_000]


def test_model_move_keeps_length():
    m = SelectionModel()
    m.reset(100_000, full_range=False)
    m.add_at(10_000, 10_000)
    assert m.move(0, 95_000) == 90_000
    assert m.ranges[0].length == 10_000


def test_model_reset_full_range():
    m = SelectionModel()
    m.reset(30_000)
    assert len(m.ranges) == 1 and m.ranges[0].start == 0 and m.ranges[0].end == 30_000


# ------------------------------------------------------------ nomi file
def test_filenames():
    piece = build_pieces(60_000, [Range(10_000, 20_000)])[1]
    assert piece_filename("video", piece, "mp4") == "video_02_00m10s-00m20s.mp4"
    assert sanitize_filename('a:b?c*"') == "a_b_c__"
    assert sanitize_filename("con.mp4").startswith("_")
    assert ensure_extension("prova.mp4", "mp3") == "prova.mp3"
    assert ensure_extension("prova", "mp4") == "prova.mp4"
    assert ensure_extension("v1.2 finale", "mp4") == "v1.2 finale.mp4"


# ------------------------------------------------------------ probe e stime
SAMPLE_PROBE = {
    "format": {"duration": "120.0", "size": "60000000", "bit_rate": "4000000",
               "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
         "avg_frame_rate": "30000/1001", "bit_rate": "3800000", "pix_fmt": "yuv420p10le",
         "color_transfer": "arib-std-b67",
         "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]},
        {"codec_type": "audio", "codec_name": "aac", "bit_rate": "192000", "channels": 2},
    ],
}


def test_parse_probe():
    info = parse_probe("x.mov", SAMPLE_PROBE)
    assert info.duration_ms == 120_000
    assert abs(info.fps - 29.97) < 0.01
    assert info.is_hdr and info.rotation == 270
    assert info.display_size == (1080, 1920)
    assert info.video_copy_ok and info.audio_copy_ok
    assert video_target_bitrate(info) == int(3_800_000 * 1.5)


def test_parse_probe_skips_cover_art_and_estimates_bitrate():
    data = {
        "format": {"duration": "10", "size": "5000000"},
        "streams": [
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"codec_type": "video", "codec_name": "vp8", "width": 640, "height": 360,
             "avg_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "vorbis"},
        ],
    }
    info = parse_probe("x.webm", data)
    assert info.vcodec == "vp8" and not info.video_copy_ok and not info.audio_copy_ok
    assert info.v_bitrate > 0


def test_estimates():
    info = parse_probe("x.mov", SAMPLE_PROBE)
    mp3 = ExportSettings(fmt=FMT_MP3, mp3_bitrate=192)
    assert abs(estimate_bytes(info, 60_000, mp3) - 192_000 / 8 * 60) < 2048
    fast = ExportSettings(mode=MODE_FAST)
    assert estimate_bytes(info, 60_000, fast) == 30_000_000
    assert human_size(30_000_000) == "28.6 MB"


def test_snap_start_nearest_keyframe():
    kf = [0.0, 5.0, 10.0, 15.0]
    p = build_pieces(20_000, [Range(6_000, 12_000)])[1]
    assert snap_start(p, kf) == 5.0
    p = build_pieces(20_000, [Range(9_000, 12_000)])[1]
    assert snap_start(p, kf) == 10.0
    # il keyframe successivo coinciderebbe con la fine: si usa il precedente
    p = build_pieces(20_000, [Range(9_000, 10_050)])[1]
    assert snap_start(p, kf) == 5.0
    assert snap_start(p, None) == 9.0
