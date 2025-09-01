import asyncio
from datetime import datetime
import json
import logging
from time import time
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
from livekit.agents import llm
from livekit.agents import get_job_context, ToolError


logger = logging.getLogger("voice-agent")


class Assistant(Agent):
    _core_tool_names = {"load_tools", "display_mini_app", "get_app_info"}

    def __init__(self, user_id: str) -> None:
        self.db_session = create_db_session(DB_FULL_URL)
        self.user_id = user_id
        user_app_names = crud.apps.get_user_linked_app_names(self.db_session, user_id)
        self.tools_names: Dict[str, Any] = {}

        super().__init__(
            instructions=f"""
You are Autom8, an expert AI voice assistant. Your goal is to help users by using tools to accomplish tasks.

Today is {datetime.now().strftime('%Y-%-m-%d')}.

---
### Your Voice and Personality: CRITICAL RULES
- **Radically Brief:** Your primary goal is to be concise. Most responses should be one or two short sentences. Never speak in long paragraphs. Get straight to the point.
- **Extremely Conversational:** Speak as if you're talking to a friend on the phone. Use contractions like "it's," "you're," and "I'll." Your tone should be warm, natural, and helpful.
- **Summarize, Don't Recite:** This is vital. When a tool returns data, NEVER read the raw data back. Summarize the single most important piece of information in a natural, spoken phrase.
    - BAD (Reciting): "The result of the weather tool is: Temperature 28 degrees Celsius, condition sunny, humidity 60 percent, wind speed 5 kilometers per hour."
    - GOOD (Summarizing): "It looks like it'll be sunny and around 28 degrees today."
- **No Written Language:** Your responses are for voice ONLY. Do not use any formatting, lists, or sentence structures that sound like a written document.
- **Global Friend:** If the user speaks a language other than English, reply in their language with the same friendly and brief style.
---

### Interaction Flow: How to Handle Conversations
- **Ask Clarifying Questions:** If a user's request is vague or ambiguous, you MUST ask for more details before using any tools. Do not guess. For example, if the user says "send a message," you should ask, "Who should I send it to, and what should it say?"
- **Confirm Before Acting:** Before you perform any action that creates or modifies data (like creating a post, sending an email, or deleting something), you MUST first summarize what you are about to do and ask for the user's permission to proceed. For example, say: "Okay, I'm ready to create a GitHub issue titled 'Fix the login bug.' Should I go ahead?" Only proceed after the user confirms.
---

### CRITICAL INSTRUCTIONS FOR USING TOOLS:
First, determine if a tool is actually needed. For simple greetings or questions, respond directly.
Second, if a tool is needed, you start with only two available: `get_app_info` and `load_tools`.
Third, to figure out which app to use, you must first call `get_app_info`.
Fourth, review the app and function descriptions to decide which app is the best fit for the user's task.
Fifth, once you have identified the correct app, call `load_tools` with that app's name.
Sixth, execute the newly loaded functions to complete the user's request.
Finally, if none of the available apps can fulfill the user's request, you must inform the user clearly that you cannot complete the task.

### Available Apps for this User:
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
                instructions="Speak in a friendly and engaging tone. Ignore markdown formatting, treat it as plain text. Do not pronounce special characters like #, *, etc.",
                api_key=OPENAI_API_KEY,
            ),
            vad=silero.VAD.load(),
            turn_detection=MultilingualModel(),
        )

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """
        Called when the user has finished speaking. This implementation manually
        truncates the chat history to stay under a character limit, preserving
        the system message and the most recent exchanges.

        This version is updated to accurately reflect the ChatContext class structure.
        """
        MAX_CONTEXT_CHARS = 10000

        all_items = turn_ctx.items
        if not all_items:
            return

        # 1. Preserve the system message (it's usually the first item)
        system_message = None
        conversation_items = all_items
        # Check if the first item is a ChatMessage with the 'system' role
        if (
            all_items
            and isinstance(all_items[0], llm.ChatMessage)
            and all_items[0].role == "system"
        ):
            system_message = all_items[0]
            conversation_items = all_items[1:]

        system_msg_len = len(system_message.text_content or "") if system_message else 0

        # 2. Iterate backwards from the newest item to build the new history
        truncated_conversation = []
        current_char_count = 0
        for item in reversed(conversation_items):
            item_len = 0
            # Calculate character length based on the item's type
            if isinstance(item, llm.ChatMessage):
                # Use the convenient .text_content property
                item_len = len(item.text_content or "")
            elif isinstance(item, llm.FunctionCall):
                # Approximate length by name + arguments
                item_len = len(item.name) + len(item.arguments)
            elif isinstance(item, llm.FunctionCallOutput):
                # Approximate length by name + output content
                item_len = len(item.name) + len(item.output)

            # Check if adding this item would exceed our character budget
            if current_char_count + item_len + system_msg_len > MAX_CONTEXT_CHARS:
                break  # Stop adding older items

            truncated_conversation.append(item)
            current_char_count += item_len

        # 3. Reconstruct the final item list in the correct (chronological) order
        final_items = []
        if system_message:
            final_items.append(system_message)

        # The list was built backwards, so we reverse it to restore order
        final_items.extend(reversed(truncated_conversation))

        # 4. Update the mutable context with the new, shorter item history
        turn_ctx.items = final_items

        logger.info(
            f"Context truncated: {len(all_items)} items -> {len(final_items)} items "
            f"({current_char_count + system_msg_len} chars)."
        )

    async def on_enter(self):
        self.session.generate_reply(user_input="Hey!", allow_interruptions=True)

    def _create_tool_callable(self, func_obj: Function):
        """
        Factory to create a unique async callable for a function,
        correctly capturing the function object in a closure.
        """

        async def tool_callable(raw_arguments: dict[str, object]):
            result = await asyncio.to_thread(
                self._execute_tool_logic, func_obj, **raw_arguments
            )
            logger.info(f"Tool {func_obj.name} returned result: {result}")
            return f"{result[:10000]}... (truncated)" if len(result) > 10000 else result

        return tool_callable

    @function_tool(raw_schema={
        "type": "function",
        "name": "display_mini_app",
        "description": "Renders a self-contained mini-application (HTML, CSS, JS) on the user's frontend.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "html_content": {
                    "type": "string",
                    "description": "The complete HTML string for the application, including all necessary CSS and JavaScript."
                },  
            },
            "required": ["html_content"],
        },
    })
    async def display_mini_app(
        self,
        context: RunContext,
        html_content: str,
        app_title: str = "Mini App",
        timeout: float = 10.0,
    ):
        """
        Renders a self-contained mini-application (HTML, CSS, JS) on the user's frontend.

        This tool is used to display interactive widgets, calculators, or other small applications
        for the user to interact with. The application's logic must be fully contained
        within the provided HTML string.

        Args:
            app_title: A short, descriptive title for the application (e.g., "BMI Calculator").
            html_content: The complete HTML string for the application. This must include
                        all necessary CSS (e.g., in <style> tags) and JavaScript
                        (e.g., in <script> tags).
            timeout: The time in seconds to wait for the frontend to acknowledge the request.

        Returns:
            A confirmation message indicating that the app was sent successfully.
        """
        try:
            room = get_job_context().room
            participant_identity = next(iter(room.remote_participants))

            # The frontend client must have a listener for the "displayMiniApp" method
            await room.local_participant.perform_rpc(
                destination_identity=participant_identity,
                method="displayMiniApp",
                payload=json.dumps({"title": app_title, "html": html_content}),
                response_timeout=timeout,
            )

            # The frontend should send back a simple success message
            return "App successfully sent to the user for display."
        except StopIteration:
            raise ToolError(
                "No remote participants found in the room to display the app."
            )
        except Exception as e:
            # This will catch RPC timeouts or other communication errors
            raise ToolError(f"Failed to send the mini-app to the frontend: {e}")

    @function_tool()
    async def get_app_info(self, app_names: list[str]) -> str:
        """Fetch and return detailed information about specified apps and their functions."""
        logger.info(f"Agent requested info for apps: {app_names}")
        if len(app_names) > 3:
            return json.dumps(
                {"error": "You can request info for up to 3 apps at a time."}
            )
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
            formatted_function: OpenAIFunction = cast(
                OpenAIFunctionDefinition,
                format_function_definition(function, FunctionDefinitionFormat.OPENAI),
            ).function
            raw_schema = {
                "type": "function",
                "name": function.name,
                "description": function.description,
                "strict": True,
                "parameters": formatted_function.parameters,
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

        msg = f"Successfully loaded {len(new_tools)} tools for apps: {app_names}. Ask the user if they would like to proceed"
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
        max_tool_steps=4,
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
