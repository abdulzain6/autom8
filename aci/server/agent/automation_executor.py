from datetime import datetime
from typing import Literal, cast
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field, SecretStr
from aci.common.db.sql_models import Automation, Function
from langchain_core.tools import StructuredTool
from aci.common.schemas.function import OpenAIFunction, OpenAIFunctionDefinition
from aci.server.config import DEEPINFRA_BASE_URL, DEEPINFRA_API_KEY
from aci.server.dependencies import get_db_session
from aci.server.function_executors.function_utils import (
    format_function_definition,
    FunctionDefinitionFormat,
    execute_function,
)
from langchain_openai import ChatOpenAI
from logging import getLogger
import logging


logger = getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class AutomationResult(BaseModel):
    """The required schema for the final output of the automation run."""

    status: Literal["success", "failure"] = Field(
        ..., description="The final status of the automation execution."
    )
    automation_output: str = Field(
        ...,
        description="A plain text, human-readable summary of the final result. This field must NOT contain JSON or any other machine-readable format. It should clearly explain the outcome of the automation to a person.",
    )
    artifact_ids: list[str] = Field(
        default_factory=list,
        description="A list of final artifact IDs to be returned to the user. ONLY include IDs that were explicitly returned by a tool in a previous step.",
    )


class AutomationExecutor:
    """Executor for running automations using LangGraph."""

    def __init__(
        self,
        automation: Automation,
        run_id: str | None = None,
    ):
        self.automation = automation
        self.run_id = run_id
        self.browser_used = False
        prompt_components = [
            (
                "You are an expert automation agent named Autom8. Your primary objective is to "
                "successfully execute the user's defined task by formulating a plan and using the provided tools."
                f"Today is {datetime.utcnow().strftime('%A, %B %d, %Y')} (UTC)."
            ),
            (f'You must accomplish the following goal:\n"{self.automation.goal}"'),
            (
                "### Detailed Task Instructions & Rules\n"
                "1.  **Efficiency First**: Use the MINIMUM number of tool calls necessary to complete the task. Each tool call has overhead, so be strategic.\n"
                "2.  **Plan Concisely**: Think through the task but avoid over-planning. Execute tools when you have enough information.\n"
                "3.  **Tool Adherence**: You may ONLY use the tools provided in the tool list. Do not invent tools.\n"
                "4.  **Artifact Chaining**: If a task requires multiple steps (e.g., create a file, then edit it), you MUST use the `artifact_id` from the first step as an input to the second.\n"
                "5.  **Crucial Rule on Artifacts**: You MUST NOT invent, guess, or hallucinate `artifact_id`s. An `artifact_id` can ONLY be used if it was explicitly present in the output of a previous tool call.\n"
                "6.  **Consolidate Operations**: When possible, combine multiple operations into single tool calls rather than making separate calls.\n"
                "7.  **Browser Tool Restriction**: The BROWSER__RUN_BROWSER_AUTOMATION tool can only be used once per automation run. Plan your browser interactions accordingly.\n"
                "8.  **Final Answer Formatting**: Your final answer MUST use the `AutomationResult` schema. The `automation_output` field is critical: it must be a **plain, human-readable string** that summarizes the outcome for a non-technical user. It should **NOT** be a JSON string or a raw data dump. Think of it as the final report you'd give to a person."
            ),
            (
                # --- UPDATED EXEMPLAR ---
                "### Exemplar\n"
                '**User Goal**: "Generate a picture of a lion and resize it for a profile picture."\n\n'
                "**Your Plan**:\n"
                "1.  Use the `image_generation_tool` with the prompt \"a majestic lion\". This will return an artifact with ID 'artifact-123'.\n"
                "2.  Use the `image_resizing_tool`, providing the `artifact_id` 'artifact-123' from the previous step. This will return a new artifact with ID 'artifact-456'.\n"
                "3.  The goal is now complete. I will format my final answer using the `AutomationResult` schema.\n\n"
                "**Good `automation_output` Example:**\n"
                "\"I successfully generated an image of a lion and resized it to be suitable for a profile picture. The final image is available with artifact ID 'artifact-456'.\"\n\n"
                "**Bad `automation_output` Example (Do NOT do this):**\n"
                '"`{"status": "complete", "final_artifact": "artifact-456"}`" (This is incorrect because it is a JSON string, not a human-readable summary.)'
            ),
            (
                "### Performance Guidelines\n"
                "- **Be Concise**: Keep your reasoning brief and action-oriented.\n"
                "- **Tool Response Awareness**: Tool responses are automatically trimmed to reduce overhead. Focus on key data and artifact IDs.\n"
                "- **Batch Operations**: When possible, request multiple related items in a single tool call rather than making separate calls.\n"
                "- **Success Criteria**: Complete the task with the minimum viable set of actions that achieve the goal."
            ),
        ]
        self.system_prompt = "\n\n---\n\n".join(prompt_components)

    def get_functions(self) -> list[Function]:
        """Retrieve the list of functions associated with the automation."""
        linked_accounts = self.automation.linked_accounts
        functions = []
        for account in linked_accounts:
            if not account.linked_account.app.active:
                continue

            disabled_functions = account.linked_account.disabled_functions
            linked_account_functions = account.linked_account.app.functions

            # Filter out disabled functions
            enabled_functions = [
                func
                for func in linked_account_functions
                if func.id not in disabled_functions and func.active
            ]
            functions.extend(enabled_functions)

        return functions

    def _trim_tool_response(self, response, max_length: int = 20000):
        """
        Trim tool responses to keep only essential information and reduce token usage.
        """
        # Convert to dict if needed
        if hasattr(response, "model_dump"):
            response_dict = response.model_dump()
        elif hasattr(response, "__dict__"):
            response_dict = response.__dict__
        elif isinstance(response, dict):
            response_dict = response
        else:
            # For simple types, just convert to string and trim if needed
            response_str = str(response)
            if len(response_str) > max_length:
                return f"{response_str[:max_length]}...[TRUNCATED]"
            return response

        # Simple recursive trimming
        def trim_value(value):
            if isinstance(value, str):
                if len(value) > max_length:
                    return f"{value[:max_length//2]}...[TRUNCATED {len(value)-max_length} chars]...{value[-max_length//4:]}"
                return value
            elif isinstance(value, (list, tuple)):
                # Limit to first 10 items and trim each
                limited_items = value[:10]
                trimmed_items = [trim_value(item) for item in limited_items]
                if len(value) > 10:
                    trimmed_items.append(f"...[{len(value)-10} more items]")
                return trimmed_items
            elif isinstance(value, dict):
                return {k: trim_value(v) for k, v in value.items()}
            else:
                return value

        return trim_value(response_dict)

    def _execute_tool_logic(self, function: Function, **kwargs):
        """
        A helper method containing the actual logic for executing a tool.
        It creates a new, isolated database session for each execution.
        """
        # Restrict browser tool to one use per automation run
        if function.name in ["BROWSER__RUN_BROWSER_AUTOMATION", "BROWSER__SCRAPE_WITH_BROWSER"]:
            if self.browser_used:
                return {"error": "Browser tool can only be used once per automation run"}
            self.browser_used = True

        # Create a new session within the async context to ensure thread safety.
        try:
            with get_db_session() as tool_db_session:
                try:
                    logger.info(f"Executing tool: {function.name} with args: {kwargs}")
                    result = execute_function(
                        tool_db_session,
                        function.name,
                        self.automation.user_id,
                        kwargs,
                        run_id=self.run_id,
                    )
                    # Trim the response to reduce token usage while preserving essential information
                    trimmed_result = self._trim_tool_response(result, 35000)
                    logger.info(
                        f"Tool {function.name} executed successfully, response trimmed from {len(str(result))} to {len(str(trimmed_result))} chars"
                    )
                    logger.info(f"Full trimmed result: {trimmed_result}")
                    return trimmed_result
                except Exception as e:
                    logger.error(
                        f"Error executing tool: {function.name} with args: {kwargs}, error: {e}"
                    )
                    import traceback
                    traceback.print_exc()
                    return {"error": str(e)}
        except Exception as db_error:
            # Handle database session errors specifically
            logger.error(f"Database session error for tool {function.name}: {db_error}")
            if "pending rollback" in str(db_error).lower():
                logger.warning("Handled pending rollback error, operation may have completed successfully")
                return {"warning": "Database transaction issue occurred, but operation may have completed"}
            return {"error": f"Database connection error: {str(db_error)}"}

    def get_tools(self) -> list[StructuredTool]:
        """Convert the automation's functions into LangChain tools."""
        functions = self.get_functions()
        tools = []

        for function in functions:
            formatted_function: OpenAIFunction = cast(
                OpenAIFunctionDefinition,
                format_function_definition(function, FunctionDefinitionFormat.OPENAI),
            ).function

            tool = StructuredTool.from_function(
                name=function.name,
                description=function.description,
                infer_schema=False,
                args_schema=formatted_function.parameters,
                func=lambda f=function, **kwargs: self._execute_tool_logic(f, **kwargs),
            )
            tools.append(tool)

        return tools

    def create_agent(self):
        model = ChatOpenAI(
            base_url=DEEPINFRA_BASE_URL,
            api_key=SecretStr(DEEPINFRA_API_KEY),
            model="moonshotai/Kimi-K2-Instruct-0905",
            timeout=300,
            max_retries=3,
        )
        agent = create_react_agent(
            model=model,
            tools=self.get_tools(),
            response_format=AutomationResult,
            prompt=self.system_prompt,
        )
        return agent

    def run(self) -> AutomationResult:
        """Run the automation using the defined agent."""
        agent = self.create_agent()
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Execute the automation: {self.automation.name}\nGoal: {self.automation.goal}. Think step by step.",
                    }
                ]
            }
        )
        return result["structured_response"]
