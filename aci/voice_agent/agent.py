import asyncio
import json
import logging
from datetime import datetime, timezone
from time import time
from typing import cast, Optional
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
from aci.common.enums import FunctionDefinitionFormat
from aci.common.schemas.function import OpenAIFunctionDefinition
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


def validate_html(html_content: str) -> None:
    """
    Validates that the provided HTML content is well-formed.
    
    Args:
        html_content: The HTML string to validate
        
    Raises:
        ToolError: If the HTML is malformed or invalid
    """
    from html.parser import HTMLParser
    from html import unescape
    
    class HTMLValidator(HTMLParser):
        def __init__(self):
            super().__init__()
            self.errors = []
            
        def error(self, message):
            self.errors.append(message)
    
    # Unescape HTML entities first
    try:
        unescaped_html = unescape(html_content)
    except Exception as e:
        raise ToolError(f"Invalid HTML entities: {str(e)}")
    
    # Parse the HTML
    validator = HTMLValidator()
    try:
        validator.feed(unescaped_html)
        validator.close()
    except Exception as e:
        raise ToolError(f"HTML parsing error: {str(e)}")
    
    # Check for any parsing errors
    if validator.errors:
        raise ToolError(f"HTML validation errors: {'; '.join(validator.errors)}")
    
    # Basic checks
    if not html_content.strip():
        raise ToolError("HTML content cannot be empty")
    
    # Check for basic HTML structure (at least one tag)
    if '<' not in html_content or '>' not in html_content:
        raise ToolError("HTML content must contain at least one HTML tag")


class Assistant(Agent):
    _restricted_apps = {"browser"}

    def __init__(self, user_id: str) -> None:
        self.db_session = create_db_session(DB_FULL_URL)
        self.user_id = user_id
        
        # Auto-create linked accounts for essential apps if they don't exist
        essential_apps = ["SEARXNG", "NOTIFYME"]
        for app_name in essential_apps:
            try:
                # Check if linked account already exists
                existing_linked_account = crud.linked_accounts.get_linked_account(
                    self.db_session, user_id, app_name.upper()
                )
                
                if not existing_linked_account:
                    # Get the app from database
                    app = crud.apps.get_app(self.db_session, app_name, active_only=True)
                    if app and app.has_default_credentials:
                        # Create linked account with default credentials
                        from aci.common.enums import SecurityScheme
                        from aci.common.schemas.security_scheme import NoAuthSchemeCredentials
                        
                        # Use NO_AUTH security scheme with empty credentials for default apps
                        crud.linked_accounts.create_linked_account(
                            self.db_session,
                            user_id,
                            app_name,
                            SecurityScheme.NO_AUTH,
                            NoAuthSchemeCredentials(),
                        )
                        self.db_session.commit()
                        logger.info(f"Auto-created linked account for {app_name} for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to auto-create linked account for {app_name}: {e}")
                # Don't fail initialization if auto-creation fails
                self.db_session.rollback()
        
        # Fetch user's linked apps to inject into prompt
        user_app_names = crud.apps.get_user_linked_app_names(self.db_session, user_id)
        # Filter out restricted apps
        user_app_names = [name for name in user_app_names if name.lower() not in self._restricted_apps]
        linked_apps_str = ", ".join(user_app_names) if user_app_names else "No apps connected yet"
        self.linked_apps_str = linked_apps_str

        super().__init__(
            instructions=f"""
Autom8 AI assistant. Today: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} UTC.

Voice: Brief (1-2 sentences), conversational, summarize results. Match user's language.

YOUR CONNECTED APPS: {linked_apps_str}
Use execute_function with functions from these apps only. If user asks about an app not in this list, tell them it's not connected.
If user asks "what apps do I have?", just tell them from the list above - no need to call get_linked_apps.

CRITICAL - EXTREME TOOL CALL LIMITS:
- MAXIMUM 1 tool call per response unless absolutely necessary
- System has hard limit - too many calls = failure
- You already know your connected apps (see above) - NO need for get_linked_apps unless user explicitly asks
- If you need function details, call get_app_info ONLY if necessary
- Better: Try execute_function directly, it will tell you if parameters are wrong

WRONG APPROACH (causes failures):
❌ get_linked_apps → get_app_info → execute_function (3 calls!)
❌ get_app_info → execute_function (2 calls - still too many)

CORRECT APPROACH:
✅ Just call execute_function directly (1 call)
✅ "latest news" → execute_function(SEARXNG__SEARCH_GENERAL, {{"query": "latest news"}})
✅ "send notification" → execute_function(NOTIFYME__SEND_ME_NOTIFICATION, {{"message": "text"}})
✅ "cricket scores" → execute_function(CRICBUZZ__GET_LIVE_MATCHES, {{}})

AGENT TOOLS:
- execute_function: Run app functions from your connected apps
- create_automation: Create recurring/scheduled tasks (check list_user_automations FIRST to avoid duplicates!)
- list_user_automations: Check existing automations BEFORE creating new ones
- update_automation: Modify existing automation settings
- get_user_timezone: Get timezone for scheduling
- get_linked_apps: Return connected apps (rarely needed - you already know them above)
- get_app_info: ONLY if you need to know function parameters
- display_mini_app: Show HTML tools

AUTOMATION WORKFLOW:
Before creating automation: Call list_user_automations to check if similar one exists
If duplicate found: Suggest using existing one or updating it instead

REAL-TIME DATA:
- NEVER say "I don't have current info" or "knowledge cutoff"
- Search/news → execute_function(SEARXNG__SEARCH_GENERAL, {{"query": "...", "num_results": 5}})
- Notifications → execute_function(NOTIFYME__SEND_ME_EMAIL or NOTIFYME__SEND_ME_NOTIFICATION)

SECURITY VALIDATION RULES:
- ALWAYS validate URLs before using them in any tool calls
- BLOCK any URLs that are localhost, private IPs (192.168.x.x, 10.x.x.x, 172.16-31.x.x), or internal services
- BLOCK dangerous schemes like file://, javascript:, data:, or custom protocols
- BLOCK suspicious patterns like encoded payloads or unusual characters
- ONLY allow HTTP/HTTPS URLs pointing to legitimate public websites
- If a URL fails security validation, explain the security concern and suggest alternatives
- Be extremely conservative with any URL-based operations - when in doubt, reject

Timezone: Call get_user_timezone, convert to UTC, explain conversion.
Voice: Brief (1-2 sentences), conversational, summarize results. Match user's language.
""",
            stt=mistralai.STT(model="voxtral-mini-latest", api_key=MISTRALAI_API_KEY),
            llm=openai.LLM.with_x_ai(
                model="grok-4-fast-non-reasoning-latest",
                temperature=0,
                api_key=XAI_API_KEY
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

    async def _notify_tool_used(self, tool_name: str, display_name: Optional[str] = None) -> None:
        """
        Sends a notification to the frontend when a tool is being used.
        
        Args:
            tool_name: The name of the tool being used
            display_name: Optional user-friendly name to display instead of tool_name
        """
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
                response_timeout=5,  # Shorter timeout for notifications
            )
        except Exception as e:
            # Don't fail the tool execution if notification fails
            logger.warning(f"Failed to send tool usage notification: {e}")

    @staticmethod
    def _tool_notification_wrapper(tool_name: str, display_name: Optional[str] = None):
        """
        Context manager factory to wrap tool functions with usage notifications.
        
        Args:
            tool_name: The name of the tool being used
            display_name: Optional user-friendly name to display instead of tool_name
        """
        from contextlib import asynccontextmanager
        
        @asynccontextmanager
        async def context_manager(self_ref):
            # Send tool started notification
            await self_ref._notify_tool_used(tool_name, display_name=display_name)
            
            try:
                yield
            except Exception as e:
                # Send tool completed notification with failure
                await self_ref._notify_tool_completed(tool_name, success=False, error=str(e), display_name=display_name)
                raise
            else:
                # Send tool completed notification
                await self_ref._notify_tool_completed(tool_name, success=True, display_name=display_name)
        
        return context_manager

    async def _notify_tool_completed(self, tool_name: str, success: bool, error: Optional[str] = None, display_name: Optional[str] = None) -> None:
        """
        Sends a notification to the frontend when a tool execution is completed.
        
        Args:
            tool_name: The name of the tool that was used
            success: Whether the tool execution was successful
            error: Error message if the tool failed
            display_name: Optional user-friendly name to display instead of tool_name
        """
        try:
            room = get_job_context().room
            participant_identity = next(iter(room.remote_participants))

            name_to_show = display_name or tool_name
            if success:
                message = f"AI completed using {name_to_show}"
            else:
                message = f"AI encountered an issue with {name_to_show}"

            await room.local_participant.perform_rpc(
                destination_identity=participant_identity,
                method="toolUseCompleted",
                payload=json.dumps({
                    "message": message,
                    "tool_name": tool_name,
                    "success": success,
                    "error": error
                }),
                response_timeout=5,  # Shorter timeout for notifications
            )
        except Exception as e:
            # Don't fail if notification fails
            logger.warning(f"Failed to send tool completion notification: {e}")

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

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "execute_function",
            "description": "Executes a specific function from a connected app with provided parameters. Use this to directly call any available function after checking get_app_info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "The exact name of the function to execute (e.g., 'CRICBUZZ__GET_LIVE_MATCHES', 'NOTIFYME__SEND_ME_EMAIL')",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "The parameters required by the function as a dictionary/object. Check get_app_info for required parameters.",
                        "additionalProperties": True,
                    },
                },
                "required": ["function_name", "parameters"],
            },
        }
    )
    async def execute_function_tool(
        self, raw_arguments: dict[str, object], context: RunContext
    ):
        """
        Executes a specific function from a connected app with provided parameters.

        This tool allows you to directly call any available function from the user's
        connected apps. First use get_app_info to see available functions and their
        parameters, then call this function with the function name and required parameters.

        Args:
            function_name: The exact name of the function to execute (e.g., 'CRICBUZZ__GET_LIVE_MATCHES')
            parameters: A dictionary of parameters required by the function

        Returns:
            The result of the function execution as a JSON string.
        """
        logger.info(f"execute_function called with arguments: {raw_arguments}")
        
        try:
            function_name = str(raw_arguments["function_name"])
            # Convert parameters to dict, handling the object type from raw_arguments
            parameters_obj = raw_arguments.get("parameters", {})
            if isinstance(parameters_obj, dict):
                parameters = parameters_obj
            else:
                parameters = {}
            
            # Parse function name to get user-friendly display name
            if "__" in function_name:
                # Format: APP_NAME__FUNCTION_NAME -> "function name"
                app_name, func_part = function_name.split("__", 1)
                # Convert camelCase/PascalCase to readable text
                display_name = func_part.replace("_", " ").lower()
                # Capitalize first letter
                display_name = display_name[0].upper() + display_name[1:] if display_name else display_name
            else:
                display_name = function_name.replace("_", " ").lower()
            
            # Send tool started notification with the cleaned function name
            await self._notify_tool_used("execute_function", display_name=f"{display_name}")
            
            logger.info(f"Executing function: {function_name} with parameters: {parameters}")
            
            # Use the existing execute_function from function_utils
            try:
                result = await asyncio.to_thread(
                    execute_function,
                    db_session=self.db_session,
                    function_name=function_name,
                    user_id=self.user_id,
                    function_input=parameters,
                    run_id=None,
                )
            except Exception as e:
                logger.error(f"Error executing function {function_name}: {e}", exc_info=True)
                raise ToolError(f"Error executing function: {str(e)}") from e
            
            logger.info(f"Function {function_name} executed successfully")
            
            # Format the result
            if result.success:
                # Send success notification
                await self._notify_tool_completed("execute_function", success=True, display_name=f"{display_name}")
                return json.dumps({
                    "success": True,
                    "data": result.data,
                })
            else:
                # Send failure notification
                await self._notify_tool_completed("execute_function", success=False, error=result.error, display_name=f"{display_name}")
                return json.dumps({
                    "success": False,
                    "error": result.error,
                })
            
        except KeyError as e:
            logger.error(f"Missing required argument in execute_function: {e}")
            await self._notify_tool_completed("execute_function", success=False, error=f"Missing required argument: {str(e)}", display_name="function execution")
            return json.dumps({
                "success": False,
                "error": f"Missing required argument: {str(e)}"
            })
        except Exception as e:
            logger.error(f"Error in execute_function: {e}", exc_info=True)
            await self._notify_tool_completed("execute_function", success=False, error=f"Error executing function: {str(e)}", display_name="function execution")
            return json.dumps({
                "success": False,
                "error": f"Error executing function: {str(e)}"
            })

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "display_mini_app",
            "description": "Renders a self-contained mini-application (HTML, CSS, JS) on the user's frontend.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_title": {
                        "type": "string",
                        "description": "A short, descriptive title for the application (e.g., 'BMI Calculator').",
                        "default": "Mini App",
                    },
                    "html_content": {
                        "type": "string",
                        "description": "The complete HTML string for the application, including all necessary CSS and JavaScript.",
                    },
                },
                "required": ["html_content"],
            },
        }
    )
    async def display_mini_app(
        self, raw_arguments: dict[str, object], context: RunContext
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
            # Validate HTML content
            html_content = str(raw_arguments["html_content"])
            validate_html(html_content)
            
            async with self._tool_notification_wrapper("display_mini_app", "Display mini app")(self):
                room = get_job_context().room
                participant_identity = next(iter(room.remote_participants))

                # The frontend client must have a listener for the "displayMiniApp" method
                await room.local_participant.perform_rpc(
                    destination_identity=participant_identity,
                    method="displayMiniApp",
                    payload=json.dumps(
                        {
                            "title": raw_arguments.get("app_title", "Mini App"),
                            "html": html_content,
                        }
                    ),
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
    async def get_linked_apps(self) -> str:
        """Fetch and return the names of apps linked to the user's account."""
        async with self._tool_notification_wrapper("get_linked_apps", "Get linked apps")(self):
            return self.linked_apps_str

    @function_tool()
    async def get_app_info(self, app_names: list[str]) -> str:
        """Fetch and return detailed information about specified apps and their functions."""
        logger.info(f"Agent requested info for apps: {app_names}")
        if len(app_names) > 3:
            return json.dumps(
                {"error": "You can request info for up to 3 apps at a time."}
            )
        async with self._tool_notification_wrapper("get_app_info", "Get app info")(self):
            try:
                # Normalize app names to uppercase for case-insensitive lookup
                normalized_app_names = [name.upper() for name in app_names]
                
                # Use the new CRUD function to get apps with their functions pre-loaded
                apps = crud.apps.get_apps_with_functions_by_names(
                    self.db_session, normalized_app_names
                )
                if not apps:
                    return json.dumps({"error": f"No apps found with names: {app_names}"})

                # Build the detailed response
                response_data = []
                for app in apps:
                    if app.name.lower() in self._restricted_apps:
                        continue

                    response_data.append(
                        {
                            "name": app.name,
                            "description": app.description,
                            "functions": [
                                {
                                    "name": func.name,
                                    "description": func.description,
                                    "parameters": cast(
                                        OpenAIFunctionDefinition,
                                        format_function_definition(
                                            func, FunctionDefinitionFormat.OPENAI
                                        ),
                                    ).function.parameters,
                                }
                                for func in app.functions
                                if func.active
                            ],
                        }
                    )
                return json.dumps(response_data)
            except Exception as e:
                logger.error(f"Error in get_app_info: {e}", exc_info=True)
                return json.dumps({"error": "An internal error occurred."})

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "create_automation",
            "description": "Creates a new automation that can perform tasks using available apps. Only create automations for tasks that can be accomplished with the available tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "A clear, descriptive name for the automation (max 255 characters)",
                    },
                    "description": {
                        "type": "string",
                        "description": "A brief description of what this automation does",
                    },
                    "goal": {
                        "type": "string",
                        "description": "The specific goal or instruction for the automation - what exactly should it accomplish?",
                    },
                    "app_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of app names required for this automation (e.g., ['gmail', 'google_calendar', 'notifyme'])",
                    },
                    "is_deep": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set to true for complex automations that require multiple steps",
                    },
                    "is_recurring": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether this automation should run on a schedule",
                    },
                    "cron_schedule": {
                        "type": "string",
                        "description": "UTC cron schedule (e.g., '0 9 * * 1' for every Monday at 9 AM UTC). Required if is_recurring is true. Minimum interval is 30 minutes.",
                    },
                },
                "required": ["name", "goal", "app_names"],
            },
        }
    )
    async def create_automation(
        self, raw_arguments: dict[str, object], context: RunContext
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
        logger.info(
            f"[AUTOMATION_TOOL] create_automation called by user {self.user_id}"
        )
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")

        async with self._tool_notification_wrapper("create_automation", "Create automation")(self):
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
                    "app_names": (
                        [str(app) for app in raw_arguments["app_names"]]
                        if isinstance(raw_arguments["app_names"], list)
                        else []
                    ),
                    "description": (
                        str(raw_arguments.get("description"))
                        if raw_arguments.get("description")
                        else None
                    ),
                    "is_deep": bool(raw_arguments.get("is_deep", False)),
                    "is_recurring": bool(raw_arguments.get("is_recurring", False)),
                    "cron_schedule": (
                        str(raw_arguments.get("cron_schedule"))
                        if raw_arguments.get("cron_schedule")
                        else None
                    ),
                }

                logger.info(f"[AUTOMATION_TOOL] Processed arguments: {automation_args}")

                # Create the automation schema
                automation_data = AutomationAgentCreate(**automation_args)
                logger.info(
                    f"[AUTOMATION_TOOL] Created automation schema for '{automation_data.name}'"
                )

                # Get the user's linked accounts for the specified apps
                app_names = automation_data.app_names
                linked_accounts = []
                logger.info(
                    f"[AUTOMATION_TOOL] Checking linked accounts for apps: {app_names}"
                )

                for app_name in app_names:
                    linked_account = linked_accounts_crud.get_linked_account(
                        self.db_session, self.user_id, app_name.upper()
                    )
                    if not linked_account:
                        logger.error(
                            f"[AUTOMATION_TOOL] Missing linked account for app: {app_name}"
                        )
                        return f"Error: You don't have the '{app_name}' app connected. Please connect this app first before creating the automation."
                    linked_accounts.append(linked_account)
                    logger.info(
                        f"[AUTOMATION_TOOL] Found linked account for {app_name}: {linked_account.id}"
                    )

                # Convert to the standard AutomationCreate schema
                automation_create = AutomationCreate(
                    name=automation_data.name,
                    description=automation_data.description,
                    goal=automation_data.goal,
                    is_deep=automation_data.is_deep,
                    active=automation_data.active,
                    linked_account_ids=[la.id for la in linked_accounts],
                    is_recurring=automation_data.is_recurring,
                    cron_schedule=automation_data.cron_schedule,
                )

                logger.info(
                    f"[AUTOMATION_TOOL] Creating automation with linked accounts: {[la.id for la in linked_accounts]}"
                )

                # Create the automation
                automation = crud.automations.create_automation(
                    self.db_session, self.user_id, automation_create
                )

                logger.info(
                    f"[AUTOMATION_TOOL] Successfully created automation '{automation.name}' with ID: {automation.id}"
                )

                result = f"Successfully created automation '{automation.name}' (ID: {automation.id}). The automation is {'active and will run on schedule' if automation.is_recurring else 'ready to be triggered manually'}."
                logger.info(f"[AUTOMATION_TOOL] create_automation result: {result}")
                return result

            except ValueError as e:
                logger.error(f"[AUTOMATION_TOOL] ValueError in create_automation: {str(e)}")
                return f"Error creating automation: {str(e)}"
            except Exception as e:
                logger.error(
                    f"[AUTOMATION_TOOL] Unexpected error in create_automation: {str(e)}",
                    exc_info=True,
                )
                return (
                    f"An unexpected error occurred while creating the automation: {str(e)}"
                )

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "list_user_automations",
            "description": "Lists all automations created by the user with their current status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Maximum number of automations to retrieve (1-100)",
                    }
                },
            },
        }
    )
    async def list_user_automations(
        self, raw_arguments: dict[str, object], context: RunContext
    ):
        """
        Lists all automations created by the user.

        This tool shows you all your automations with their current status,
        including when they last ran and whether they're active or not.
        """
        logger.info(
            f"[AUTOMATION_TOOL] list_user_automations called by user {self.user_id}"
        )
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")

        async with self._tool_notification_wrapper("list_user_automations", "List automations")(self):
            try:
                from aci.common.db import crud

                # Handle limit parameter with proper type checking
                limit_value = raw_arguments.get("limit", 20)
                if isinstance(limit_value, (int, float)):
                    limit = min(int(limit_value), 100)
                else:
                    limit = 20

                logger.info(f"[AUTOMATION_TOOL] Listing automations with limit: {limit}")

                # Use a fresh database session to avoid transaction issues
                with get_db_session() as fresh_db_session:
                    automations = crud.automations.list_user_automations(
                        fresh_db_session, self.user_id, limit, 0
                    )

                    logger.info(
                        f"[AUTOMATION_TOOL] Found {len(automations)} automations for user {self.user_id}"
                    )

                    if not automations:
                        return json.dumps({
                            "message": "You haven't created any automations yet.",
                            "automations": []
                        })

                    # Return simple JSON with just id, name, and schedule
                    automations_list = []
                    for automation in automations:
                        automations_list.append({
                            "id": automation.id,
                            "name": automation.name,
                            "schedule": automation.cron_schedule if automation.is_recurring else None
                        })

                    logger.info(
                        f"[AUTOMATION_TOOL] list_user_automations completed successfully"
                    )
                    return json.dumps({
                        "message": f"Found {len(automations)} automation(s)",
                        "automations": automations_list
                    })

            except Exception as e:
                logger.error(
                    f"[AUTOMATION_TOOL] Error in list_user_automations: {str(e)}",
                    exc_info=True,
                )
                return f"An unexpected error occurred while retrieving your automations: {str(e)}"

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "update_automation",
            "description": "Updates an existing automation's settings, including name, description, goal, schedule, or required apps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "The ID of the automation to update",
                    },
                    "name": {
                        "type": "string",
                        "description": "New name for the automation (optional)",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description for the automation (optional)",
                    },
                    "goal": {
                        "type": "string",
                        "description": "New goal or instruction for the automation (optional)",
                    },
                    "app_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New list of app names required for this automation (optional)",
                    },
                    "is_deep": {
                        "type": "boolean",
                        "description": "Whether this automation requires complex processing (optional)",
                    },
                    "active": {
                        "type": "boolean",
                        "description": "Whether the automation should be active or inactive (optional)",
                    },
                    "is_recurring": {
                        "type": "boolean",
                        "description": "Whether this automation should run on a schedule (optional)",
                    },
                    "cron_schedule": {
                        "type": "string",
                        "description": "New UTC cron schedule (optional). Required if changing is_recurring to true.",
                    },
                },
                "required": ["automation_id"],
            },
        }
    )
    async def update_automation(
        self, raw_arguments: dict[str, object], context: RunContext
    ):
        """
        Updates an existing automation's settings.
        
        Allows modifying name, description, goal, schedule, active status, and required apps.
        """
        logger.info(
            f"[AUTOMATION_TOOL] update_automation called by user {self.user_id}"
        )
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")

        async with self._tool_notification_wrapper("update_automation", "Update automation")(self):
            try:
                from aci.common.db import crud
                from aci.common.schemas.automations import AutomationUpdate

                automation_id = str(raw_arguments["automation_id"])
                
                # Use a fresh database session
                with get_db_session() as fresh_db_session:
                    # Verify the automation exists and belongs to the user
                    automation = crud.automations.get_automation(
                        fresh_db_session, automation_id
                    )
                    if not automation:
                        return f"Error: Automation with ID '{automation_id}' not found."

                    if automation.user_id != self.user_id:
                        return f"Error: You don't have access to automation '{automation_id}'."

                    # Build update data from provided arguments
                    update_data = {}
                    if "name" in raw_arguments:
                        update_data["name"] = str(raw_arguments["name"])
                    if "description" in raw_arguments:
                        update_data["description"] = str(raw_arguments["description"])
                    if "goal" in raw_arguments:
                        update_data["goal"] = str(raw_arguments["goal"])
                    if "active" in raw_arguments:
                        update_data["active"] = bool(raw_arguments["active"])
                    if "is_deep" in raw_arguments:
                        update_data["is_deep"] = bool(raw_arguments["is_deep"])
                    if "is_recurring" in raw_arguments:
                        update_data["is_recurring"] = bool(raw_arguments["is_recurring"])
                    if "cron_schedule" in raw_arguments:
                        update_data["cron_schedule"] = str(raw_arguments["cron_schedule"])
                    if "app_names" in raw_arguments:
                        update_data["linked_account_ids"] = []  # Handle app linking separately if needed

                    if not update_data:
                        return "Error: No fields provided to update."

                    # Create AutomationUpdate schema
                    automation_update = AutomationUpdate(**update_data)
                    
                    # Update the automation
                    updated_automation = crud.automations.update_automation(
                        fresh_db_session, automation_id, automation_update
                    )
                    fresh_db_session.commit()

                    logger.info(
                        f"[AUTOMATION_TOOL] Successfully updated automation {automation_id}"
                    )
                    return f"Successfully updated automation '{updated_automation.name}' (ID: {automation_id})."

            except ValueError as e:
                logger.error(f"[AUTOMATION_TOOL] ValueError in update_automation: {str(e)}")
                return f"Error updating automation: {str(e)}"
            except Exception as e:
                logger.error(
                    f"[AUTOMATION_TOOL] Unexpected error in update_automation: {str(e)}",
                    exc_info=True,
                )
                return f"An unexpected error occurred while updating the automation: {str(e)}"

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "get_user_timezone",
            "description": "Retrieves the user's local time and timezone information from their device/browser.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }
    )
    async def get_user_timezone(
        self, raw_arguments: dict[str, object], context: RunContext
    ):
        """
        Retrieves the user's local time and timezone information from their device/browser.

        This tool requests the user's current local time, timezone offset, and timezone name
        directly from their frontend client. This is useful for scheduling, time-based
        calculations, and providing accurate time references.

        Returns:
            A JSON string containing the user's timezone information including:
            - local_time: Current local time in ISO format
            - timezone_name: User's timezone name (e.g., "America/New_York")
            - timezone_offset: UTC offset in minutes
            - utc_time: Current UTC time for reference
        """
        async with self._tool_notification_wrapper("get_user_timezone", "Get user timezone")(self):
            try:
                room = get_job_context().room
                participant_identity = next(iter(room.remote_participants))

                # The frontend client must have a listener for the "getUserTimezone" method
                rpc_response = await room.local_participant.perform_rpc(
                    destination_identity=participant_identity,
                    method="getUserTimezone",
                    payload="{}",  # Empty payload since we don't need to send data
                    response_timeout=10,
                )

                # The frontend should send back timezone information as JSON
                logger.info(f"Received timezone data from frontend: {rpc_response}")
                return f"User timezone information retrieved: {rpc_response}"

            except StopIteration:
                raise ToolError(
                    "No remote participants found in the room to get timezone information."
                )
            except Exception as e:
                # This will catch RPC timeouts or other communication errors
                logger.error(f"Failed to get user timezone: {e}", exc_info=True)
                raise ToolError(
                    f"Failed to get timezone information from the frontend: {e}"
                )

    @function_tool(
        raw_schema={
            "type": "function",
            "name": "get_automation_by_id",
            "description": "Retrieves detailed information about a specific automation by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "automation_id": {
                        "type": "string",
                        "description": "The ID of the automation to retrieve",
                    },
                },
                "required": ["automation_id"],
            },
        }
    )
    async def get_automation_by_id(
        self, raw_arguments: dict[str, object], context: RunContext
    ):
        """
        Retrieves detailed information about a specific automation by its ID.
        
        This tool returns the full automation object as JSON, including all
        settings, linked accounts, and current status.
        """
        logger.info(
            f"[AUTOMATION_TOOL] get_automation_by_id called by user {self.user_id}"
        )
        logger.info(f"[AUTOMATION_TOOL] Raw arguments: {raw_arguments}")

        async with self._tool_notification_wrapper("get_automation_by_id", "Get automation by ID")(self):
            try:
                from aci.common.db import crud

                automation_id = str(raw_arguments["automation_id"])
                
                # Use a fresh database session
                with get_db_session() as fresh_db_session:
                    # Verify the automation exists and belongs to the user
                    automation = crud.automations.get_automation(
                        fresh_db_session, automation_id
                    )
                    if not automation:
                        return json.dumps({
                            "success": False,
                            "error": f"Automation with ID '{automation_id}' not found."
                        })

                    if automation.user_id != self.user_id:
                        return json.dumps({
                            "success": False,
                            "error": f"You don't have access to automation '{automation_id}'."
                        })

                    # Return the full automation object as JSON
                    automation_data = {
                        "id": automation.id,
                        "name": automation.name,
                        "description": automation.description,
                        "goal": automation.goal,
                        "is_deep": automation.is_deep,
                        "active": automation.active,
                        "is_recurring": automation.is_recurring,
                        "cron_schedule": automation.cron_schedule,
                        "created_at": automation.created_at.isoformat() if automation.created_at else None,
                        "updated_at": automation.updated_at.isoformat() if automation.updated_at else None,
                        "last_run_at": automation.last_run_at.isoformat() if automation.last_run_at else None,
                        "linked_account_ids": [la.id for la in automation.linked_accounts] if automation.linked_accounts else []
                    }

                    logger.info(
                        f"[AUTOMATION_TOOL] Successfully retrieved automation {automation_id}"
                    )
                    return json.dumps({
                        "success": True,
                        "automation": automation_data
                    })

            except Exception as e:
                logger.error(
                    f"[AUTOMATION_TOOL] Error in get_automation_by_id: {str(e)}",
                    exc_info=True,
                )
                return json.dumps({
                    "success": False,
                    "error": f"An unexpected error occurred while retrieving the automation: {str(e)}"
                })


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
    session_metrics = {"llm_tokens": 0, "stt_duration": 0.0, "tts_characters": 0}

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

        logger.info(
            f"User {user_id} disconnected. Session duration: {session_duration_minutes:.2f} minutes"
        )

        # Use real-time accumulated metrics first, fall back to usage_collector if needed
        usage_summary = usage_collector.get_summary()

        # If real-time tracking didn't capture anything but usage_collector did, use that as fallback
        if (
            session_metrics["llm_tokens"] == 0
            and session_metrics["stt_duration"] == 0
            and session_metrics["tts_characters"] == 0
        ):
            if (
                usage_summary.llm_prompt_tokens > 0
                or usage_summary.llm_completion_tokens > 0
                or usage_summary.stt_audio_duration > 0
                or usage_summary.tts_characters_count > 0
            ):
                logger.warning(
                    f"Real-time metrics were zero but usage_collector has data - using fallback for user {user_id}"
                )
                session_metrics["llm_tokens"] = (
                    usage_summary.llm_prompt_tokens
                    + usage_summary.llm_completion_tokens
                    + usage_summary.llm_prompt_cached_tokens
                )
                session_metrics["stt_duration"] = usage_summary.stt_audio_duration
                session_metrics["tts_characters"] = usage_summary.tts_characters_count

        logger.info(
            f"Final session metrics for user {user_id}: "
            f"LLM tokens: {session_metrics['llm_tokens']}, "
            f"STT duration: {session_metrics['stt_duration']:.2f}s, "
            f"TTS characters: {session_metrics['tts_characters']}"
        )

        logger.info(
            f"UsageCollector summary for comparison: "
            f"LLM tokens: {usage_summary.llm_prompt_tokens + usage_summary.llm_completion_tokens + usage_summary.llm_prompt_cached_tokens}, "
            f"STT duration: {usage_summary.stt_audio_duration:.2f}s, "
            f"TTS characters: {usage_summary.tts_characters_count}"
        )

        # --- CONSOLIDATED SAVING LOGIC ---
        try:
            with create_db_session(DB_FULL_URL) as db_session:
                
                stt_minutes = (
                    session_metrics["stt_duration"] / 60
                )  # Convert seconds to minutes

                # Only save if there is actually something to record
                if (
                    session_duration_minutes > 0
                    or session_metrics["llm_tokens"] > 0
                    or stt_minutes > 0
                    or session_metrics["tts_characters"] > 0
                ):
                    
                    # Create ONE usage event with ALL metrics for the session
                    usage_crud.create_voice_session_event(
                        db_session,
                        user_id,
                        voice_agent_minutes=session_duration_minutes, 
                        llm_tokens=session_metrics["llm_tokens"],
                        stt_minutes=stt_minutes,
                        tts_characters=session_metrics["tts_characters"],
                    )

                    logger.info(
                        f"Recorded consolidated usage event for user {user_id}: "
                        f"{session_duration_minutes:.2f} voice minutes, "
                        f"{session_metrics['llm_tokens']} LLM tokens, "
                        f"{stt_minutes:.2f} STT minutes, "
                        f"{session_metrics['tts_characters']} TTS characters"
                    )
                else:
                    logger.info(
                        f"No usage metrics to record for user {user_id}. Skipping DB call."
                    )

        except Exception as e:
            logger.error(
                f"Error saving usage metrics for user {user_id}: {str(e)}",
                exc_info=True,
            )

    # Listen for user disconnect events
    def on_participant_disconnected(participant):
        if participant.identity == user_id:
            logger.info(f"User {user_id} disconnected from room, saving usage metrics")
            save_usage_metrics()

    # Listen for room disconnect events as fallback
    def on_room_disconnected(reason):
        logger.info(
            f"Room disconnected (reason: {reason}), saving usage metrics for user {user_id}"
        )
        save_usage_metrics()

    # Listen for connection state changes for additional robustness
    def on_connection_state_changed(state):
        if state == ConnectionState.CONN_DISCONNECTED:
            logger.info(
                f"Connection disconnected, saving usage metrics for user {user_id}"
            )
            save_usage_metrics()

    # Register event listeners
    ctx.room.on("participant_disconnected", on_participant_disconnected)
    ctx.room.on("disconnected", on_room_disconnected)
    ctx.room.on("connection_state_changed", on_connection_state_changed)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
        max_tool_steps=15,
    )

    logger.info(f"Registering metrics_collected callback for user {user_id}")

    # Use the correct decorator syntax for metrics collection
    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        logger.info(
            f"Metrics collected callback triggered: {type(ev.metrics).__name__}"
        )
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

        # Accumulate metrics in real-time based on metric type
        if isinstance(ev.metrics, metrics.LLMMetrics):
            session_metrics["llm_tokens"] += (
                ev.metrics.completion_tokens
                + ev.metrics.prompt_tokens
                + ev.metrics.prompt_cached_tokens
            )
        elif isinstance(ev.metrics, metrics.STTMetrics):
            session_metrics["stt_duration"] += ev.metrics.audio_duration
        elif isinstance(ev.metrics, metrics.TTSMetrics):
            session_metrics["tts_characters"] += ev.metrics.characters_count
        elif isinstance(ev.metrics, metrics.RealtimeModelMetrics):
            session_metrics["llm_tokens"] += (
                ev.metrics.input_tokens + ev.metrics.output_tokens
            )


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
            logger.info(
                f"Session error occurred, saving usage metrics for user {user_id}"
            )
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
