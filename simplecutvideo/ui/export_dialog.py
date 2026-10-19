"""Finestra di esportazione: parti, unione, formato, modalità di taglio, destinazione."""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.ffmpeg import Capabilities, MediaInfo
from ..core.planner import (
    DEFAULT_MP3_BITRATE, FMT_MP3, FMT_MP4, MODE_FAST, MODE_PRECISE, MP3_BITRATES, ExportSettings,
    estimate_bytes, human_size, snap_start,
)
from ..core.segments import Piece, ensure_extension, merged_filename, piece_filename
from ..core.timecode import format_ms
from ..i18n import tr

COL_CHECK, COL_TYPE, COL_START, COL_END, COL_DUR, COL_SIZE, COL_NAME = range(7)


class ExportDialog(QDialog):
    def __init__(
        self,
        info: MediaInfo,
        pieces: list[Piece],
        prefs: ExportSettings,
        default_dir: str,
        caps: Capabilities | None,
        keyframes_provider: Callable[[], list[float] | None],
        parent=None,
    ):
        super().__init__(parent)
        self.info = info
        self.pieces = pieces
        self.caps = caps
        self._keyframes_provider = keyframes_provider
        self._keyframes: list[float] | None = None
        self.stem = os.path.splitext(os.path.basename(info.path))[0]
        self._names = {p.number: piece_filename(self.stem, p, prefs.fmt) for p in pieces}
        self._updating = False

        self.outputs: list[tuple[str, list[Piece]]] = []
        self.settings = ExportSettings()

        self.setWindowTitle(tr("export_title"))
        self.setMinimumWidth(860)
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_parts_group())
        layout.addWidget(self._build_format_group())
        layout.addWidget(self._build_destination_group(default_dir))

        self.summary = QLabel()
        self.summary.setObjectName("sectionTitle")
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox()
        self.export_btn = buttons.addButton(tr("btn_export"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.export_btn.setObjectName("primary")
        buttons.addButton(tr("btn_cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_prefs(prefs)
        self._refresh()

    # ------------------------------------------------------------ costruzione
    def _build_parts_group(self) -> QGroupBox:
        box = QGroupBox(tr("grp_parts"))
        v = QVBoxLayout(box)

        quick = QHBoxLayout()
        quick.addWidget(QLabel(tr("quick_select")))
        for key, mode in (("qs_selected", "sel"), ("qs_outside", "out"),
                          ("qs_all", "all"), ("qs_none", "none")):
            btn = QPushButton(tr(key))
            btn.clicked.connect(lambda _=False, m=mode: self._quick_select(m))
            quick.addWidget(btn)
        quick.addStretch()
        v.addLayout(quick)

        self.table = QTableWidget(len(self.pieces), 7)
        self.table.setHorizontalHeaderLabels([
            tr("col_part"), tr("col_type"), tr("col_start"), tr("col_end"),
            tr("col_duration"), tr("col_size"), tr("col_name"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        header = self.table.horizontalHeader()
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)

        for row, piece in enumerate(self.pieces):
            check = QTableWidgetItem(str(piece.number))
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if piece.selected else Qt.CheckState.Unchecked)
            self.table.setItem(row, COL_CHECK, check)
            kind = tr("type_selection", n=piece.sel_number) if piece.selected else tr("type_outside")
            for col, text in ((COL_TYPE, kind), (COL_END, format_ms(piece.end))):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, col, item)
            for col in (COL_START, COL_DUR, COL_SIZE):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, col, item)
            name = QTableWidgetItem(self._names[piece.number])
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
            name.setToolTip(tr("name_tooltip"))
            self.table.setItem(row, COL_NAME, name)
        self.table.itemChanged.connect(self._on_item_changed)
        rows_h = self.table.verticalHeader().defaultSectionSize() * min(len(self.pieces), 8)
        self.table.setMinimumHeight(rows_h + self.table.horizontalHeader().sizeHint().height() + 12)
        v.addWidget(self.table)

        merge_row = QHBoxLayout()
        self.merge_check = QCheckBox(tr("merge_check"))
        self.merge_check.setToolTip(tr("merge_tooltip"))
        self.merge_check.toggled.connect(self._refresh)
        self.merge_name = QLineEdit(merged_filename(self.stem, FMT_MP4))
        merge_row.addWidget(self.merge_check)
        merge_row.addWidget(QLabel(tr("merge_name")))
        merge_row.addWidget(self.merge_name, 1)
        v.addLayout(merge_row)
        return box

    def _build_format_group(self) -> QGroupBox:
        box = QGroupBox(tr("grp_format"))
        grid = QGridLayout(box)

        self.fmt_mp4 = QRadioButton(tr("fmt_mp4"))
        self.fmt_mp3 = QRadioButton(tr("fmt_mp3"))
        self.fmt_group = QButtonGroup(self)
        self.fmt_group.addButton(self.fmt_mp4)
        self.fmt_group.addButton(self.fmt_mp3)
        if not self.info.has_audio:
            self.fmt_mp3.setEnabled(False)
            self.fmt_mp3.setToolTip(tr("mp3_no_audio"))
        self.fmt_mp4.toggled.connect(self._on_format_changed)
        grid.addWidget(self.fmt_mp4, 0, 0)
        grid.addWidget(self.fmt_mp3, 1, 0)

        self.options = QStackedWidget()
        # --- opzioni MP4
        mp4 = QWidget()
        mv = QVBoxLayout(mp4)
        mv.setContentsMargins(12, 0, 0, 0)
        modes = QHBoxLayout()
        self.mode_precise = QRadioButton(tr("mode_precise"))
        self.mode_fast = QRadioButton(tr("mode_fast"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_precise)
        self.mode_group.addButton(self.mode_fast)
        self.mode_precise.toggled.connect(self._on_mode_changed)
        modes.addWidget(QLabel(tr("cut_mode")))
        modes.addWidget(self.mode_precise)
        modes.addWidget(self.mode_fast)
        modes.addStretch()
        mv.addLayout(modes)
        self.mode_help = QLabel()
        self.mode_help.setObjectName("hint")
        self.mode_help.setWordWrap(True)
        mv.addWidget(self.mode_help)
        gpu_row = QHBoxLayout()
        self.gpu_check = QCheckBox(tr("use_gpu"))
        self.gpu_check.toggled.connect(self._refresh)
        self.encoder_label = QLabel()
        self.encoder_label.setObjectName("hint")
        gpu_row.addWidget(self.gpu_check)
        gpu_row.addWidget(self.encoder_label)
        gpu_row.addStretch()
        mv.addLayout(gpu_row)
        if not self.info.video_copy_ok:
            self.mode_fast.setEnabled(False)
            self.mode_fast.setToolTip(tr("fast_unavailable", codec=self.info.vcodec))
        self.options.addWidget(mp4)

        # --- opzioni MP3
        mp3 = QWidget()
        h = QHBoxLayout(mp3)
        h.setContentsMargins(12, 0, 0, 0)
        h.addWidget(QLabel(tr("mp3_bitrate")))
        self.bitrate = QComboBox()
        for kbps in MP3_BITRATES:
            self.bitrate.addItem(f"{kbps} kbps", kbps)
        self.bitrate.currentIndexChanged.connect(self._refresh)
        h.addWidget(self.bitrate)
        hint = QLabel(tr("mp3_hint"))
        hint.setObjectName("hint")
        h.addWidget(hint, 1)
        self.options.addWidget(mp3)

        grid.addWidget(self.options, 0, 1, 2, 1)
        grid.setColumnStretch(1, 1)
        return box

    def _build_destination_group(self, default_dir: str) -> QGroupBox:
        box = QGroupBox(tr("grp_destination"))
        h = QHBoxLayout(box)
        self.dest = QLineEdit(default_dir)
        browse = QPushButton(tr("btn_browse"))
        browse.clicked.connect(self._browse)
        h.addWidget(self.dest, 1)
        h.addWidget(browse)
        return box

    # ------------------------------------------------------------- stato
    def _apply_prefs(self, prefs: ExportSettings) -> None:
        self._updating = True
        use_mp3 = prefs.fmt == FMT_MP3 and self.info.has_audio
        (self.fmt_mp3 if use_mp3 else self.fmt_mp4).setChecked(True)
        fast = prefs.mode == MODE_FAST and self.info.video_copy_ok
        (self.mode_fast if fast else self.mode_precise).setChecked(True)
        self.gpu_check.setChecked(prefs.use_gpu)
        self.merge_check.setChecked(prefs.merge)
        idx = self.bitrate.findData(prefs.mp3_bitrate)
        self.bitrate.setCurrentIndex(idx if idx >= 0 else self.bitrate.findData(DEFAULT_MP3_BITRATE))
        self._updating = False
        self._on_format_changed()

    def current_settings(self) -> ExportSettings:
        return ExportSettings(
            fmt=FMT_MP3 if self.fmt_mp3.isChecked() else FMT_MP4,
            mode=MODE_FAST if self.mode_fast.isChecked() else MODE_PRECISE,
            mp3_bitrate=int(self.bitrate.currentData() or DEFAULT_MP3_BITRATE),
            use_gpu=self.gpu_check.isChecked(),
            merge=self.merge_check.isChecked(),
        )

    def set_capabilities(self, caps: Capabilities) -> None:
        self.caps = caps
        self._refresh()

    def keyframes(self) -> list[float] | None:
        return self._keyframes

    def _checked_pieces(self) -> list[Piece]:
        return [
            p for row, p in enumerate(self.pieces)
            if self.table.item(row, COL_CHECK).checkState() == Qt.CheckState.Checked
        ]

    def _is_fast(self) -> bool:
        return self.fmt_mp4.isChecked() and self.mode_fast.isChecked()

    def _effective(self, piece: Piece) -> tuple[int, int]:
        """Inizio effettivo e durata (ms) considerando l'aggancio ai keyframe."""
        if self._is_fast() and self._keyframes:
            start = int(round(snap_start(piece, self._keyframes) * 1000))
            return start, piece.end - start
        return piece.start, piece.length

    # ------------------------------------------------------------- eventi
    def _quick_select(self, mode: str) -> None:
        self._updating = True
        for row, piece in enumerate(self.pieces):
            on = mode == "all" or (mode == "sel" and piece.selected) or (
                mode == "out" and not piece.selected)
            self.table.item(row, COL_CHECK).setCheckState(
                Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            )
        self._updating = False
        self._refresh()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        if item.column() == COL_NAME:
            piece = self.pieces[item.row()]
            self._names[piece.number] = item.text().strip()
        self._refresh()

    def _on_format_changed(self) -> None:
        if self._updating:
            return
        ext = FMT_MP3 if self.fmt_mp3.isChecked() else FMT_MP4
        self.options.setCurrentIndex(1 if ext == FMT_MP3 else 0)
        for number, name in self._names.items():
            self._names[number] = ensure_extension(name, ext) if name else name
        self.merge_name.setText(ensure_extension(self.merge_name.text() or self.stem, ext))
        self._load_keyframes_if_needed()
        self._refresh()

    def _on_mode_changed(self) -> None:
        if self._updating:
            return
        self._load_keyframes_if_needed()
        self._refresh()

    def _load_keyframes_if_needed(self) -> None:
        if self._is_fast() and self._keyframes is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._keyframes = self._keyframes_provider() or []
            finally:
                QApplication.restoreOverrideCursor()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("choose_folder"), self.dest.text())
        if folder:
            self.dest.setText(folder)

    # ------------------------------------------------------------ refresh
    def _refresh(self) -> None:
        if self._updating:
            return
        self._updating = True
        settings = self.current_settings()
        fast = self._is_fast()
        merge = settings.merge

        total_bytes = 0
        checked = self._checked_pieces()
        for row, piece in enumerate(self.pieces):
            start, duration = self._effective(piece)
            start_item = self.table.item(row, COL_START)
            if fast and start != piece.start:
                start_item.setText("≈ " + format_ms(start))
                start_item.setToolTip(tr("start_snapped", requested=format_ms(piece.start)))
            else:
                start_item.setText(format_ms(start))
                start_item.setToolTip("")
            self.table.item(row, COL_DUR).setText(format_ms(duration))
            size = estimate_bytes(self.info, duration, settings)
            self.table.item(row, COL_SIZE).setText(("≈ " if fast else "") + human_size(size))
            if piece in checked:
                total_bytes += size
            name_item = self.table.item(row, COL_NAME)
            if name_item.text() != self._names[piece.number]:
                name_item.setText(self._names[piece.number])
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable if not merge
                else Qt.ItemFlag.NoItemFlags
            )
        self.merge_name.setEnabled(merge)

        # testo di aiuto sulla modalità
        if settings.fmt == FMT_MP4:
            if fast:
                text = tr("mode_fast_help")
                if not self.info.audio_copy_ok:
                    text += " " + tr("fast_audio_note", codec=self.info.acodec or "?")
            else:
                text = tr("mode_precise_help")
                if self.info.is_hdr:
                    note_key = "hdr_note" if (self.caps is None or self.caps.has_tonemap) else "hdr_no_tonemap"
                    text += " " + tr(note_key)
            if not self.info.video_copy_ok:
                text += " " + tr("fast_unavailable", codec=self.info.vcodec)
            self.mode_help.setText(text)
            self.gpu_check.setEnabled(not fast)
            if fast:
                self.encoder_label.setText(tr("encoder_none"))
            elif self.caps is None:
                self.encoder_label.setText(tr("encoder_detecting"))
            else:
                self.encoder_label.setText(
                    tr("encoder_label", name=self.caps.best(settings.use_gpu).label)
                )

        n_files = (1 if checked else 0) if merge else len(checked)
        self.summary.setText(tr(
            "summary", files=n_files, size=human_size(total_bytes),
            source=human_size(self.info.size_bytes),
        ))
        self.export_btn.setEnabled(bool(checked))
        self._updating = False

    # ------------------------------------------------------------ conferma
    def _on_accept(self) -> None:
        settings = self.current_settings()
        checked = self._checked_pieces()
        if not checked:
            QMessageBox.warning(self, tr("export_title"), tr("err_no_parts"))
            return
        folder = self.dest.text().strip()
        if not folder:
            QMessageBox.warning(self, tr("export_title"), tr("err_no_folder"))
            return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, tr("export_title"), tr("err_folder", error=str(exc)))
            return

        ext = settings.fmt
        if settings.merge:
            name = ensure_extension(self.merge_name.text().strip() or self.stem, ext)
            outputs = [(os.path.join(folder, name), checked)]
        else:
            outputs = []
            for piece in checked:
                raw = self._names[piece.number].strip() or piece_filename(self.stem, piece, ext)
                outputs.append((os.path.join(folder, ensure_extension(raw, ext)), [piece]))

        paths = [os.path.normcase(os.path.abspath(p)) for p, _ in outputs]
        if len(set(paths)) != len(paths):
            QMessageBox.warning(self, tr("export_title"), tr("err_duplicate_names"))
            return
        source = os.path.normcase(os.path.abspath(self.info.path))
        if source in paths:
            QMessageBox.warning(self, tr("export_title"), tr("err_same_as_source"))
            return

        existing = [p for p, _ in outputs if os.path.exists(p)]
        if existing:
            listing = "\n".join(os.path.basename(p) for p in existing[:10])
            if len(existing) > 10:
                listing += "\n…"
            answer = QMessageBox.question(
                self, tr("overwrite_title"), tr("overwrite_text", files=listing),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.outputs = outputs
        self.settings = settings
        if settings.fmt == FMT_MP4 and settings.mode == MODE_FAST:
            self._load_keyframes_if_needed()
        self.accept()
