"""Esecuzione dei lavori FFmpeg con avanzamento, annullamento e ripiego su CPU."""

from __future__ import annotations

import os
import shutil
from collections import deque
from typing import Callable

from PySide6.QtCore import QObject, QProcess, Signal

from .core.ffmpeg import EncoderInfo
from .core.planner import Job


class Exporter(QObject):
    progress = Signal(int)                  # 0..1000 (per mille)
    job_started = Signal(int, int, str)     # indice (1-based), totale, nome file
    notice = Signal(str)                    # chiave di traduzione di un avviso
    finished = Signal(bool, bool, list, str)  # ok, annullato, file creati, dettagli errore

    def __init__(
        self,
        plan: Callable[[EncoderInfo], list[Job]],
        encoder: EncoderInfo,
        fallback: EncoderInfo | None,
        temp_dir: str | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._plan = plan
        self._encoder = encoder
        self._fallback = fallback
        self._temp_dir = temp_dir
        self._jobs: list[Job] = []
        self._job_index = 0
        self._step_index = 0
        self._done_weight = 0
        self._total_weight = 1
        self._created: list[str] = []
        self._stderr: deque[str] = deque(maxlen=60)
        self._stdout_buffer = b""
        self._stderr_buffer = b""
        self._cancelled = False
        self._finished = False
        self._proc: QProcess | None = None

    # ------------------------------------------------------------ pubblico
    def start(self) -> None:
        self._jobs = self._plan(self._encoder)
        self._total_weight = max(1, sum(j.weight_ms for j in self._jobs))
        self._start_job()

    def cancel(self) -> None:
        if self._finished:
            return
        self._cancelled = True
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            proc = self._proc
            proc.kill()
            # attende la chiusura così il file parziale viene rimosso subito
            # (anche se l'applicazione si sta chiudendo)
            proc.waitForFinished(3000)
        else:
            self._finish(False)

    @property
    def encoder(self) -> EncoderInfo:
        return self._encoder

    # ------------------------------------------------------------- interno
    def _start_job(self) -> None:
        if self._job_index >= len(self._jobs):
            self._finish(True)
            return
        job = self._jobs[self._job_index]
        self.job_started.emit(self._job_index + 1, len(self._jobs), os.path.basename(job.output))
        try:
            for path, content in job.prepare_files.items():
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
        except OSError as exc:
            self._stderr.append(str(exc))
            self._fail()
            return
        self._step_index = 0
        self._start_step()

    def _start_step(self) -> None:
        job = self._jobs[self._job_index]
        step = job.steps[self._step_index]
        self._stdout_buffer = b""
        self._stderr_buffer = b""
        proc = QProcess(self)
        proc.setProgram(step.args[0])
        proc.setArguments(step.args[1:])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start()

    def _current_step_weight(self) -> int:
        return self._jobs[self._job_index].steps[self._step_index].weight_ms

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        self._stdout_buffer += bytes(self._proc.readAllStandardOutput())
        *lines, self._stdout_buffer = self._stdout_buffer.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us="):
                value = line.split("=", 1)[1]
                if value.lstrip("-").isdigit():
                    done_ms = max(0, int(value) // 1000)
                    current = min(done_ms, self._current_step_weight())
                    self._emit_progress(self._done_weight + current)

    def _on_stderr(self) -> None:
        if self._proc is None:
            return
        self._stderr_buffer += bytes(self._proc.readAllStandardError())
        *lines, self._stderr_buffer = self._stderr_buffer.split(b"\n")
        for raw in lines:
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                self._stderr.append(text)

    def _emit_progress(self, weight: int) -> None:
        self.progress.emit(int(1000 * min(weight, self._total_weight) / self._total_weight))

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._stderr.append("ffmpeg: failed to start")
            self._proc = None
            if self._cancelled:
                self._finish(False)
            else:
                self._fail()

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._on_stdout()
        self._on_stderr()
        if self._stderr_buffer:
            self._stderr.append(self._stderr_buffer.decode("utf-8", "replace"))
        self._proc = None
        if self._cancelled:
            self._cleanup_job(self._jobs[self._job_index], remove_output=True)
            self._finish(False)
            return
        ok = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        if not ok:
            self._fail()
            return
        self._done_weight += self._current_step_weight()
        self._emit_progress(self._done_weight)
        job = self._jobs[self._job_index]
        self._step_index += 1
        if self._step_index < len(job.steps):
            self._start_step()
            return
        self._cleanup_job(job, remove_output=False)
        self._created.append(job.output)
        self._job_index += 1
        self._start_job()

    def _fail(self) -> None:
        job = self._jobs[self._job_index] if self._job_index < len(self._jobs) else None
        if job is not None:
            self._cleanup_job(job, remove_output=True)
        if job is not None and self._fallback is not None and self._encoder.is_hardware:
            # Ripiego: ripete il lavoro con l'encoder software
            self._encoder = self._fallback
            self._fallback = None
            self.notice.emit("notice_gpu_fallback")
            self._jobs = self._plan(self._encoder)
            self._done_weight = sum(j.weight_ms for j in self._jobs[: self._job_index])
            self._stderr.clear()
            self._start_job()
            return
        self._finish(False, error=True)

    def _cleanup_job(self, job: Job, remove_output: bool) -> None:
        for path in job.temp_files:
            try:
                os.remove(path)
            except OSError:
                pass
        if remove_output:
            try:
                os.remove(job.output)
            except OSError:
                pass

    def _finish(self, ok: bool, error: bool = False) -> None:
        if self._finished:
            return
        self._finished = True
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        details = "\n".join(self._stderr) if error else ""
        self.finished.emit(ok, self._cancelled, list(self._created), details)
