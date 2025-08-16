import logging
import os
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    metrics,
    RoomInputOptions,
)
from livekit.plugins import (
    openai,
    noise_cancellation,
    silero,
)
from gemini_tts import TTS as GeminiTTS
from livekit.plugins.turn_detector.multilingual import MultilingualModel


logger = logging.getLogger("voice-agent")
load_dotenv()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""
You are a Autom8 an AI voice assistant talking to users through voice only.
Speak naturally, like a real person. No lists, no summaries, no robotic tone.
Keep replies short, clear, and conversational. Just talk—dont write.
Avoid punctuation thats hard to speak or sounds unnatural.
""",
            stt=openai.STT(
                detect_language=True,
                model=os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
            ),
            llm=openai.LLM(
                model="Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
                base_url=os.environ["TOGETHERAI_BASE_URL"],
                api_key=os.environ["TOGETHERAI_API_KEY"],
            ),
            tts=GeminiTTS(
                model=os.getenv("GOOGLE_TTS_MODEL", "gemini-1.5-flash-latest"),
                api_key=os.environ["GOOGLE_API_KEY"],
            ),
            turn_detection=MultilingualModel(),
        )

    async def on_enter(self):
        self.session.generate_reply(
            instructions="Hey, how can I help you today?", allow_interruptions=True
        )


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the first participant to connect
    participant = await ctx.wait_for_participant()
    ctx.room.metadata
    logger.info(f"starting voice assistant for participant {participant.identity}")

    usage_collector = metrics.UsageCollector()

    # Log metrics and collect usage data
    def on_metrics_collected(agent_metrics: metrics.AgentMetrics):
        metrics.log_metrics(agent_metrics)
        usage_collector.collect(agent_metrics)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
    )

    session.on("metrics_collected", on_metrics_collected)

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="Autom8 AI",
        ),
    )
