from __future__ import annotations

import asyncio
import logging
import os
import struct
from typing import Union
import weakref
from dataclasses import dataclass, replace

from google import genai
from livekit.agents import (APIConnectOptions, APIStatusError,
                           APITimeoutError, tokenize, tts, utils)
from livekit.agents.types import (DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN,
                                  NotGivenOr)
from livekit.agents.utils import is_given

logger = logging.getLogger(__name__)

# Default Gemini TTS configuration
DEFAULT_MODEL = "gemini-1.5-flash-latest"
DEFAULT_VOICE = "zephyr"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_NUM_CHANNELS = 1

@dataclass
class _GeminiTTSOptions:
    model: str
    voice: str
    temperature: float
    tokenizer: tokenize.SentenceTokenizer
    api_key: str | None

class TTS(tts.TTS):
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        temperature: float = 1.0,
        api_key: str | None = None,
        tokenizer: NotGivenOr[tokenize.SentenceTokenizer] = NOT_GIVEN,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=DEFAULT_NUM_CHANNELS,
        )

        self._client: genai.Client | None = None
        
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY must be set or api_key must be provided"
            )

        if not is_given(tokenizer):
            tokenizer = tokenize.blingfire.SentenceTokenizer()

        self._opts = _GeminiTTSOptions(
            model=model,
            voice=voice,
            temperature=temperature,
            tokenizer=tokenizer,
            api_key=api_key
        )
        # FIX 4: Use the directly imported BaseStream
        self._streams = weakref.WeakSet[Union[ChunkedStream, SynthesizeStream]]()

    def _ensure_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._opts.api_key)
        return self._client

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        stream = ChunkedStream(tts=self, input_text=text, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        stream = SynthesizeStream(tts=self, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        for stream in list(self._streams):
            await stream.aclose()
        self._streams.clear()


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        client = self._tts._ensure_client()
        
        # FIX 1: Use genai.* instead of types.* for all Gemini types
        config = genai.GenerateContentConfig(
            temperature=self._opts.temperature,
            response_modalities=["audio"],
            speech_config=genai.SpeechConfig(
                voice_config=genai.VoiceConfig(
                    prebuilt_voice_config=genai.PrebuiltVoiceConfig(
                        voice_name=self._opts.voice
                    )
                )
            ),
        )
        
        contents = [genai.Content(role="user", parts=[genai.Part.from_text(text=self._input_text)])]

        try:
            full_audio_data = bytearray()
            mime_type = ""
            
            stream_coro = client.models.generate_content_stream(
                model=self._opts.model,
                contents=contents,
                config=config,
            )

            async for chunk in await asyncio.wait_for(stream_coro, timeout=self._conn_options.timeout):
                if (
                    chunk.candidates
                    and chunk.candidates[0].content
                    and chunk.candidates[0].content.parts
                    and chunk.candidates[0].content.parts[0].inline_data
                ):
                    inline_data = chunk.candidates[0].content.parts[0].inline_data
                    if not mime_type:
                        mime_type = inline_data.mime_type
                    
                    full_audio_data.extend(inline_data.data)

            if not full_audio_data:
                logger.warning("Received no audio data from Gemini TTS")
                return

            wav_data = _convert_to_wav(bytes(full_audio_data), mime_type)
            
            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=DEFAULT_SAMPLE_RATE,
                num_channels=DEFAULT_NUM_CHANNELS,
                mime_type="audio/wav",
            )
            output_emitter.push(wav_data)

        except asyncio.TimeoutError:
            raise APITimeoutError("Gemini TTS timed out") from None
        except Exception as e:
            raise APIStatusError(f"Gemini TTS error: {e}") from e


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions):
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)
        self._sentence_ch = utils.aio.Chan[str]()

    async def _tokenize_input(self) -> None:
        tokenizer_stream = self._opts.tokenizer.stream()
        async for event in self._input_ch:
            if isinstance(event, str):
                tokenizer_stream.push_text(event)
            elif isinstance(event, self._FlushSentinel):
                tokenizer_stream.end_input()

            async for sentence in tokenizer_stream:
                await self._sentence_ch.send(sentence.token)
        
        self._sentence_ch.close()

    async def _run_synthesis(self, output_emitter: tts.AudioEmitter) -> None:
        client = self._tts._ensure_client()
        
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=DEFAULT_NUM_CHANNELS,
            mime_type="audio/pcm",
            stream=True,
        )

        # FIX 1: Use genai.* instead of types.* for all Gemini types
        config = genai.GenerateContentConfig(
            temperature=self._opts.temperature,
            response_modalities=["audio"],
            speech_config=genai.SpeechConfig(
                voice_config=genai.VoiceConfig(
                    prebuilt_voice_config=genai.PrebuiltVoiceConfig(
                        voice_name=self._opts.voice
                    )
                )
            ),
        )

        async for sentence in self._sentence_ch:
            self._mark_started()
            output_emitter.start_segment(segment_id=utils.shortuuid())
            
            contents = [genai.Content(role="user", parts=[genai.Part.from_text(text=sentence)])]
            
            try:
                stream_coro = client.models.generate_content_stream(
                    model=self._opts.model,
                    contents=contents,
                    config=config,
                )
                
                async for chunk in await asyncio.wait_for(stream_coro, timeout=self._conn_options.timeout):
                    if (
                        chunk.candidates
                        and chunk.candidates[0].content
                        and chunk.candidates[0].content.parts
                        and chunk.candidates[0].content.parts[0].inline_data
                        and chunk.candidates[0].content.parts[0].inline_data.data
                    ):
                        audio_data = chunk.candidates[0].content.parts[0].inline_data.data
                        output_emitter.push(audio_data)

            except asyncio.TimeoutError:
                logger.warning(f"Gemini TTS timed out for sentence: '{sentence}'")
            except Exception as e:
                logger.error(f"Error synthesizing sentence '{sentence}': {e}")
            finally:
                output_emitter.end_segment()

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        tasks = [
            asyncio.create_task(self._tokenize_input()),
            asyncio.create_task(self._run_synthesis(output_emitter)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            await utils.aio.cancel_and_wait(*tasks)

def _parse_audio_mime_type(mime_type: str) -> dict[str, int | None]:
    bits_per_sample = 16
    rate = DEFAULT_SAMPLE_RATE

    parts = mime_type.split(";")
    for param in parts:
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate = int(param.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif param.startswith("audio/L"):
            try:
                bits_per_sample = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass
    return {"bits_per_sample": bits_per_sample, "rate": rate}

def _convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    params = _parse_audio_mime_type(mime_type)
    bits_per_sample = params["bits_per_sample"] or 16
    sample_rate = params["rate"] or DEFAULT_SAMPLE_RATE
    num_channels = DEFAULT_NUM_CHANNELS
    data_size = len(audio_data)
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + audio_data