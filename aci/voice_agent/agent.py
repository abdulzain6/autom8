import asyncio
import json
import logging
from datetime import datetime, timezone
from time import time
from typing import Any, Dict, cast, Optional

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    metrics,
    MetricsCollectedEvent,
    RoomInputOptions,
    function_tool,
    RunContext,
    llm,
    get_job_context,
    ToolError,
)
from livekit.rtc import ConnectionState
from livekit.plugins import noise_cancellation, silero, openai, mistralai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# ACI / Backend Imports
from aci.common.db.sql_models import Function
from aci.common.enums import FunctionDefinitionFormat
from aci.common.schemas.function import OpenAIFunction, OpenAIFunctionDefinition
from aci.server.dependencies import get_db_session
from aci.server.function_executors.function_utils import (
    execute_function,
    format_function_definition,
)
from aci.voice_agent.config import *
from aci.common.db import crud
from aci.common.utils import create_db_session
from aci.common.db.crud import usage as usage_crud

logger = logging.getLogger("voice-agent")


def validate_html(html_content: str) -> None:
    """Validates that the provided HTML content is well-formed."""
    from html.parser import HTMLParser
    from html import unescape

    class HTMLValidator(HTMLParser):
        def __init__(self):
            super().__init__()
            self.errors = []

        def error(self, message):
            self.errors.append(message)

    try:
        unescaped_html = unescape(html_content)
    except Exception as e:
        raise ToolError(f"Invalid HTML entities: {str(e)}")

    validator = HTMLValidator()
    try:
        validator.feed(unescaped_html)
        validator.close()
    except Exception as e:
        raise ToolError(f"HTML parsing error: {str(e)}")

    if validator.errors:
        raise ToolError(f"HTML validation errors: {'; '.join(validator.errors)}")

    if not html_content.strip():
        raise ToolError("HTML content cannot be empty")

    if '<' not in html_content or '>' not in html_content:
        raise ToolError("HTML content must contain at least one HTML tag")


class Assistant(Agent):
    # Tools that must NEVER be removed during a context switch
    _core_tool_names = {
        "load_tools", 
        "display_mini_app", 
        "get_app_info", 
        "get_linked_apps",
        "create_automation", 
        "get_automation_runs", 
        "list_user_automations", 
        "run_automation", 
        "update_automation", 
        "get_user_timezone",
        "get_automation_by_id"
    }

    _restricted_apps = {"browser"}

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        # Note: We do NOT keep a persistent self.db_session here to avoid threading issues.
        # We create fresh sessions on the fly in the tools.

        # --- AUTO-CREATE LINKED ACCOUNTS ---
        # Using a local scope session for initialization
        with create_db_session(DB_FULL_URL) as init_session:
            essential_apps = ["SEARXNG", "NOTIFYME"]
            for app_name in essential_apps:
                try:
                    existing_linked_account = crud.linked_accounts.get_linked_account(
                        init_session, user_id, app_name.upper()
                    )
                    if not existing_linked_account:
                        app = crud.apps.get_app(init_session, app_name, active_only=True)
                        if app and app.has_default_credentials:
                            from aci.common.enums import SecurityScheme
                            from aci.common.schemas.security_scheme import NoAuthSchemeCredentials

                            crud.linked_accounts.create_linked_account(
                                init_session,
                                user_id,
                                app_name,
                                SecurityScheme.NO_AUTH,
                                NoAuthSchemeCredentials(),
                            )
                            init_session.commit()
                except Exception as e:
                    init_session.rollback()
                    logger.warning(f"Failed to auto-create account for {app_name}: {e}")

            # Get initial app names for prompt context
            user_app_names = crud.apps.get_user_linked_app_names(init_session, user_id)
            user_app_names = [name for name in user_app_names if name.lower() not in self._restricted_apps]

        self.linked_apps_str = ", ".join(user_app_names) if user_app_names else "No apps connected yet"
        
        super().__init__(
            instructions=f"""
Autom8 AI assistant. Today: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} UTC.

Voice: Brief (1-2 sentences), conversational. No emojis.
Your Connected Apps: {self.linked_apps_str}

### HOW TO USE TOOLS (DYNAMIC LOADING):
You do not have all tools loaded by default. You must load them based on user requests.

1. **CHECK**: If the user asks for something (e.g., "Check my email"), first check `get_app_info`.
2. **LOAD**: Call `load_tools(app_names=["GMAIL"])`.
3. **CONFIRM**: `load_tools` will instruct you to ask for user confirmation. **OBEY THIS.** Do not attempt to use the new tools in the same turn.
4. **EXECUTE**: Only after the user says "Yes" or "Proceed" in the *next* turn, use the specific tool (e.g., `GMAIL__SEND_EMAIL`).

### SPECIAL CAPABILITIES:
- **Mini Apps**: If a user needs a calculator, converter, or visual widget, offer to `display_mini_app`.
- **Automations**: You can create, list, and run scheduled tasks using the automation tools.

### SAFETY:
- Validate URLs. Block localhost/private IPs.
- If a tool fails, tell the user exactly why.
""",
            stt=mistralai.STT(model="voxtral-mini-latest", api_key=MISTRALAI_API_KEY),
            llm=openai.LLM.with_x_ai(
                model="grok-4-1-fast-non-reasoning-latest",
                temperature=0,
                api_key=XAI_API_KEY
            ),
            tts=openai.TTS(
                model="gpt-4o-mini-tts",
                voice="sage",
                instructions="Speak in a friendly and engaging tone. Ignore markdown formatting.",
                api_key=OPENAI_API_KEY,
            ),
            vad=silero.VAD.load(),
            turn_detection=MultilingualModel(),
        )

    # --- CORE INFRASTRUCTURE METHODS ---

    async def _notify_tool_used(self, tool_name: str, display_name: Optional[str] = None) -> None:
        """Sends a notification to the frontend when a tool is being used."""
        try:
            room = get_job_context().room
            participant_identity = next(iter(room.remote_participants))
            name_to_show = display_name or tool_name
            await room.local_participant.perform_rpc(
                destination_identity=participant_identity,
                method="toolUsed",
                payload=json.dumps({
                    "message": f"AI is working on your request using {name_to_show}...",
                    "tool_name": tool_name
                }),
                response_timeout=5,
            )
        except Exception:
            pass # Suppress notification errors to keep flow smooth

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        """Truncates chat history to prevent context overflow."""
        MAX_CONTEXT_CHARS = 10000
        all_items = turn_ctx.items
        if not all_items: return

        system_message = None
        conversation_items = all_items
        if all_items and isinstance(all_items[0], llm.ChatMessage) and all_items[0].role == "system":
            system_message = all_items[0]
            conversation_items = all_items[1:]

        system_msg_len = len(system_message.text_content or "") if system_message else 0
        truncated_conversation = []
        current_char_count = 0

        for item in reversed(conversation_items):
            item_len = 0
            if isinstance(item, llm.ChatMessage):
                item_len = len(item.text_content or "")
            elif isinstance(item, llm.FunctionCall):
                item_len = len(item.name) + len(item.arguments)
            elif isinstance(item, llm.FunctionCallOutput):
                item_len = len(item.name) + len(item.output)

            if current_char_count + item_len + system_msg_len > MAX_CONTEXT_CHARS:
                break

            truncated_conversation.append(item)
            current_char_count += item_len

        final_items = []
        if system_message: final_items.append(system_message)
        final_items.extend(reversed(truncated_conversation))
        turn_ctx.items = final_items

    async def on_enter(self):
        self.session.generate_reply(user_input="Hey!", allow_interruptions=True)

    # --- DYNAMIC TOOL LOGIC ---

    def _create_tool_callable(self, func_obj: Function):
        """
        Factory to create a thread-safe async callable for a dynamic tool.
        Wraps the sync DB execution in asyncio.to_thread.
        """
        async def tool_callable(raw_arguments: dict[str, object], context: RunContext):
            # Notify frontend
            display_name = func_obj.name.split("__")[-1].replace("_", " ").lower().capitalize()
            await self._notify_tool_used(func_obj.name, display_name=display_name)
            
            try:
                # Run in thread to prevent blocking voice loop
                result_json = await asyncio.to_thread(
                    self._execute_tool_logic, func_obj, **raw_arguments
                )
                return result_json
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                return json.dumps({"error": str(e)})

        return tool_callable

    def _execute_tool_logic(self, function: Function, **kwargs) -> str:
        """
        Worker method running in a thread. Creates its OWN DB session.
        """
        with create_db_session(DB_FULL_URL) as tool_db_session:
            try:
                logger.info(f"Executing dynamic tool: {function.name}")
                result = execute_function(
                    tool_db_session, function.name, self.user_id, kwargs, run_id=None
                )
                if result.success:
                    return json.dumps(result.data) if isinstance(result.data, (dict, list)) else str(result.data)
                else:
                    return json.dumps({"error": result.error})
            except Exception as e:
                logger.error(f"Error in _execute_tool_logic: {e}", exc_info=True)
                return json.dumps({"error": str(e)})

    @function_tool()
    async def load_tools(self, context: RunContext, app_names: list[str]) -> str:
        """
        Loads the specific tools for the requested apps.
        It rebuilds the toolset from scratch (Core Tools + New Apps) to ensure a clean state.
        """
        logger.info(f"Loading tools for apps: {app_names}")
        await self._notify_tool_used("load_tools", display_name=f"Loading {', '.join(app_names)}...")

        # 1. Get the function definitions from DB
        try:
            with create_db_session(DB_FULL_URL) as db_sess:
                functions = crud.functions.get_user_enabled_functions_for_apps(
                    db_session=db_sess, user_id=self.user_id, app_names=app_names
                )
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)})

        if not functions:
            return f"No enabled tools found for apps: {app_names}. Are they connected?"

        # 2. Create the Dynamic Tools
        dynamic_tools = []
        for function in functions:
            # Format schema
            formatted_function: OpenAIFunction = cast(
                OpenAIFunctionDefinition,
                format_function_definition(function, FunctionDefinitionFormat.OPENAI),
            ).function
            
            raw_schema = {
                "type": "function",
                "name": function.name,
                "description": function.description,
                "parameters": formatted_function.parameters,
            }
            
            # Create wrapper and instance
            tool_logic = self._create_tool_callable(function)
            livekit_tool = function_tool(raw_schema=raw_schema)(tool_logic)
            dynamic_tools.append(livekit_tool)

        # 3. REBUILD STRATEGY: Start Empty -> Add Core -> Add Dynamic
        final_tool_list = []

        # A. Add Core Tools (By looking them up on 'self')
        # This avoids inspecting the 'tool' object and crashing on missing attributes
        for name in self._core_tool_names:
            if hasattr(self, name):
                core_tool = getattr(self, name)
                final_tool_list.append(core_tool)
            else:
                logger.warning(f"Core tool '{name}' not found on Agent instance.")

        # B. Add the new Dynamic Tools
        final_tool_list.extend(dynamic_tools)
                
        await self.update_tools(tools=final_tool_list)

        app_list_str = ", ".join(app_names)
        
        msg = (
            f"SYSTEM UPDATE: The tools for {app_list_str} have been loaded successfully. "
            f"CRITICAL INSTRUCTION: Do NOT attempt to use these tools immediately. "
            f"Instead, tell the user: 'I have connected to {app_list_str}. Shall I proceed with your request?' "
            f"Wait for the user's confirmation."
        )
        return msg

    # --- STANDARD TOOLS ---

    @function_tool()
    async def get_linked_apps(self) -> str:
        """Fetch and return the names of apps linked to the user's account."""
        return self.linked_apps_str

    @function_tool()
    async def get_app_info(self, app_names: list[str]) -> str:
        """Fetch detailed information (functions/descriptions) about specified apps."""
        if len(app_names) > 3:
            return "Request info for max 3 apps at a time."
        
        with create_db_session(DB_FULL_URL) as sess:
            apps = crud.apps.get_apps_with_functions_by_names(
                sess, [n.upper() for n in app_names]
            )
            if not apps:
                return f"No apps found with names: {app_names}"

            response_data = []
            for app in apps:
                if app.name.lower() in self._restricted_apps: continue
                response_data.append({
                    "name": app.name,
                    "description": app.description,
                    "functions": [
                        {"name": f.name, "description": f.description} 
                        for f in app.functions if f.active
                    ]
                })
            return json.dumps(response_data)

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "display_mini_app",
            "description": "Renders a mini-app. REQUIRED: HTML with <style> and <script>.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_title": {"type": "string", "default": "Mini App"},
                    "html_content": {"type": "string", "description": "Complete HTML/CSS/JS"},
                },
                "required": ["html_content"],
            },
        }
    )
    async def display_mini_app(self, raw_arguments: dict[str, object], context: RunContext):
        try:
            html_content = str(raw_arguments["html_content"])
            validate_html(html_content)
            
            await self._notify_tool_used("display_mini_app", "Mini App")
            room = get_job_context().room
            participant_identity = next(iter(room.remote_participants))

            await room.local_participant.perform_rpc(
                destination_identity=participant_identity,
                method="displayMiniApp",
                payload=json.dumps({
                    "title": raw_arguments.get("app_title", "Mini App"),
                    "html": html_content,
                }),
                response_timeout=10,
            )
            return "App displayed successfully."
        except Exception as e:
            raise ToolError(f"Failed to display app: {e}")

    @function_tool()
    async def get_user_timezone(self, raw_arguments: dict[str, object], context: RunContext):
        try:
            room = get_job_context().room
            participant_identity = next(iter(room.remote_participants))
            resp = await room.local_participant.perform_rpc(
                destination_identity=participant_identity,
                method="getUserTimezone",
                payload="{}",
                response_timeout=10,
            )
            return f"Timezone info: {resp}"
        except Exception as e:
            return f"Could not get timezone: {e}"

    # --- AUTOMATION TOOLS ---
    @function_tool()
    async def list_user_automations(self, raw_arguments: dict[str, object], context: RunContext):
        limit = min(int(raw_arguments.get("limit", 20)), 100)
        with create_db_session(DB_FULL_URL) as sess:
            automations = crud.automations.list_user_automations(sess, self.user_id, limit, 0)
            if not automations: return "No automations found."
            return json.dumps([{
                "id": a.id, "name": a.name, 
                "active": a.active, "schedule": a.cron_schedule
            } for a in automations])

    @function_tool()
    async def create_automation(self, raw_arguments: dict[str, object], context: RunContext):
        try:
            from aci.common.schemas.automations import AutomationAgentCreate, AutomationCreate
            from aci.common.db.crud import linked_accounts as linked_accounts_crud

            args = {
                "name": str(raw_arguments["name"]),
                "goal": str(raw_arguments["goal"]),
                "app_names": raw_arguments["app_names"],
                "description": raw_arguments.get("description"),
                "is_deep": bool(raw_arguments.get("is_deep", False)),
                "is_recurring": bool(raw_arguments.get("is_recurring", False)),
                "cron_schedule": raw_arguments.get("cron_schedule"),
            }

            with create_db_session(DB_FULL_URL) as sess:
                # Check linked accounts
                linked_ids = []
                for app in args["app_names"]:
                    la = linked_accounts_crud.get_linked_account(sess, self.user_id, str(app).upper())
                    if not la: return f"App {app} not connected."
                    linked_ids.append(la.id)

                auto_create = AutomationCreate(
                    **args, linked_account_ids=linked_ids, active=True
                )
                automation = crud.automations.create_automation(sess, self.user_id, auto_create)
                sess.commit()
                return f"Automation '{automation.name}' created (ID: {automation.id})."
        except Exception as e:
            return f"Failed to create automation: {e}"

    @function_tool()
    async def run_automation(self, raw_arguments: dict[str, object], context: RunContext):
        try:
            from aci.server.tasks.tasks import execute_automation
            auto_id = str(raw_arguments["automation_id"])
            with create_db_session(DB_FULL_URL) as sess:
                auto = crud.automations.get_automation(sess, auto_id)
                if not auto or auto.user_id != self.user_id: return "Automation not found."
                
                run = crud.automation_runs.create_run(sess, auto_id)
                sess.commit()
                execute_automation(run.id) # Queue task
                return f"Automation started (Run ID: {run.id})."
        except Exception as e:
            return f"Error running automation: {e}"

    @function_tool()
    async def update_automation(self, raw_arguments: dict[str, object], context: RunContext):
        try:
            from aci.common.schemas.automations import AutomationUpdate
            auto_id = str(raw_arguments.pop("automation_id"))
            with create_db_session(DB_FULL_URL) as sess:
                auto = crud.automations.get_automation(sess, auto_id)
                if not auto or auto.user_id != self.user_id: return "Automation not found."
                
                # Handle app names / linked accounts update if present
                if "app_names" in raw_arguments:
                     # Logic to fetch linked accounts would go here similar to create
                     del raw_arguments["app_names"] 

                update_data = AutomationUpdate(**raw_arguments)
                updated = crud.automations.update_automation(sess, auto_id, update_data)
                sess.commit()
                return f"Updated automation {updated.name}."
        except Exception as e:
            return f"Error updating: {e}"
    
    @function_tool()
    async def get_automation_runs(self, raw_arguments: dict[str, object], context: RunContext):
        limit = int(raw_arguments.get("limit", 10))
        auto_id = str(raw_arguments["automation_id"])
        with create_db_session(DB_FULL_URL) as sess:
             runs = crud.automation_runs.list_runs_for_automation(sess, auto_id, limit, 0)
             if not runs: return "No runs found."
             return json.dumps([
                 {"id": r.id, "status": r.status.value, "started": str(r.started_at)} 
                 for r in runs
             ])
             
    @function_tool()
    async def get_automation_by_id(self, raw_arguments: dict[str, object], context: RunContext):
        auto_id = str(raw_arguments["automation_id"])
        with create_db_session(DB_FULL_URL) as sess:
            auto = crud.automations.get_automation(sess, auto_id)
            if not auto or auto.user_id != self.user_id: return "Not found."
            return json.dumps({
                "id": auto.id, "name": auto.name, "goal": auto.goal,
                "active": auto.active, "schedule": auto.cron_schedule
            })

# --- MAIN ENTRYPOINT ---

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
    user_id = participant.identity

    # Usage Tracking Setup
    usage_collector = metrics.UsageCollector()
    session_start_time = time()
    usage_recorded = False
    session_metrics = {'llm_tokens': 0, 'stt_duration': 0.0, 'tts_characters': 0}

    def save_usage_metrics():
        nonlocal usage_recorded
        if usage_recorded: return
        usage_recorded = True
        
        duration_mins = (time() - session_start_time) / 60
        summary = usage_collector.get_summary()

        # Fallback to collector if realtime metrics are empty
        if session_metrics['llm_tokens'] == 0 and session_metrics['stt_duration'] == 0:
             session_metrics['llm_tokens'] = summary.llm_prompt_tokens + summary.llm_completion_tokens
             session_metrics['stt_duration'] = summary.stt_audio_duration
             session_metrics['tts_characters'] = summary.tts_characters_count

        try:
            with create_db_session(DB_FULL_URL) as db_session:
                stt_mins = session_metrics['stt_duration'] / 60
                if duration_mins > 0 or session_metrics['llm_tokens'] > 0:
                    usage_crud.create_voice_session_event(
                        db_session, user_id,
                        voice_agent_minutes=duration_mins,
                        llm_tokens=session_metrics['llm_tokens'],
                        stt_minutes=stt_mins,
                        tts_characters=session_metrics['tts_characters']
                    )
                    logger.info(f"Saved usage for {user_id}: {duration_mins:.2f} mins")
        except Exception as e:
            logger.error(f"Failed to save usage: {e}")

    # Event Listeners
    ctx.room.on("participant_disconnected", lambda p: save_usage_metrics() if p.identity == user_id else None)
    ctx.room.on("disconnected", lambda r: save_usage_metrics())
    ctx.room.on("connection_state_changed", lambda s: save_usage_metrics() if s == ConnectionState.CONN_DISCONNECTED else None)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
        max_tool_steps=4, # Limit tool chaining
    )

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)
        if isinstance(ev.metrics, metrics.LLMMetrics):
            session_metrics['llm_tokens'] += ev.metrics.completion_tokens + ev.metrics.prompt_tokens
        elif isinstance(ev.metrics, metrics.STTMetrics):
            session_metrics['stt_duration'] += ev.metrics.audio_duration
        elif isinstance(ev.metrics, metrics.TTSMetrics):
            session_metrics['tts_characters'] += ev.metrics.characters_count

    ctx.add_shutdown_callback(lambda: logger.info(f"Final usage: {usage_collector.get_summary()}"))

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