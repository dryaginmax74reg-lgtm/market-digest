"""Голосовая сводка: текст (insights.voice_text) -> речь (edge-tts, женский голос ru-RU-SvetlanaNeural)
-> голосовое сообщение Telegram (OGG/Opus через ffmpeg; без ffmpeg — mp3 как аудио).
Любая ошибка — предупреждение в логе, дайджест уходит без голоса.

python voice.py "текст"     проверка: сохранить voice.ogg / voice.mp3 рядом со скриптом"""
import asyncio
import os
import shutil
import subprocess
import sys

from common import log

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = os.environ.get("VOICE_NAME", "ru-RU-SvetlanaNeural")
RATE = os.environ.get("VOICE_RATE", "+8%")          # чуть быстрее обычного — сводка на минуту


def make_voice(text, base=os.path.join(HERE, "voice")):
    """Путь к готовому файлу (.ogg или .mp3) или None."""
    if not text or os.environ.get("NO_VOICE") == "1":
        return None
    try:
        import edge_tts
    except ImportError:
        log("[warn] голос: пакет edge-tts не установлен")
        return None
    mp3 = base + ".mp3"
    try:
        async def run():
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(mp3)
        asyncio.run(asyncio.wait_for(run(), timeout=90))
    except Exception as ex:  # noqa: BLE001
        log(f"[warn] голос: синтез не удался: {type(ex).__name__}: {ex}")
        return None
    if not os.path.exists(mp3) or os.path.getsize(mp3) < 2000:
        log("[warn] голос: пустой файл")
        return None
    if shutil.which("ffmpeg"):
        ogg = base + ".ogg"
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-c:a", "libopus", "-b:a", "48k", "-ac", "1", ogg],
                           check=True, timeout=60)
            return ogg
        except Exception as ex:  # noqa: BLE001
            log(f"[warn] голос: ffmpeg: {ex} — отправлю mp3")
    return mp3


if __name__ == "__main__":
    print(make_voice(" ".join(sys.argv[1:]) or "Доброе утро. Проверка голосовой сводки."))
