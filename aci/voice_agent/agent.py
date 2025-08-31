import logging
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
from livekit.plugins import noise_cancellation, silero, openai, mistralai
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from aci.voice_agent.config import *

logger = logging.getLogger("voice-agent")

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""
You are a Autom8 an AI voice assistant talking to users through voice only.
Speak naturally, like a real person. No lists, no summaries, no robotic tone.
Keep replies short, clear, and conversational. Just talk—dont write.
Avoid punctuation thats hard to speak or sounds unnatural.
If a user speaks in a different language, respond in their language if possible.
""",
            stt=mistralai.STT(
                model="voxtral-mini-latest", api_key=MISTRALAI_API_KEY
            ),
            llm=openai.LLM.with_cerebras(
                model="qwen-3-235b-a22b-instruct-2507",
                api_key=CEREBRAS_API_KEY,
            ),
            tts=openai.TTS(
                model="gpt-4o-mini-tts",
                voice="sage",
                instructions="Speak in a friendly and engaging tone.",
                api_key=OPENAI_API_KEY,
            ),
            vad=silero.VAD.load(),
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

    logger.info(f"starting voice assistant for participant {participant.identity}")

    user_id = participant.identity
    
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
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
            ws_url=LIVEKIT_URL,
        ),
    )
