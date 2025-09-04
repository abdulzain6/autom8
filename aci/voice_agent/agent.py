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
    MetricsCollectedEvent,
    RoomInputOptions,
)
from livekit.rtc import ConnectionState
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
from aci.common.db.crud import usage as usage_crud


logger = logging.getLogger("voice-agent")


class Assistant(Agent):
    _core_tool_names = {"load_tools", "display_mini_app", "get_app_info", "create_automation", "get_automation_runs", "list_user_automations", "run_automation", "get_current_session_usage"}

    def __init__(self, user_id: str) -> None:
        self.db_session = create_db_session(DB_FULL_URL)
        self.user_id = user_id
        user_app_names = crud.apps.get_user_linked_app_names(self.db_session, user_id)
        self.tools_names: Dict[str, Any] = {}

        super().__init__(
            instructions=f"""
You are Autom8, a friendly and efficient AI voice assistant. Your goal is to help users by accomplishing tasks quickly and communicating clearly.

Today is {datetime.now().strftime('%Y-%m-%d')}.

---
### Your Voice and Personality: CRITICAL RULES
- **Radically Brief:** Your primary goal is to be concise. Most responses should be one or two short sentences.
- **Extremely Conversational:** Speak as if you're talking to a friend on the phone. Use contractions like "it's," "you're," and "I'll."
- **Summarize, Don't Recite:** When a tool returns data, NEVER read the raw data back. Summarize the single most important piece of information in a natural, spoken phrase.
- **No Written Language:** Your responses are for voice ONLY. Do not use any formatting or sentence structures that sound like a written document.
- **Global Friend:** If the user speaks a language other than English, reply in their language.
---

### Your Proactive Superpower: Mini-Apps
You have a special tool called `display_mini_app`. This is your most creative ability. You should actively listen for opportunities to use it to help the user.
- **When to Use It**: If a user mentions a topic that could be helped with a small, visual, or interactive tool, you should offer to create one for them.
- **Examples**:
    - If the user talks about health, fitness, or weight, **proactively offer to show a BMI Calculator.**
    - If they discuss finance or loans, **proactively offer a Loan Interest Calculator.**
    - If they mention travel between countries, **proactively offer a simple Currency Converter.**
- **How to Use It**: You must generate the complete, self-contained HTML for these apps yourself. This includes all necessary CSS in `<style>` tags and all JavaScript logic in `<script>` tags. The app must work entirely on the client-side.
- **Always Ask First**: Before showing an app, always ask the user. For example, say: "Hey, I could spin up a little BMI calculator for you on the screen if you'd like?"
---

### AUTOMATION CAPABILITIES:
You can help users create and manage automations that run automatically to accomplish tasks. You have these automation tools:

**create_automation**: Creates new automations that can perform tasks using available apps
- IMPORTANT: Before creating, always list existing automations to check for duplicates or similar ones
- Use your intelligence to determine if the user's request is similar to existing automations
- Only create automations for tasks that can be accomplished with the user's available apps
- Examples: Daily email summaries, scheduled reports, automatic file processing, reminders
- Always ask which apps are needed and verify the user has them connected
- All cron schedules use UTC time - make this clear to users
- Minimum scheduling interval is 30 minutes

**run_automation**: Manually triggers an automation to run immediately
- Use this when users want to test an automation or run it outside its schedule
- Verify the automation exists and belongs to the user before running

**get_automation_runs**: Shows execution history for automations to track performance and troubleshoot issues

**list_user_automations**: Lists all user's automations with their current status
- Use this to check for existing automations before creating new ones
- Help users find automations they want to run or modify

IMPORTANT: 
- Always list existing automations first when users want to create new ones - use your intelligence to spot similarities
- Before running automations, verify they exist and are accessible to the user
- Only create automations that can actually be accomplished with the available tools
- Always explain what apps are needed and verify the user has access to them

Common automation examples you can help with:
- Email notifications (using notifyme + data sources like news, weather, etc.)
- File processing (using pdf_tools, docx_tools + storage apps)
- Data collection and reporting (using web scraping + notification apps)
- Social media monitoring (using news feeds + notification systems)
---

### CRITICAL INSTRUCTIONS FOR EXTERNAL TOOLS:
First, determine if an external tool is actually needed. For simple conversation, respond directly.
Second, if a tool is needed, you start with only two available: `get_app_info` and `load_tools`.
Third, to figure out which app to use, you must first call `get_app_info`.
Fourth, review the app and function descriptions to decide which app is the best fit for the user's task.
Fifth, once you have identified the correct app, call `load_tools` with that app's name.
Sixth, execute the newly loaded functions to complete the user's request.
Finally, if none of the available apps can fulfill the user's request, you must inform the user that you cannot complete the task.

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
                "app_title": {
                    "type": "string",
                    "description": "A short, descriptive title for the application (e.g., 'BMI Calculator').",
                    "default": "Mini App"
                },
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
        raw_arguments: dict[str, object],
        context: RunContext
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
                payload=json.dumps({"title": raw_arguments.get("app_title", "Mini App"), "html": raw_arguments["html_content"]}),
                response_timeout=10,
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
            elif core_name == "create_automation":
                core_tools["create_automation"] = self.create_automation
            elif core_name == "get_automation_runs":
                core_tools["get_automation_runs"] = self.get_automation_runs
            elif core_name == "list_user_automations":
                core_tools["list_user_automations"] = self.list_user_automations
            elif core_name == "run_automation":
                core_tools["run_automation"] = self.run_automation
            elif core_name == "get_current_session_usage":
                core_tools["get_current_session_usage"] = self.get_current_session_usage
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

    @function_tool(raw_schema={
        "type": "function",
        "name": "create_automation",
        "description": "Creates a new automation that can perform tasks using available apps. Only create automations for tasks that can be accomplished with the available tools.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "A clear, descriptive name for the automation (max 255 characters)"
                },
                "description": {
                    "type": "string",
                    "description": "A brief description of what this automation does"
                },
                "goal": {
                    "type": "string",
                    "description": "The specific goal or instruction for the automation - what exactly should it accomplish?"
                },
                "app_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of app names required for this automation (e.g., ['gmail', 'google_calendar', 'notifyme'])"
                },
                "is_deep": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set to true for complex automations that require multiple steps"
                },
                "is_recurring": {
                    "type": "boolean", 
                    "default": False,
                    "description": "Whether this automation should run on a schedule"
                },
                "cron_schedule": {
                    "type": "string",
                    "description": "UTC cron schedule (e.g., '0 9 * * 1' for every Monday at 9 AM UTC). Required if is_recurring is true. Minimum interval is 30 minutes."
                }
            },
            "required": ["name", "goal", "app_names"]
        }
    })
    async def create_automation(
        self,
        raw_arguments: dict[str, object],
        context: RunContext
    ):
        """
        Creates a new automation that can perform tasks using available apps.
        
        This tool creates automations that run automatically to accomplish specific goals.
        Only create automations for tasks that can be accomplished with the user's available apps.
        
        Examples of good automations:
        - Daily email summary from news sources
        - Weekly calendar reminders 
        - Automatic file processing and notifications
        - Scheduled data collection and reporting
        
        The automation will use the specified apps to accomplish the goal. Make sure the user 
        has the necessary apps connected before creating the automation.
        """
        logger.info(f"[AUTOMATION_TOOL] create_automation called by user {self.user_id}")
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")
        
        try:
            # Import here to avoid circular imports
            from aci.common.schemas.automations import AutomationAgentCreate
            from aci.common.db import crud
            from aci.common.db.crud import linked_accounts as linked_accounts_crud
            from aci.common.schemas.automations import AutomationCreate
            
            # Convert raw arguments to proper types
            automation_args = {
                "name": str(raw_arguments["name"]),
                "goal": str(raw_arguments["goal"]),
                "app_names": [str(app) for app in raw_arguments["app_names"]] if isinstance(raw_arguments["app_names"], list) else [],
                "description": str(raw_arguments.get("description")) if raw_arguments.get("description") else None,
                "is_deep": bool(raw_arguments.get("is_deep", False)),
                "is_recurring": bool(raw_arguments.get("is_recurring", False)),
                "cron_schedule": str(raw_arguments.get("cron_schedule")) if raw_arguments.get("cron_schedule") else None
            }
            
            logger.info(f"[AUTOMATION_TOOL] Processed arguments: {automation_args}")
            
            # Create the automation schema
            automation_data = AutomationAgentCreate(**automation_args)
            logger.info(f"[AUTOMATION_TOOL] Created automation schema for '{automation_data.name}'")
            
            # Check if a similar automation already exists - let the AI be smart about this
            existing_automations = crud.automations.list_user_automations(
                self.db_session, self.user_id, limit=20, offset=0
            )
            logger.info(f"[AUTOMATION_TOOL] Found {len(existing_automations)} existing automations for user")
            
            # Only check for exact name duplicates - let the AI handle similarity detection
            automation_name_lower = automation_data.name.lower()
            for existing in existing_automations:
                if existing.name.lower() == automation_name_lower:
                    logger.warning(f"[AUTOMATION_TOOL] Duplicate automation name detected: '{existing.name}' (ID: {existing.id})")
                    return f"Error: An automation named '{existing.name}' already exists (ID: {existing.id}). Please choose a different name or use the existing automation."
            
            # If there are existing automations, mention them so the AI can consider them
            if existing_automations:
                automation_list = []
                for existing in existing_automations[:5]:  # Show up to 5 existing automations
                    status = "Active" if existing.active else "Inactive"
                    automation_list.append(f"'{existing.name}' ({status}) - {existing.goal}")
                
                existing_info = "\n   ".join(automation_list)
                if len(existing_automations) > 5:
                    existing_info += f"\n   ... and {len(existing_automations) - 5} more"
                
                # Let the AI know about existing automations but proceed with creation
                logger.info(f"[AUTOMATION_TOOL] Existing automations context provided to AI")
                print(f"Note: You have {len(existing_automations)} existing automations:\n   {existing_info}")
            
            # Get the user's linked accounts for the specified apps
            app_names = automation_data.app_names
            linked_accounts = []
            logger.info(f"[AUTOMATION_TOOL] Checking linked accounts for apps: {app_names}")
            
            for app_name in app_names:
                linked_account = linked_accounts_crud.get_linked_account(
                    self.db_session, self.user_id, app_name
                )
                if not linked_account:
                    logger.error(f"[AUTOMATION_TOOL] Missing linked account for app: {app_name}")
                    return f"Error: You don't have the '{app_name}' app connected. Please connect this app first before creating the automation."
                linked_accounts.append(linked_account)
                logger.info(f"[AUTOMATION_TOOL] Found linked account for {app_name}: {linked_account.id}")
            
            # Convert to the standard AutomationCreate schema
            automation_create = AutomationCreate(
                name=automation_data.name,
                description=automation_data.description,
                goal=automation_data.goal,
                is_deep=automation_data.is_deep,
                active=automation_data.active,
                linked_account_ids=[la.id for la in linked_accounts],
                is_recurring=automation_data.is_recurring,
                cron_schedule=automation_data.cron_schedule
            )
            
            logger.info(f"[AUTOMATION_TOOL] Creating automation with linked accounts: {[la.id for la in linked_accounts]}")
            
            # Create the automation
            automation = crud.automations.create_automation(
                self.db_session, self.user_id, automation_create
            )
            
            logger.info(f"[AUTOMATION_TOOL] Successfully created automation '{automation.name}' with ID: {automation.id}")
            
            result = f"Successfully created automation '{automation.name}' (ID: {automation.id}). The automation is {'active and will run on schedule' if automation.is_recurring else 'ready to be triggered manually'}."
            logger.info(f"[AUTOMATION_TOOL] create_automation result: {result}")
            return result
            
        except ValueError as e:
            logger.error(f"[AUTOMATION_TOOL] ValueError in create_automation: {str(e)}")
            return f"Error creating automation: {str(e)}"
        except Exception as e:
            logger.error(f"[AUTOMATION_TOOL] Unexpected error in create_automation: {str(e)}", exc_info=True)
            return f"An unexpected error occurred while creating the automation: {str(e)}"

    @function_tool(raw_schema={
        "type": "function", 
        "name": "get_automation_runs",
        "description": "Retrieves the execution history for a specific automation to see how it has performed.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {
                    "type": "string",
                    "description": "The ID of the automation to get runs for"
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum number of runs to retrieve (1-100)"
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "success", "failed"],
                    "description": "Filter runs by status (optional)"
                }
            },
            "required": ["automation_id"]
        }
    })
    async def get_automation_runs(
        self,
        raw_arguments: dict[str, object], 
        context: RunContext
    ):
        """
        Retrieves the execution history for a specific automation.
        
        This tool shows you how an automation has been performing, including:
        - When it ran
        - Whether it succeeded or failed
        - Any error messages or logs
        - Files or artifacts created during runs
        
        Use this to troubleshoot automations or check their performance history.
        """
        logger.info(f"[AUTOMATION_TOOL] get_automation_runs called by user {self.user_id}")
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")
        
        try:
            from aci.common.db import crud
            from aci.common.enums import RunStatus
            
            automation_id = str(raw_arguments["automation_id"])
            logger.info(f"[AUTOMATION_TOOL] Getting runs for automation ID: {automation_id}")
            
            # Handle limit parameter with proper type checking
            limit_value = raw_arguments.get("limit", 10)
            if isinstance(limit_value, (int, float)):
                limit = min(int(limit_value), 100)
            else:
                limit = 10
                
            status_str = str(raw_arguments.get("status")) if raw_arguments.get("status") else None
            logger.info(f"[AUTOMATION_TOOL] Query parameters - limit: {limit}, status: {status_str}")
            
            # Verify the automation exists and belongs to the user
            automation = crud.automations.get_automation(self.db_session, automation_id)
            if not automation:
                logger.warning(f"[AUTOMATION_TOOL] Automation not found: {automation_id}")
                return f"Error: Automation with ID '{automation_id}' not found."
            
            if automation.user_id != self.user_id:
                logger.warning(f"[AUTOMATION_TOOL] Access denied for automation {automation_id} by user {self.user_id}")
                return f"Error: You don't have access to automation '{automation_id}'."
            
            logger.info(f"[AUTOMATION_TOOL] Verified access to automation '{automation.name}' (ID: {automation_id})")
            
            # Convert status string to enum if provided
            status = None
            if status_str:
                try:
                    status = RunStatus(status_str)
                    logger.info(f"[AUTOMATION_TOOL] Filtering by status: {status}")
                except ValueError:
                    logger.error(f"[AUTOMATION_TOOL] Invalid status value: {status_str}")
                    return f"Error: Invalid status '{status_str}'. Valid options are: pending, in_progress, success, failed"
            
            # Get the runs
            runs = crud.automation_runs.list_runs_for_automation(
                self.db_session, automation_id, limit, 0, status
            )
            logger.info(f"[AUTOMATION_TOOL] Retrieved {len(runs)} runs for automation {automation_id}")
            
            if not runs:
                status_filter = f" with status '{status_str}'" if status_str else ""
                result = f"No runs found for automation '{automation.name}'{status_filter}."
                logger.info(f"[AUTOMATION_TOOL] No runs found - returning: {result}")
                return result
            
            # Format the response
            response_lines = [f"Automation: {automation.name}"]
            response_lines.append(f"Total runs shown: {len(runs)}")
            response_lines.append("")
            
            logger.info(f"[AUTOMATION_TOOL] Formatting response for {len(runs)} runs")
            
            for i, run in enumerate(runs, 1):
                response_lines.append(f"{i}. Run ID: {run.id}")
                response_lines.append(f"   Status: {run.status.value}")
                response_lines.append(f"   Started: {run.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                
                if run.finished_at:
                    duration = (run.finished_at - run.started_at).total_seconds()
                    response_lines.append(f"   Duration: {duration:.1f} seconds")
                
                if run.message:
                    # Truncate long messages
                    message = run.message[:200] + "..." if len(run.message) > 200 else run.message
                    response_lines.append(f"   Message: {message}")
                
                if hasattr(run, 'artifacts') and run.artifacts:
                    response_lines.append(f"   Artifacts: {len(run.artifacts)} files created")
                
                response_lines.append("")  # Empty line between runs
            
            result = "\n".join(response_lines)
            logger.info(f"[AUTOMATION_TOOL] get_automation_runs completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"[AUTOMATION_TOOL] Error in get_automation_runs: {str(e)}", exc_info=True)
            return f"An unexpected error occurred while retrieving automation runs: {str(e)}"

    @function_tool(raw_schema={
        "type": "function",
        "name": "list_user_automations", 
        "description": "Lists all automations created by the user with their current status.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Maximum number of automations to retrieve (1-100)"
                }
            }
        }
    })
    async def list_user_automations(
        self,
        raw_arguments: dict[str, object],
        context: RunContext  
    ):
        """
        Lists all automations created by the user.
        
        This tool shows you all your automations with their current status, 
        including when they last ran and whether they're active or not.
        """
        logger.info(f"[AUTOMATION_TOOL] list_user_automations called by user {self.user_id}")
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")
        
        try:
            from aci.common.db import crud
            
            # Handle limit parameter with proper type checking
            limit_value = raw_arguments.get("limit", 20)
            if isinstance(limit_value, (int, float)):
                limit = min(int(limit_value), 100)
            else:
                limit = 20
            
            logger.info(f"[AUTOMATION_TOOL] Listing automations with limit: {limit}")
            
            automations = crud.automations.list_user_automations(
                self.db_session, self.user_id, limit, 0
            )
            
            logger.info(f"[AUTOMATION_TOOL] Found {len(automations)} automations for user {self.user_id}")
            
            if not automations:
                result = "You haven't created any automations yet. Use the create_automation tool to create your first one!"
                logger.info(f"[AUTOMATION_TOOL] No automations found - returning: {result}")
                return result
            
            response_lines = [f"Your Automations ({len(automations)} total):"]
            response_lines.append("")
            
            logger.info(f"[AUTOMATION_TOOL] Formatting response for {len(automations)} automations")
            
            for i, automation in enumerate(automations, 1):
                response_lines.append(f"{i}. {automation.name}")
                response_lines.append(f"   ID: {automation.id}")
                response_lines.append(f"   Status: {'Active' if automation.active else 'Inactive'}")
                response_lines.append(f"   Type: {'Recurring' if automation.is_recurring else 'Manual'}")
                
                if automation.is_recurring and automation.cron_schedule:
                    response_lines.append(f"   Schedule: {automation.cron_schedule} (UTC)")
                
                if automation.last_run_at:
                    response_lines.append(f"   Last run: {automation.last_run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                    response_lines.append(f"   Last status: {automation.last_run_status.value}")
                else:
                    response_lines.append("   Never run")
                
                if automation.description:
                    desc = automation.description[:100] + "..." if len(automation.description) > 100 else automation.description
                    response_lines.append(f"   Description: {desc}")
                
                response_lines.append("")  # Empty line between automations
            
            result = "\n".join(response_lines)
            logger.info(f"[AUTOMATION_TOOL] list_user_automations completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"[AUTOMATION_TOOL] Error in list_user_automations: {str(e)}", exc_info=True)
            return f"An unexpected error occurred while retrieving your automations: {str(e)}"

    @function_tool(raw_schema={
        "type": "function",
        "name": "run_automation",
        "description": "Manually triggers an automation to run immediately, regardless of its schedule.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {
                    "type": "string",
                    "description": "The ID of the automation to run"
                }
            },
            "required": ["automation_id"]
        }
    })
    async def run_automation(
        self,
        raw_arguments: dict[str, object],
        context: RunContext
    ):
        """
        Manually triggers an automation to run immediately.
        
        This tool allows you to run an automation outside of its scheduled time,
        which is useful for:
        - Testing new automations
        - Running one-time tasks
        - Manually triggering recurring automations
        
        The automation will be executed asynchronously and you can check its 
        progress using the get_automation_runs tool.
        """
        logger.info(f"[AUTOMATION_TOOL] run_automation called by user {self.user_id}")
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")
        
        try:
            from aci.common.db import crud
            from aci.server.tasks.tasks import execute_automation
            
            automation_id = str(raw_arguments["automation_id"])
            logger.info(f"[AUTOMATION_TOOL] Attempting to run automation ID: {automation_id}")
            
            # Verify the automation exists and belongs to the user
            automation = crud.automations.get_automation(self.db_session, automation_id)
            if not automation:
                logger.warning(f"[AUTOMATION_TOOL] Automation not found: {automation_id}")
                return f"Error: Automation with ID '{automation_id}' not found."
            
            if automation.user_id != self.user_id:
                logger.warning(f"[AUTOMATION_TOOL] Access denied for automation {automation_id} by user {self.user_id}")
                return f"Error: You don't have access to automation '{automation_id}'."
            
            if not automation.active:
                logger.warning(f"[AUTOMATION_TOOL] Attempt to run inactive automation: {automation_id}")
                return f"Error: Automation '{automation.name}' is currently inactive. Please activate it first."
            
            logger.info(f"[AUTOMATION_TOOL] Verified automation '{automation.name}' (ID: {automation_id}) is ready to run")
            
            # Create a new run record
            automation_run = crud.automation_runs.create_run(self.db_session, automation_id)
            logger.info(f"[AUTOMATION_TOOL] Created automation run with ID: {automation_run.id}")
            
            # Commit the run creation to the database
            self.db_session.commit()
            logger.info(f"[AUTOMATION_TOOL] Committed run creation to database")
            
            # Queue the automation for execution
            execute_automation(automation_run.id)
            logger.info(f"[AUTOMATION_TOOL] Queued automation {automation_id} for execution (run ID: {automation_run.id})")
            
            result = f"Successfully started automation '{automation.name}' (Run ID: {automation_run.id}). The automation is now running in the background. You can check its progress using the get_automation_runs tool."
            logger.info(f"[AUTOMATION_TOOL] run_automation completed successfully: {result}")
            return result
            
        except Exception as e:
            logger.error(f"[AUTOMATION_TOOL] Error in run_automation: {str(e)}", exc_info=True)
            return f"An unexpected error occurred while starting the automation: {str(e)}"

    @function_tool(raw_schema={
        "type": "function",
        "name": "get_current_session_usage",
        "description": "Shows the current voice session usage metrics including time spent and processing costs.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
        }
    })
    async def get_current_session_usage(
        self,
        raw_arguments: dict[str, object],
        context: RunContext
    ):
        """
        Shows the current voice session usage metrics.
        
        This tool provides real-time information about the current session including:
        - Session duration so far
        - LLM tokens used
        - Speech-to-text processing time
        - Text-to-speech characters generated
        """
        try:
            # This would need to be implemented with session state tracking
            # For now, we can query the current month's usage
            from aci.common.db.crud import usage as usage_crud
            
            current_usage = usage_crud.get_current_month_usage(self.db_session, self.user_id)
            
            if not current_usage:
                return "No usage data found for this month yet."
            
            response_lines = [
                "Your Current Month Usage:",
                f"Voice Agent Time: {current_usage.voice_agent_minutes:.1f} minutes",
                f"Automation Runs: {current_usage.automation_runs_count}",
                "",
                "Processing Metrics:",
                f"LLM Tokens: {current_usage.llm_tokens_used:,}",
                f"Speech Processing: {current_usage.stt_audio_minutes:.1f} minutes", 
                f"Voice Generation: {current_usage.tts_characters_used:,} characters"
            ]
            
            return "\n".join(response_lines)
            
        except Exception as e:
            logger.error(f"Error getting session usage: {str(e)}", exc_info=True)
            return f"Sorry, I couldn't retrieve your usage information right now."


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

    # Track session start time for voice minutes calculation
    session_start_time = time()
    usage_recorded = False  # Prevent duplicate usage recording
    
    # Track metrics in real-time as they occur
    session_metrics = {
        'llm_tokens': 0,
        'stt_duration': 0.0,
        'tts_characters': 0
    }
    
    logger.info(f"Session started for user {user_id} at {session_start_time}")

    # Function to save usage metrics when user disconnects
    def save_usage_metrics():
        nonlocal usage_recorded
        if usage_recorded:
            logger.info(f"Usage already recorded for user {user_id}, skipping")
            return
            
        usage_recorded = True
        session_end_time = time()
        session_duration_minutes = (session_end_time - session_start_time) / 60
        
        logger.info(f"User {user_id} disconnected. Session duration: {session_duration_minutes:.2f} minutes")
        
        # Use real-time accumulated metrics first, fall back to usage_collector if needed
        usage_summary = usage_collector.get_summary()
        
        # If real-time tracking didn't capture anything but usage_collector did, use that as fallback
        if session_metrics['llm_tokens'] == 0 and session_metrics['stt_duration'] == 0 and session_metrics['tts_characters'] == 0:
            if usage_summary.llm_prompt_tokens > 0 or usage_summary.llm_completion_tokens > 0 or usage_summary.stt_audio_duration > 0 or usage_summary.tts_characters_count > 0:
                logger.warning(f"Real-time metrics were zero but usage_collector has data - using fallback for user {user_id}")
                session_metrics['llm_tokens'] = usage_summary.llm_prompt_tokens + usage_summary.llm_completion_tokens + usage_summary.llm_prompt_cached_tokens
                session_metrics['stt_duration'] = usage_summary.stt_audio_duration
                session_metrics['tts_characters'] = usage_summary.tts_characters_count
        
        logger.info(f"Final session metrics for user {user_id}: "
                   f"LLM tokens: {session_metrics['llm_tokens']}, "
                   f"STT duration: {session_metrics['stt_duration']:.2f}s, "
                   f"TTS characters: {session_metrics['tts_characters']}")
        
        logger.info(f"UsageCollector summary for comparison: "
                   f"LLM tokens: {usage_summary.llm_prompt_tokens + usage_summary.llm_completion_tokens + usage_summary.llm_prompt_cached_tokens}, "
                   f"STT duration: {usage_summary.stt_audio_duration:.2f}s, "
                   f"TTS characters: {usage_summary.tts_characters_count}")
        
        # Save all usage metrics to database
        try:
            with create_db_session(DB_FULL_URL) as db_session:
                # Save voice session minutes
                if session_duration_minutes > 0:
                    usage_crud.increment_voice_minutes(db_session, user_id, session_duration_minutes)
                    logger.info(f"Recorded {session_duration_minutes:.2f} voice minutes for user {user_id}")
                
                # Save real-time usage metrics using bulk increment for efficiency
                if session_metrics['llm_tokens'] > 0 or session_metrics['stt_duration'] > 0 or session_metrics['tts_characters'] > 0:
                    stt_minutes = session_metrics['stt_duration'] / 60  # Convert seconds to minutes
                    
                    usage_crud.increment_usage_from_livekit_metrics(
                        db_session, 
                        user_id,
                        llm_tokens=session_metrics['llm_tokens'],
                        stt_minutes=stt_minutes,
                        tts_characters=session_metrics['tts_characters']
                    )
                    
                    logger.info(f"Recorded final metrics for user {user_id}: "
                               f"{session_metrics['llm_tokens']} LLM tokens, "
                               f"{stt_minutes:.2f} STT minutes, "
                               f"{session_metrics['tts_characters']} TTS characters")
                
        except Exception as e:
            logger.error(f"Error saving usage metrics for user {user_id}: {str(e)}", exc_info=True)

    # Listen for user disconnect events
    def on_participant_disconnected(participant):
        if participant.identity == user_id:
            logger.info(f"User {user_id} disconnected from room, saving usage metrics")
            save_usage_metrics()

    # Listen for room disconnect events as fallback
    def on_room_disconnected(reason):
        logger.info(f"Room disconnected (reason: {reason}), saving usage metrics for user {user_id}")
        save_usage_metrics()
    
    # Listen for connection state changes for additional robustness  
    def on_connection_state_changed(state):
        if state == ConnectionState.CONN_DISCONNECTED:
            logger.info(f"Connection disconnected, saving usage metrics for user {user_id}")
            save_usage_metrics()

    # Register event listeners
    ctx.room.on("participant_disconnected", on_participant_disconnected)
    ctx.room.on("disconnected", on_room_disconnected)
    ctx.room.on("connection_state_changed", on_connection_state_changed)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
        max_tool_steps=4,
    )

    logger.info(f"Registering metrics_collected callback for user {user_id}")
    
    # Use the correct decorator syntax for metrics collection
    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        logger.info(f"Metrics collected callback triggered: {type(ev.metrics).__name__}")
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)
        
        # Accumulate metrics in real-time based on metric type
        if isinstance(ev.metrics, metrics.LLMMetrics):
            session_metrics['llm_tokens'] += ev.metrics.completion_tokens + ev.metrics.prompt_tokens + ev.metrics.prompt_cached_tokens
            logger.info(f"LLM metrics: +{ev.metrics.completion_tokens + ev.metrics.prompt_tokens + ev.metrics.prompt_cached_tokens} tokens (total: {session_metrics['llm_tokens']})")
        elif isinstance(ev.metrics, metrics.STTMetrics):
            session_metrics['stt_duration'] += ev.metrics.audio_duration
            logger.info(f"STT metrics: +{ev.metrics.audio_duration:.2f}s (total: {session_metrics['stt_duration']:.2f}s)")
        elif isinstance(ev.metrics, metrics.TTSMetrics):
            session_metrics['tts_characters'] += ev.metrics.characters_count
            logger.info(f"TTS metrics: +{ev.metrics.characters_count} characters (total: {session_metrics['tts_characters']})")
        elif isinstance(ev.metrics, metrics.RealtimeModelMetrics):
            session_metrics['llm_tokens'] += ev.metrics.input_tokens + ev.metrics.output_tokens
            logger.info(f"Realtime model metrics: +{ev.metrics.input_tokens + ev.metrics.output_tokens} tokens (total: {session_metrics['llm_tokens']})")

    # Add shutdown callback to log usage summary
    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Final usage summary: {summary}")

    ctx.add_shutdown_callback(log_usage)

    try:
        await session.start(
            room=ctx.room,
            agent=Assistant(user_id=user_id),
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        )
    except Exception as e:
        logger.error(f"Error in session for user {user_id}: {str(e)}", exc_info=True)
        # Only save usage on actual errors, not normal disconnections
        if not usage_recorded:
            logger.info(f"Session error occurred, saving usage metrics for user {user_id}")
            save_usage_metrics()
        raise


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
