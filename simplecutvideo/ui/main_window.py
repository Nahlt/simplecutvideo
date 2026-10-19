"""Finestra principale: lettore video, barra delle selezioni, comandi."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QSizePolicy, QSlider, QStackedWidget, QStyle, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QAbstractItemView,
)

from .. import APP_NAME, APP_VERSION
from ..core.ffmpeg import (
    Capabilities, FFmpegPaths, MediaInfo, ProbeError, detect_capabilities, probe, read_keyframes,
)
from ..core.planner import (
    DEFAULT_MP3_BITRATE, FMT_MP3, FMT_MP4, MODE_FAST, MODE_PRECISE, ExportSettings, human_size,
    plan_export,
)
from ..core.segments import SelectionModel, build_pieces
from ..core.timecode import format_ms
from ..exporter import Exporter
from ..i18n import LANGUAGES, i18n, tr
from ..settings import Settings
from .export_dialog import ExportDialog
from .range_bar import RangeBar
from .widgets import ClickSlider, TimeEdit

VIDEO_EXTENSIONS = (
    "mp4", "m4v", "mov", "mkv", "avi", "webm", "ts", "mts", "m2ts", "wmv", "mpg", "mpeg", "3gp",
)


class _Background(QObject):
    """Ponte per notificare al thread grafico la fine di un lavoro in background."""

    capsReady = Signal(object)
    keyframesReady = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, ffmpeg: FFmpegPaths | None):
        super().__init__()
        self.settings = settings
        self.ffmpeg = ffmpeg
        self.info: MediaInfo | None = None
        self.model = SelectionModel()
        self.caps: Capabilities | None = None
        self._caps_thread: threading.Thread | None = None
        self._keyframes: dict[str, list[float]] = {}
        self._keyframe_threads: dict[str, threading.Thread] = {}
        self._preview_end: int | None = None
        self._pos = 0   # posizione corrente (setPosition del player è asincrono)
        self._slider_dragging = False
        self._exporter: Exporter | None = None
        self._progress: QProgressDialog | None = None
        self._export_dialog: ExportDialog | None = None
        self._bg = _Background()
        self._bg.capsReady.connect(self._on_caps_ready)

        self.setAcceptDrops(True)
        self.setMinimumSize(920, 680)

        self._build_player()
        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
        i18n.changed.connect(self.retranslate)
        self.retranslate()
        self._update_enabled()

        geometry = settings.get_bytes("window/geometry")
        if isinstance(geometry, (QByteArray, bytes)) and geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1180, 820)

        if self.ffmpeg is not None:
            self._caps_thread = threading.Thread(target=self._detect_caps, daemon=True)
            self._caps_thread.start()

    # ================================================================ costruzione
    def _build_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(self.settings.get_int("player/volume", 80) / 100)
        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_player_error)

    def _button(self, icon: QStyle.StandardPixmap | None = None, text: str = "") -> QPushButton:
        btn = QPushButton(text)
        if icon is not None:
            btn.setIcon(self.style().standardIcon(icon))
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # --- area video (segnaposto finché non si apre un file)
        self.video_stack = QStackedWidget()
        self.video_stack.setStyleSheet("background: #0E0F11; border-radius: 6px;")
        placeholder = QWidget()
        pv = QVBoxLayout(placeholder)
        pv.addStretch()
        self.drop_label = QLabel()
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setStyleSheet("color: #9AA0A9; font-size: 16px;")
        pv.addWidget(self.drop_label)
        self.open_big = QPushButton()
        self.open_big.setObjectName("primary")
        self.open_big.clicked.connect(self.open_dialog)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.open_big)
        row.addStretch()
        pv.addLayout(row)
        pv.addStretch()
        self.video_stack.addWidget(placeholder)
        video_page = QWidget()
        vl = QVBoxLayout(video_page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.video_widget)
        self.preview_error = QLabel()
        self.preview_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_error.setWordWrap(True)
        self.preview_error.setStyleSheet("color: #E5534B; padding: 6px;")
        self.preview_error.hide()
        vl.addWidget(self.preview_error)
        self.video_stack.addWidget(video_page)
        root.addWidget(self.video_stack, 1)

        # --- barra di riproduzione
        self.play_slider = ClickSlider(Qt.Orientation.Horizontal)
        self.play_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_slider.sliderPressed.connect(self._slider_pressed)
        self.play_slider.sliderReleased.connect(self._slider_released)
        self.play_slider.sliderMoved.connect(self._slider_moved)
        root.addWidget(self.play_slider)

        controls = QHBoxLayout()
        sp = QStyle.StandardPixmap
        self.btn_back5 = self._button(sp.SP_MediaSeekBackward, "5s")
        self.btn_prev_frame = self._button(None, "◀|")
        self.btn_play = self._button(sp.SP_MediaPlay)
        self.btn_stop = self._button(sp.SP_MediaStop)
        self.btn_next_frame = self._button(None, "|▶")
        self.btn_fwd5 = self._button(sp.SP_MediaSeekForward, "5s")
        self.btn_play.setMinimumWidth(52)
        self.btn_back5.clicked.connect(lambda: self.seek_relative(-5000))
        self.btn_fwd5.clicked.connect(lambda: self.seek_relative(5000))
        self.btn_prev_frame.clicked.connect(lambda: self.step_frame(-1))
        self.btn_next_frame.clicked.connect(lambda: self.step_frame(1))
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_stop.clicked.connect(self.stop)
        for b in (self.btn_back5, self.btn_prev_frame, self.btn_play, self.btn_stop,
                  self.btn_next_frame, self.btn_fwd5):
            controls.addWidget(b)
        controls.addSpacing(12)
        self.time_label = QLabel()
        self.time_label.setObjectName("timeLabel")
        controls.addWidget(self.time_label)
        controls.addStretch()
        self.volume_icon = QLabel()
        self.volume_icon.setPixmap(self.style().standardIcon(sp.SP_MediaVolume).pixmap(18, 18))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(110)
        self.volume.setValue(int(self.audio.volume() * 100))
        self.volume.valueChanged.connect(self._on_volume)
        controls.addWidget(self.volume_icon)
        controls.addWidget(self.volume)
        root.addLayout(controls)

        # --- selezioni
        self.sel_group = QGroupBox()
        sg = QVBoxLayout(self.sel_group)
        bounds = QHBoxLayout()
        self.file_start_label = QLabel()
        self.file_end_label = QLabel()
        for lbl in (self.file_start_label, self.file_end_label):
            lbl.setObjectName("hint")
        bounds.addWidget(self.file_start_label)
        bounds.addStretch()
        self.bar_hint = QLabel()
        self.bar_hint.setObjectName("hint")
        bounds.addWidget(self.bar_hint)
        bounds.addStretch()
        bounds.addWidget(self.file_end_label)
        sg.addLayout(bounds)

        self.range_bar = RangeBar(self.model)
        self.range_bar.seekRequested.connect(self._on_bar_seek)
        self.range_bar.rangesChanged.connect(self._on_ranges_changed)
        self.range_bar.activeChanged.connect(self._on_active_changed)
        self.range_bar.addRequested.connect(self._on_bar_add)
        sg.addWidget(self.range_bar)

        bottom = QHBoxLayout()
        bottom.setSpacing(18)

        editor = QGridLayout()
        editor.setHorizontalSpacing(8)
        self.active_title = QLabel()
        self.active_title.setObjectName("sectionTitle")
        editor.addWidget(self.active_title, 0, 0, 1, 4)
        self.lbl_start = QLabel()
        self.start_edit = TimeEdit()
        self.start_edit.msEdited.connect(self._on_start_edited)
        self.btn_set_start = self._button()
        self.btn_set_start.clicked.connect(self.set_start_from_position)
        self.btn_go_start = self._button(None, "↦")
        self.btn_go_start.setFixedWidth(36)
        self.btn_go_start.clicked.connect(lambda: self._go_to_active("start"))
        editor.addWidget(self.lbl_start, 1, 0)
        editor.addWidget(self.start_edit, 1, 1)
        editor.addWidget(self.btn_set_start, 1, 2)
        editor.addWidget(self.btn_go_start, 1, 3)
        self.lbl_end = QLabel()
        self.end_edit = TimeEdit()
        self.end_edit.msEdited.connect(self._on_end_edited)
        self.btn_set_end = self._button()
        self.btn_set_end.clicked.connect(self.set_end_from_position)
        self.btn_go_end = self._button(None, "↦")
        self.btn_go_end.setFixedWidth(36)
        self.btn_go_end.clicked.connect(lambda: self._go_to_active("end"))
        editor.addWidget(self.lbl_end, 2, 0)
        editor.addWidget(self.end_edit, 2, 1)
        editor.addWidget(self.btn_set_end, 2, 2)
        editor.addWidget(self.btn_go_end, 2, 3)
        self.lbl_plus = QLabel()
        self.seconds_spin = QDoubleSpinBox()
        self.seconds_spin.setRange(0.1, 86_400)
        self.seconds_spin.setDecimals(3)
        self.seconds_spin.setValue(30)
        self.seconds_spin.setSuffix(" s")
        self.seconds_spin.setFixedWidth(self.start_edit.width())
        self.btn_apply_seconds = self._button()
        self.btn_apply_seconds.clicked.connect(self.apply_seconds)
        editor.addWidget(self.lbl_plus, 3, 0)
        editor.addWidget(self.seconds_spin, 3, 1)
        editor.addWidget(self.btn_apply_seconds, 3, 2)
        self.duration_label = QLabel()
        editor.addWidget(self.duration_label, 4, 0, 1, 4)
        bottom.addLayout(editor)

        actions = QVBoxLayout()
        self.btn_add = self._button()
        self.btn_add.clicked.connect(self.add_selection)
        self.btn_remove = self._button()
        self.btn_remove.clicked.connect(self.remove_selection)
        self.btn_preview = self._button(QStyle.StandardPixmap.SP_MediaPlay)
        self.btn_preview.clicked.connect(self.preview_selection)
        for b in (self.btn_add, self.btn_remove, self.btn_preview):
            b.setStyleSheet("text-align: left; padding-left: 10px;")
            actions.addWidget(b)
        actions.addStretch()
        bottom.addLayout(actions)

        lst = QVBoxLayout()
        self.list_title = QLabel()
        self.list_title.setObjectName("hint")
        self.sel_list = QListWidget()
        self.sel_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sel_list.currentRowChanged.connect(self._on_list_row)
        self.sel_list.setMinimumWidth(300)
        self.sel_list.setMaximumHeight(150)
        lst.addWidget(self.list_title)
        lst.addWidget(self.sel_list)
        bottom.addLayout(lst, 1)
        sg.addLayout(bottom)
        root.addWidget(self.sel_group)

        # --- riga finale
        footer = QHBoxLayout()
        self.total_label = QLabel()
        footer.addWidget(self.total_label)
        footer.addStretch()
        self.btn_export = QPushButton()
        self.btn_export.setObjectName("primary")
        self.btn_export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export.clicked.connect(self.open_export)
        footer.addWidget(self.btn_export)
        root.addLayout(footer)

        self.setCentralWidget(central)
        self.status_info = QLabel()
        self.statusBar().addPermanentWidget(self.status_info)

    def _build_menu(self) -> None:
        bar = self.menuBar()
        self.menu_file = bar.addMenu("")
        self.act_open = QAction(self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_dialog)
        self.act_export = QAction(self)
        self.act_export.setShortcut(QKeySequence("Ctrl+E"))
        self.act_export.triggered.connect(self.open_export)
        self.act_quit = QAction(self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)
        self.menu_file.addAction(self.act_open)
        self.menu_file.addAction(self.act_export)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_quit)

        self.menu_lang = bar.addMenu("")
        group = QActionGroup(self)
        group.setExclusive(True)
        self.lang_actions: dict[str, QAction] = {}
        for code, name in LANGUAGES:
            act = QAction(name, self, checkable=True)
            act.triggered.connect(lambda _=False, c=code: self.set_language(c))
            group.addAction(act)
            self.menu_lang.addAction(act)
            self.lang_actions[code] = act

        self.menu_help = bar.addMenu("")
        self.act_shortcuts = QAction(self)
        self.act_shortcuts.setShortcut(QKeySequence("F1"))
        self.act_shortcuts.triggered.connect(self.show_shortcuts)
        self.act_about = QAction(self)
        self.act_about.triggered.connect(self.show_about)
        self.menu_help.addAction(self.act_shortcuts)
        self.menu_help.addAction(self.act_about)

    def _build_shortcuts(self) -> None:
        def sc(keys, handler):
            for key in keys:
                s = QShortcut(QKeySequence(key), self)
                s.activated.connect(handler)

        sc(["Space"], self.toggle_play)
        sc(["Left"], lambda: self.seek_relative(-5000))
        sc(["Right"], lambda: self.seek_relative(5000))
        sc(["Shift+Left", ","], lambda: self.step_frame(-1))
        sc(["Shift+Right", "."], lambda: self.step_frame(1))
        sc(["Home"], lambda: self.seek(0))
        sc(["End"], lambda: self.seek(self.model.duration))
        sc(["I"], self.set_start_from_position)
        sc(["O"], self.set_end_from_position)
        sc(["P"], self.preview_selection)
        sc(["N"], self.add_selection)
        sc(["Delete"], self.remove_selection)
        sc(["PgDown"], lambda: self._cycle_active(1))
        sc(["PgUp"], lambda: self._cycle_active(-1))

    # ================================================================ testi
    def retranslate(self) -> None:
        self.menu_file.setTitle(tr("menu_file"))
        self.act_open.setText(tr("act_open"))
        self.act_export.setText(tr("act_export"))
        self.act_quit.setText(tr("act_quit"))
        self.menu_lang.setTitle(tr("menu_language"))
        self.menu_help.setTitle(tr("menu_help"))
        self.act_shortcuts.setText(tr("act_shortcuts"))
        self.act_about.setText(tr("act_about", app=APP_NAME))
        for code, act in self.lang_actions.items():
            act.setChecked(code == i18n.language)

        self.drop_label.setText(tr("drop_hint"))
        self.open_big.setText(tr("btn_open"))
        self.btn_back5.setToolTip(tr("tip_back5"))
        self.btn_fwd5.setToolTip(tr("tip_fwd5"))
        self.btn_prev_frame.setToolTip(tr("tip_prev_frame"))
        self.btn_next_frame.setToolTip(tr("tip_next_frame"))
        self.btn_play.setToolTip(tr("tip_play"))
        self.btn_stop.setToolTip(tr("tip_stop"))
        self.volume.setToolTip(tr("tip_volume"))

        self.sel_group.setTitle(tr("grp_selections"))
        self.bar_hint.setText(tr("bar_hint"))
        self.lbl_start.setText(tr("lbl_start"))
        self.lbl_end.setText(tr("lbl_end"))
        self.btn_set_start.setText(tr("btn_set_start"))
        self.btn_set_end.setText(tr("btn_set_end"))
        self.btn_go_start.setToolTip(tr("tip_go_start"))
        self.btn_go_end.setToolTip(tr("tip_go_end"))
        self.lbl_plus.setText(tr("lbl_end_plus"))
        self.btn_apply_seconds.setText(tr("btn_apply"))
        self.btn_add.setText("＋  " + tr("btn_add_sel"))
        self.btn_remove.setText("－  " + tr("btn_remove_sel"))
        self.btn_preview.setText(tr("btn_preview"))
        self.list_title.setText(tr("list_title"))
        self.btn_export.setText(tr("btn_export_main"))
        self.preview_error.setText(tr("preview_error"))
        self._refresh_selection_ui()
        self._update_time_label(self._pos)
        self._update_status_info()
        self._update_title()

    def set_language(self, code: str) -> None:
        i18n.set_language(code)
        self.settings.set("ui/language", code)

    # ================================================================ apertura
    def open_dialog(self) -> None:
        if self._exporter is not None:
            return
        start_dir = self.settings.get_str("paths/last_open", str(Path.home()))
        patterns = " ".join(f"*.{e}" for e in VIDEO_EXTENSIONS)
        filters = f"{tr('filter_video')} ({patterns});;MP4 (*.mp4);;{tr('filter_all')} (*)"
        path, _ = QFileDialog.getOpenFileName(self, tr("act_open"), start_dir, filters)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        if self._exporter is not None:
            return
        if self.ffmpeg is None:
            QMessageBox.critical(self, APP_NAME, tr("err_no_ffmpeg"))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = probe(self.ffmpeg.ffprobe, path)
        except ProbeError as exc:
            QApplication.restoreOverrideCursor()
            key = {"no_video": "err_no_video", "no_duration": "err_no_duration",
                   "timeout": "err_probe_timeout"}.get(str(exc))
            message = tr(key) if key else tr("err_open", error=str(exc)[:500])
            QMessageBox.warning(self, APP_NAME, f"{os.path.basename(path)}\n\n{message}")
            return
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP_NAME, tr("err_open", error=str(exc)))
            return
        QApplication.restoreOverrideCursor()

        self.settings.set("paths/last_open", os.path.dirname(path))
        self.info = info
        self._preview_end = None
        self.model.reset(info.duration_ms)
        self.play_slider.setRange(0, info.duration_ms)
        self.play_slider.setValue(0)
        self._pos = 0
        self.range_bar.set_position(0)
        self.preview_error.hide()
        self.video_stack.setCurrentIndex(1)
        self.player.setSource(QUrl.fromLocalFile(path))
        self._start_keyframes(path)
        self._refresh_selection_ui()
        self._update_time_label(0)
        self._update_status_info()
        self._update_title()
        self._update_enabled()

    def _start_keyframes(self, path: str) -> None:
        if path in self._keyframes or path in self._keyframe_threads or not self.info:
            return
        if not self.info.video_copy_ok:
            return

        def work():
            self._keyframes[path] = read_keyframes(self.ffmpeg.ffprobe, path)

        t = threading.Thread(target=work, daemon=True)
        self._keyframe_threads[path] = t
        t.start()

    def _get_keyframes(self) -> list[float] | None:
        if self.info is None:
            return None
        path = self.info.path
        if path not in self._keyframe_threads:
            self._start_keyframes(path)
        t = self._keyframe_threads.get(path)
        if t is not None:
            t.join()
        return self._keyframes.get(path)

    # ---------------------------------------------------------- drag & drop
    @staticmethod
    def _dropped_path(event) -> str | None:
        urls = event.mimeData().urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = urls[0].toLocalFile()
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        return path if ext in VIDEO_EXTENSIONS else None

    def dragEnterEvent(self, event):
        if self._exporter is None and self._dropped_path(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self._dropped_path(event)
        if path:
            event.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.open_file(path))

    # ================================================================ lettore
    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.player.pause()   # mostra il primo fotogramma
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._preview_end = None

    def _on_player_error(self, error, message: str = "") -> None:
        if error != QMediaPlayer.Error.NoError and self.info is not None:
            self.preview_error.show()
            self.statusBar().showMessage(tr("preview_error"), 8000)

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        icon = (QStyle.StandardPixmap.SP_MediaPause
                if state == QMediaPlayer.PlaybackState.PlayingState
                else QStyle.StandardPixmap.SP_MediaPlay)
        self.btn_play.setIcon(self.style().standardIcon(icon))

    def _on_position(self, ms: int) -> None:
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            # In pausa la posizione cambia solo per nostre richieste (già applicate
            # in seek); eventuali notifiche ritardate del player vanno ignorate.
            return
        self._pos = ms
        if self._preview_end is not None and ms >= self._preview_end:
            self._preview_end = None
            self.player.pause()
        if not self._slider_dragging:
            self.play_slider.setValue(ms)
        self.range_bar.set_position(ms)
        self._update_time_label(ms)

    def _update_time_label(self, ms: int) -> None:
        total = self.model.duration
        self.time_label.setText(f"{format_ms(ms)} / {format_ms(total)}")

    def _on_volume(self, value: int) -> None:
        self.audio.setVolume(value / 100)
        self.settings.set("player/volume", value)

    def _slider_pressed(self) -> None:
        self._slider_dragging = True

    def _slider_released(self) -> None:
        self._slider_dragging = False
        self.seek(self.play_slider.value())

    def _slider_moved(self, value: int) -> None:
        self.seek(value)

    def has_media(self) -> bool:
        return self.info is not None

    def toggle_play(self) -> None:
        if not self.has_media():
            return
        self._preview_end = None
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self._pos >= self.model.duration - 50:
                self.seek(0)
            self.player.play()

    def stop(self) -> None:
        if not self.has_media():
            return
        self._preview_end = None
        self.player.pause()
        self.seek(0)

    def seek(self, ms: int) -> None:
        if not self.has_media():
            return
        ms = max(0, min(int(ms), self.model.duration))
        self._pos = ms
        self.player.setPosition(ms)
        self.range_bar.set_position(ms)
        if not self._slider_dragging:
            self.play_slider.setValue(ms)
        self._update_time_label(ms)

    def seek_relative(self, delta: int) -> None:
        if self.has_media():
            self._preview_end = None
            self.seek(self._pos + delta)

    def step_frame(self, direction: int) -> None:
        if not self.has_media():
            return
        self._preview_end = None
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        step = self.info.frame_ms
        self.seek(int(round(self._pos + direction * step)))

    def preview_selection(self) -> None:
        r = self.model.active_range()
        if not self.has_media() or r is None:
            return
        self.seek(r.start)
        self._preview_end = r.end
        self.player.play()

    def _go_to_active(self, edge: str) -> None:
        r = self.model.active_range()
        if r is not None:
            self._preview_end = None
            self.player.pause()
            self.seek(r.start if edge == "start" else max(r.start, r.end - int(self.info.frame_ms)))

    # ================================================================ selezioni
    def _on_bar_seek(self, ms: int) -> None:
        self._preview_end = None
        self.seek(ms)

    def _on_bar_add(self, ms: int) -> None:
        self._add_at(ms)

    def _on_ranges_changed(self) -> None:
        self._refresh_selection_ui()

    def _on_active_changed(self) -> None:
        self._refresh_selection_ui()

    def _on_list_row(self, row: int) -> None:
        if row >= 0 and row != self.model.active:
            self.model.set_active(row)
            self._refresh_selection_ui()
            r = self.model.active_range()
            if r is not None:
                self.seek(r.start)

    def _cycle_active(self, delta: int) -> None:
        if not self.model.ranges:
            return
        current = self.model.active if self.model.active is not None else 0
        self.model.set_active((current + delta) % len(self.model.ranges))
        self._refresh_selection_ui()
        self.seek(self.model.active_range().start)

    def add_selection(self) -> None:
        if self.has_media():
            self._add_at(self._pos)

    def _add_at(self, ms: int) -> None:
        if self.model.add_at(ms) is None:
            self.statusBar().showMessage(tr("msg_no_room"), 5000)
        self._refresh_selection_ui()

    def remove_selection(self) -> None:
        if self.model.active is None:
            return
        self.model.remove(self.model.active)
        self._refresh_selection_ui()

    def _ensure_active(self) -> int | None:
        """Indice della selezione attiva; se non ce ne sono ne crea una qui."""
        if self.model.active is not None:
            return self.model.active
        if not self.has_media():
            return None
        return self.model.add_at(self._pos)

    def set_start_from_position(self) -> None:
        index = self._ensure_active()
        if index is None:
            return
        if self.model.index_at(self._pos) not in (None, index):
            self.statusBar().showMessage(tr("msg_overlap"), 5000)
        self.model.place_start(index, self._pos)
        self._refresh_selection_ui()

    def set_end_from_position(self) -> None:
        index = self._ensure_active()
        if index is None:
            return
        self.model.place_end(index, self._pos)
        self._refresh_selection_ui()

    def _on_start_edited(self, ms: int) -> None:
        if self.model.active is None:
            return
        applied = self.model.place_start(self.model.active, ms)
        if applied != ms:
            self.statusBar().showMessage(tr("msg_clamped", value=format_ms(applied)), 6000)
        self.start_edit.force_ms(applied)
        self._refresh_selection_ui()
        self.seek(applied)

    def _on_end_edited(self, ms: int) -> None:
        if self.model.active is None:
            return
        applied = self.model.place_end(self.model.active, ms)
        if applied != ms:
            self.statusBar().showMessage(tr("msg_clamped", value=format_ms(applied)), 6000)
        self.end_edit.force_ms(applied)
        self._refresh_selection_ui()
        self.seek(applied)

    def apply_seconds(self) -> None:
        r = self.model.active_range()
        if r is None:
            return
        wanted = r.start + int(round(self.seconds_spin.value() * 1000))
        applied = self.model.set_end(self.model.active, wanted)
        if applied != wanted:
            self.statusBar().showMessage(tr("msg_clamped", value=format_ms(applied)), 6000)
        self._refresh_selection_ui()

    def _refresh_selection_ui(self) -> None:
        r = self.model.active_range()
        n = len(self.model.ranges)
        self.file_start_label.setText(tr("file_start", t=format_ms(0)))
        self.file_end_label.setText(tr("file_end", t=format_ms(self.model.duration)))
        if r is None:
            self.active_title.setText(tr("no_selection"))
            self.start_edit.force_ms(0)
            self.end_edit.force_ms(0)
            self.duration_label.setText("")
        else:
            self.active_title.setText(tr("active_title", n=self.model.active + 1, total=n))
            self.start_edit.set_ms(r.start)
            self.end_edit.set_ms(r.end)
            self.duration_label.setText(tr("sel_duration", t=format_ms(r.length)))

        self.sel_list.blockSignals(True)
        self.sel_list.clear()
        for i, rng in enumerate(self.model.ranges):
            text = f"{i + 1}.  {format_ms(rng.start)}  →  {format_ms(rng.end)}   ({format_ms(rng.length)})"
            self.sel_list.addItem(QListWidgetItem(text))
        if self.model.active is not None:
            self.sel_list.setCurrentRow(self.model.active)
        self.sel_list.blockSignals(False)

        if n:
            self.total_label.setText(tr("total_selected", t=format_ms(self.model.total_selected()), n=n))
        else:
            self.total_label.setText(tr("total_none") if self.has_media() else "")
        self.range_bar.update()
        self._update_enabled()

    def _update_enabled(self) -> None:
        media = self.has_media()
        has_active = self.model.active_range() is not None
        busy = self._exporter is not None
        for w in (self.play_slider, self.btn_back5, self.btn_prev_frame, self.btn_play,
                  self.btn_stop, self.btn_next_frame, self.btn_fwd5, self.btn_add):
            w.setEnabled(media)
        self.sel_group.setEnabled(media)
        for w in (self.start_edit, self.end_edit, self.btn_set_start, self.btn_set_end,
                  self.seconds_spin, self.btn_apply_seconds, self.btn_remove, self.btn_preview,
                  self.btn_go_start, self.btn_go_end):
            w.setEnabled(media and has_active)
        can_export = media and bool(self.model.ranges) and self.ffmpeg is not None and not busy
        self.btn_export.setEnabled(can_export)
        self.act_export.setEnabled(can_export)
        self.act_open.setEnabled(not busy)

    def _update_status_info(self) -> None:
        info = self.info
        if info is None:
            self.status_info.setText("" if self.ffmpeg else tr("err_no_ffmpeg_short"))
            return
        w, h = info.display_size
        audio = (info.acodec or "").upper() if info.has_audio else tr("no_audio")
        parts = [f"{w}×{h}", f"{info.fps:.2f} fps", f"{info.vcodec.upper()} / {audio}",
                 human_size(info.size_bytes)]
        if info.is_hdr:
            parts.append("HDR")
        self.status_info.setText("   ·   ".join(parts))

    def _update_title(self) -> None:
        if self.info:
            self.setWindowTitle(f"{APP_NAME} — {os.path.basename(self.info.path)}")
        else:
            self.setWindowTitle(APP_NAME)

    # ================================================================ esportazione
    def _detect_caps(self) -> None:
        caps = detect_capabilities(self.ffmpeg.ffmpeg)
        self._bg.capsReady.emit(caps)

    def _on_caps_ready(self, caps: Capabilities) -> None:
        self.caps = caps
        if self._export_dialog is not None:
            self._export_dialog.set_capabilities(caps)

    def _prefs(self) -> ExportSettings:
        s = self.settings
        return ExportSettings(
            fmt=s.get_str("export/format", FMT_MP4),
            mode=s.get_str("export/mode", MODE_PRECISE),
            mp3_bitrate=s.get_int("export/mp3_bitrate", DEFAULT_MP3_BITRATE),
            use_gpu=s.get_bool("export/use_gpu", True),
            merge=s.get_bool("export/merge", False),
        )

    def _save_prefs(self, p: ExportSettings) -> None:
        self.settings.set("export/format", p.fmt)
        self.settings.set("export/mode", p.mode)
        self.settings.set("export/mp3_bitrate", p.mp3_bitrate)
        self.settings.set("export/use_gpu", p.use_gpu)
        self.settings.set("export/merge", p.merge)

    def open_export(self) -> None:
        if not self.btn_export.isEnabled() or self.info is None:
            return
        self.player.pause()
        self._preview_end = None
        pieces = build_pieces(self.model.duration, self.model.ranges)
        dialog = ExportDialog(
            self.info, pieces, self._prefs(), os.path.dirname(self.info.path), self.caps,
            self._get_keyframes, self,
        )
        self._export_dialog = dialog
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._export_dialog = None
        if not accepted:
            return
        self._save_prefs(dialog.settings)
        self._start_export(dialog.outputs, dialog.settings, dialog.keyframes())

    def _start_export(self, outputs, settings: ExportSettings, keyframes) -> None:
        if self.caps is None and self._caps_thread is not None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._caps_thread.join()
            QApplication.restoreOverrideCursor()
            QApplication.processEvents()
        caps = self.caps or detect_capabilities(self.ffmpeg.ffmpeg)
        self.caps = caps

        if settings.fmt == FMT_MP3 and not caps.has_mp3:
            QMessageBox.critical(self, APP_NAME, tr("err_no_mp3"))
            return

        temp_dir = None
        if settings.fmt == FMT_MP4 and settings.mode == MODE_FAST and settings.merge:
            folder = os.path.dirname(outputs[0][0])
            try:
                temp_dir = tempfile.mkdtemp(prefix=".simplecutvideo_", dir=folder)
            except OSError:
                temp_dir = tempfile.mkdtemp(prefix="simplecutvideo_")

        info = self.info
        ffmpeg = self.ffmpeg.ffmpeg

        def plan(encoder):
            return plan_export(ffmpeg, info, outputs, settings, encoder, caps.has_tonemap,
                               temp_dir, keyframes)

        encoder = caps.best(settings.use_gpu)
        uses_encoder = settings.fmt == FMT_MP4 and settings.mode == MODE_PRECISE
        fallback = caps.software if (uses_encoder and encoder.is_hardware) else None

        self._exporter = Exporter(plan, encoder, fallback, temp_dir, self)
        self._progress = QProgressDialog(tr("progress_start"), tr("btn_cancel"), 0, 1000, self)
        self._progress.setWindowTitle(tr("progress_title"))
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        self._progress.setMinimumDuration(0)
        self._progress.setMinimumWidth(460)
        self._progress.canceled.connect(self._exporter.cancel)
        self._exporter.progress.connect(self._progress.setValue)
        self._exporter.job_started.connect(self._on_job_started)
        self._exporter.notice.connect(lambda key: self.statusBar().showMessage(tr(key), 10000))
        self._exporter.finished.connect(self._on_export_finished)
        self._update_enabled()
        self._progress.show()
        self._exporter.start()

    def _on_job_started(self, index: int, total: int, name: str) -> None:
        if self._progress is not None:
            self._progress.setLabelText(tr("progress_label", i=index, n=total, name=name))

    def _on_export_finished(self, ok: bool, cancelled: bool, created: list, details: str) -> None:
        if self._progress is not None:
            self._progress.canceled.disconnect()
            self._progress.close()
            self._progress.deleteLater()
            self._progress = None
        self._exporter.deleteLater()
        self._exporter = None
        self._update_enabled()

        if ok:
            self._show_done(created)
        elif cancelled:
            self.statusBar().showMessage(tr("msg_cancelled", n=len(created)), 8000)
        else:
            box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME, tr("err_export"), parent=self)
            if created:
                box.setInformativeText(tr("err_export_partial", n=len(created)))
            box.setDetailedText(details or "-")
            box.exec()

    def _show_done(self, created: list[str]) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(APP_NAME)
        box.setText(tr("done_text", n=len(created)))
        box.setInformativeText("\n".join(os.path.basename(p) for p in created[:12]))
        open_btn = box.addButton(tr("btn_open_folder"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("btn_close"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn and created:
            self._reveal(created[0])

    @staticmethod
    def _reveal(path: str) -> None:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ================================================================ aiuto
    def show_shortcuts(self) -> None:
        rows = [
            ("Ctrl+O", "sc_open"), ("Ctrl+E", "sc_export"), ("Space", "sc_play"),
            ("← / →", "sc_seek5"), ("Shift+← / Shift+→   , / .", "sc_frame"),
            ("Home / End", "sc_home_end"), ("I", "sc_set_start"), ("O", "sc_set_end"),
            ("P", "sc_preview"), ("N", "sc_new"), (tr("key_delete"), "sc_remove"),
            ("PgUp / PgDown", "sc_cycle"), ("F1", "sc_help"),
        ]
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("act_shortcuts"))
        v = QVBoxLayout(dlg)
        table = QTableWidget(len(rows), 2)
        table.setHorizontalHeaderLabels([tr("col_key"), tr("col_action")])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for i, (key, desc) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(key))
            table.setItem(i, 1, QTableWidgetItem(tr(desc)))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setMinimumSize(560, 420)
        v.addWidget(table)
        hint = QLabel(tr("sc_mouse_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        v.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        dlg.exec()

    def show_about(self) -> None:
        from PySide6 import __version__ as pyside_version
        QMessageBox.about(
            self, tr("act_about", app=APP_NAME),
            tr("about_text", app=APP_NAME, version=APP_VERSION, qt=pyside_version),
        )

    # ================================================================ chiusura
    def closeEvent(self, event):
        if self._exporter is not None:
            answer = QMessageBox.question(self, APP_NAME, tr("confirm_quit_export"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._exporter.cancel()
        self.player.stop()
        self.settings.set("window/geometry", self.saveGeometry())
        self.settings.sync()
        super().closeEvent(event)
