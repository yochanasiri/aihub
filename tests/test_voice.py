import io
import math
import shutil
import subprocess
import wave
from types import SimpleNamespace

import httpx
import pytest

from backend.app.main import create_app
from backend.app.models import Conversation, Message
from backend.app.voice import Voice
from test_rag import rig


def wave_data(frames=8000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 16000, frames, "NONE", "not compressed"))
        pcm = b"".join(int(6000*math.sin(2*math.pi*220*i/16000)).to_bytes(2,"little",signed=True)
                       for i in range(frames))
        wav.writeframes(pcm)
    return output.getvalue()


class VoiceFixture(Voice):
    def capabilities(self):
        return {"transcription":True, "playback":True}

    def transcribe(self, data, format):
        assert format == "matroska" and data == b"fake recording"
        return "How can I apply?"

    def speak(self, text):
        assert text == "Applications are open."
        return wave_data()


async def test_voice_requires_institute_auth_and_owns_playback(rig):
    app = create_app(rig.config, rig.service, rig.auth)
    app.state.voice = VoiceFixture(rig.config)
    student = {"Authorization":f"Bearer {rig.session_token}"}
    user = await rig.auth.authenticate(rig.session_token)
    async with rig.sessions() as db:
        chat = Conversation(user_id=user.id, title="Voice fixture")
        db.add(chat)
        await db.flush()
        answer = Message(conversation_id=chat.id, role="assistant", content="> Applications are open.\n\n[S1](https://iiti.ac.in/)", sources=[])
        db.add(answer)
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        assert (await client.get("/v1/voice/status")).status_code == 401
        assert (await client.post("/v1/voice/transcribe",headers={**student,"Content-Type":"text/plain"},content=b"fake recording")).status_code == 415
        assert (await client.post("/v1/voice/transcribe",headers={**student,"Content-Type":"audio/webm"},
                                  content=b"fake recording")).json() == {"text":"How can I apply?"}
        assert (await client.post("/v1/voice/speak/11111111-1111-4111-8111-111111111111",headers=student)).status_code == 404
        response = await client.post("/v1/voice/speak/"+answer.id,headers=student)
        assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(response.content)) as wav:
            assert wav.getframerate() == 16000


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fixture generator unavailable")
def test_webm_decoder_supplies_bounded_mono_pcm():
    pytest.importorskip("faster_whisper")
    from backend.app.config import Settings
    data = subprocess.run(["ffmpeg","-loglevel","error","-i","pipe:0","-f","webm","-c:a","libopus","pipe:1"],
                          input=wave_data(),capture_output=True,check=True,timeout=10).stdout
    voice = Voice(Settings(_env_file=None))
    class WhisperFixture:
        def transcribe(self, samples, **options):
            assert samples.ndim == 1 and samples.dtype.name == "float32"
            assert 6000 <= len(samples) <= 10000 and options["vad_filter"]
            return [SimpleNamespace(text="Sample speech")], None
    voice.whisper = WhisperFixture()
    assert voice.transcribe(data,"matroska") == "Sample speech"
    with pytest.raises(ValueError, match="Could not decode"):
        voice.transcribe(b"not an audio recording","matroska")
