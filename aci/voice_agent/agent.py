import asyncio
import json
import logging
from typing import Any, Dict, cast
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
from aci.common.db.sql_models import Function
from aci.common.enums import FunctionDefinitionFormat
from aci.common.schemas.function import OpenAIFunction, OpenAIFunctionDefinition
from aci.server.dependencies import get_db_session
from aci.server.function_executors.function_utils import (
    execute_function,
    format_function_definition,
)
from aci.voice_agent.config import *
from livekit.agents import function_tool, Agent, RunContext
from aci.common.db import crud
from aci.common.utils import create_db_session


logger = logging.getLogger("voice-agent")


class Assistant(Agent):
    _core_tool_names = {"load_tools"}

    def __init__(self, user_id: str) -> None:
        self.db_session = create_db_session(DB_FULL_URL)
        self.user_id = user_id
        user_app_names = crud.apps.get_user_linked_app_names(self.db_session, user_id)
        self.tools_names: Dict[str, Any] = {}

        super().__init__(
            instructions=f"""
You are Autom8, an expert AI voice assistant. Your goal is to help users by using tools to accomplish tasks.

**Your Behavior:**
- Speak naturally and conversationally. Keep replies short and clear.
- If a user speaks in a different language, respond in their language.

**CRITICAL INSTRUCTIONS FOR USING TOOLS:**
1.  **You start with only TWO tools**: `get_app_info` and `load_tools`. All other tools are hidden.
2.  To figure out which app to use, you MUST first call `get_app_info`. This tool will give you a description of what the app does **and a list of the functions (tools) it contains.**
3.  Review the app and function descriptions to decide which app is the best fit for the user's task.
4.  Once you have identified the correct app, call `load_tools` with that app's name to make its functions available for use.
5.  Finally, execute the newly loaded functions to complete the user's request.
6.  **Capability Check**: After using `get_app_info`, if none of the available apps or their functions can fulfill the user's request, you MUST inform the user clearly and politely that you cannot complete the task.

**Available Apps for this User:**
{', '.join(user_app_names) if user_app_names else 'No apps are currently linked.'}
""",
            stt=mistralai.STT(model="voxtral-mini-latest", api_key=MISTRALAI_API_KEY),
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

    def _create_tool_callable(self, func_obj: Function):
        """
        Factory to create a unique async callable for a function,
        correctly capturing the function object in a closure.
        """

        async def tool_callable(raw_arguments: dict[str, object]):
            return await asyncio.to_thread(
                self._execute_tool_logic, func_obj, **raw_arguments
            )

        return tool_callable

    @function_tool()
    async def get_app_info(self, app_names: list[str]) -> str:
        """Fetch and return detailed information about specified apps and their functions."""
        logger.info(f"Agent requested info for apps: {app_names}")
        try:
            # Use the new CRUD function to get apps with their functions pre-loaded
            apps = crud.apps.get_apps_with_functions_by_names(
                self.db_session, app_names
            )
            if not apps:
                return json.dumps({"error": f"No apps found with names: {app_names}"})

            # Build the detailed response
            response_data = []
            for app in apps:
                response_data.append(
                    {
                        "name": app.name,
                        "description": app.description,
                        "functions": [
                            {"name": func.name, "description": func.description}
                            for func in app.functions
                            if func.active
                        ],
                    }
                )
            return json.dumps(response_data)
        except Exception as e:
            logger.error(f"Error in get_app_info: {e}", exc_info=True)
            return json.dumps({"error": "An internal error occurred."})

    @function_tool()
    async def load_tools(
        self,
        context: "RunContext",
        app_names: list[str],
    ) -> dict[str, Any]:
        logger.info(f"Loading tools for user {self.user_id} and apps {app_names}")
        try:
            functions = crud.functions.get_user_enabled_functions_for_apps(
                db_session=self.db_session, user_id=self.user_id, app_names=app_names
            )
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if not functions:
            return {
                "status": "success",
                "message": f"No enabled tools found for apps: {app_names}.",
            }

        new_tools: Dict[str, Any] = {}
        for function in functions:
            raw_schema = {
                "type": "function",
                "name": function.name,
                "description": function.description,
                "parameters": function.parameters,
            }
            tool_logic = self._create_tool_callable(function)
            livekit_tool = function_tool(raw_schema=raw_schema)(tool_logic)
            new_tools[function.name] = livekit_tool

        core_tools: Dict[str, Any] = {}
        for core_name in self._core_tool_names:
            if core_name == "load_tools":
                core_tools["load_tools"] = self.load_tools
            elif core_name in self.tools_names:
                core_tools[core_name] = self.tools_names[core_name]

        self.tools_names = {**core_tools, **new_tools}
        await self.update_tools(tools=list(self.tools_names.values()))

        msg = f"Successfully loaded {len(new_tools)} tools for apps: {app_names}."
        logger.info(msg)
        return {"status": "success", "message": msg}

    def _execute_tool_logic(self, function: Function, **kwargs) -> str:
        """
        A helper method containing the actual logic for executing a tool.
        It creates a new, isolated database session for each execution.
        """
        with get_db_session() as tool_db_session:
            try:
                logger.info(f"Executing tool: {function.name} with args: {kwargs}")
                # Execute the function and get the Pydantic result model
                result = execute_function(
                    tool_db_session, function.name, self.user_id, kwargs, run_id=None
                )
                return result.model_dump_json()
            except Exception as e:
                logger.error(
                    f"Error executing tool: {function.name} with args: {kwargs}, error: {e}",
                    exc_info=True,
                )
                # Return a JSON string with the error message
                return json.dumps({"error": str(e)})


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
        agent=Assistant(user_id=user_id),
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
