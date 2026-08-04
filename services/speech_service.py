from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class SpeechRecognitionError(RuntimeError):
    """Ошибка распознавания аудио."""


class SpeechService:
    def __init__(self) -> None:
        # Модель загружается один раз при запуске приложения,
        # а не заново для каждого голосового.
        self._model = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8",
        )

        # Чтобы несколько голосовых одновременно не забили весь CPU.
        self._semaphore = asyncio.Semaphore(1)

    async def transcribe(
        self,
        file_path: str | Path,
    ) -> str:
        path = Path(file_path)

        if not path.exists():
            raise SpeechRecognitionError(
                f"Аудиофайл не найден: {path}"
            )

        async with self._semaphore:
            try:
                return await asyncio.to_thread(
                    self._transcribe_sync,
                    path,
                )
            except Exception as exc:
                raise SpeechRecognitionError(
                    "Не удалось распознать голосовое сообщение"
                ) from exc

    def _transcribe_sync(self, file_path: Path) -> str:
        segments, info = self._model.transcribe(
            str(file_path),
            language="ru",
            vad_filter=True,
            beam_size=5,
        )

        # segments — генератор. Реальное распознавание начинается
        # при его переборе.
        parts = [
            segment.text.strip()
            for segment in segments
            if segment.text.strip()
        ]

        text = " ".join(parts).strip()

        logger.info(
            "Голос распознан: language=%s, probability=%.2f, text=%r",
            info.language,
            info.language_probability,
            text,
        )

        if not text:
            raise SpeechRecognitionError(
                "В голосовом сообщении не удалось распознать речь"
            )

        return text


speech_service = SpeechService()