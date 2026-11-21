"""Opt-in local voice I/O for institute sessions."""
import asyncio
import importlib.util
import re
import wave
from io import BytesIO
from pathlib import Path
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from .auth.api import institute_user
from .campus import sessions
from .models import Conversation, Message

router = APIRouter(prefix="/v1/voice")
FORMATS = {"audio/webm":"matroska", "audio/ogg":"ogg", "audio/mp4":"mov"}
MAX_AUDIO = 5 * 1024 * 1024
RATE = 16_000


class Voice:
    def __init__(self, config):
        self.config = config
        self.slot = asyncio.Semaphore(1)
        self.whisper = self.piper = None

    def capabilities(self):
        if not self.config.voice_enabled:
            return {"transcription":False,"playback":False}
        path = Path(self.config.voice_piper_model)
        return {
            "transcription":importlib.util.find_spec("faster_whisper") is not None,
            "playback":importlib.util.find_spec("piper") is not None and path.is_file()
                       and Path(str(path)+".json").is_file(),
        }

    def transcribe(self, data, format):
        import av
        import numpy as np
        from faster_whisper import WhisperModel

        try:
            buffers, count = [], 0
            with av.open(BytesIO(data), format=format) as container:
                if not container.streams.audio:
                    raise ValueError("Recording contains no audio")
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        count += converted.samples
                        if count > RATE * 32:
                            raise ValueError("Keep recordings under 30 seconds")
                        buffers.append(converted.to_ndarray().reshape(-1))
            if count < RATE // 4:
                raise ValueError("Recording is too short")
        except av.error.FFmpegError as exc:
            raise ValueError("Could not decode this recording") from exc
        if self.whisper is None:
            self.whisper = WhisperModel(self.config.voice_whisper_model, device="cpu", compute_type="int8",
                                        cpu_threads=2, download_root=self.config.voice_model_cache)
        samples = np.concatenate(buffers).astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(samples, language="en", beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()[:2000]

    def speak(self, text):
        from piper import PiperVoice
        if self.piper is None:
            self.piper = PiperVoice.load(self.config.voice_piper_model)
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            self.piper.synthesize_wav(text, wav)
        return output.getvalue()

    async def run(self, fn, *args):
        try:
            await asyncio.wait_for(self.slot.acquire(), 0.15)
        except TimeoutError:
            raise HTTPException(429, "Voice service is busy. Try again shortly.")
        try:
            return await to_thread.run_sync(fn, *args)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "Voice model is unavailable") from exc
        finally:
            self.slot.release()


@router.get("/status")
async def status(request: Request, user=Depends(institute_user)):
    return request.app.state.voice.capabilities()


@router.post("/transcribe")
async def transcribe(request: Request, user=Depends(institute_user)):
    voice = request.app.state.voice
    if not voice.capabilities()["transcription"]:
        raise HTTPException(503, "Transcription is disabled")
    kind = request.headers.get("content-type","").split(";")[0].strip().lower()
    if kind not in FORMATS:
        raise HTTPException(415, "Use a WebM, Ogg or MP4 audio recording")
    data = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_AUDIO:
                    raise HTTPException(413, "Recording exceeds 5 MiB")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Recording upload timed out")
    result = await voice.run(voice.transcribe, bytes(data), FORMATS[kind])
    if not result:
        raise HTTPException(422, "No speech detected")
    return {"text":result}


@router.post("/speak/{id}")
async def speak(id: UUID, request: Request, user=Depends(institute_user)):
    voice = request.app.state.voice
    if not voice.capabilities()["playback"]:
        raise HTTPException(503, "Playback is disabled")
    async with sessions(request)() as db:
        text = await db.scalar(select(Message.content).join(Conversation).where(
            Message.id == str(id), Conversation.user_id == user.id, Message.role == "assistant"))
    if text is None:
        raise HTTPException(404, "Answer not found")
    text = re.sub(r"\[[^\]\n]+\]\([^\n]*\)", "", text)
    text = re.sub(r"\\([^\w\s])", r"\1", text)
    text = re.sub(r"^\s*(?:>\s*|#+\s*|\*\*)", "", text, flags=re.M).replace("**","").strip()
    if len(text) > 2400:
        text = text[:2400].rsplit(" ", 1)[0] + ". Playback shortened. See the full answer in chat."
    if not text:
        raise HTTPException(422, "No text to read")
    return Response(await voice.run(voice.speak, text), media_type="audio/wav", headers={"Cache-Control":"no-store"})
