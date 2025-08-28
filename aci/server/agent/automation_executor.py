import asyncio
from typing import Literal, cast, List
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field, SecretStr
from aci.common.db.sql_models import Automation, Function
from langchain_core.tools import StructuredTool
from aci.common.schemas.function import OpenAIFunction, OpenAIFunctionDefinition
from aci.server.config import DEEPINFRA_API_KEY, DEEPINFRA_BASE_URL
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

    status: Literal["success", "failure"] = Field(..., description="The final status of the automation execution.")
    automation_output: str = Field(..., description="The final, synthesized output, report, or result of the automation. This should be a human-readable summary of everything that was done.")
    artifact_ids: list[str] = Field(default_factory=list, description="A list of final artifact IDs to be returned to the user. ONLY include IDs that were explicitly returned by a tool in a previous step.")


class AutomationExecutor:
    """Executor for running automations using LangGraph."""

    def __init__(
        self,
        automation: Automation,
        run_id: str | None = None,
    ):
        self.automation = automation
        self.run_id = run_id
        prompt_components = [
            (
                "You are an expert automation agent named Autom8. Your primary objective is to "
                "successfully execute the user's defined task by formulating a plan and using the provided tools."
            ),
            (f'You must accomplish the following goal:\n"{self.automation.goal}"'),
            (
                "### Detailed Task Instructions & Rules\n"
                "1.  **Plan First**: Formulate a clear, step-by-step plan before executing any tools.\n"
                "2.  **Tool Adherence**: You may ONLY use the tools provided in the tool list. Do not invent tools.\n"
                "3.  **Artifact Chaining**: If a task requires multiple steps (e.g., create a file, then edit it), you MUST use the `artifact_id` from the first step as an input to the second.\n"
                "4.  **Crucial Rule on Artifacts**: You MUST NOT invent, guess, or hallucinate `artifact_id`s. An `artifact_id` can ONLY be used if it was explicitly present in the output of a previous tool call.\n"
                "5.  **Final Answer Formatting**: After all tool calls are complete and you have gathered all necessary information, you MUST format your final, synthesized answer using the `AutomationResult` schema. Do NOT call `AutomationResult` as a tool."
            ),
            (
                "### Exemplar\n"
                '**User Goal**: "Generate a picture of a lion and resize it for a profile picture."\n\n'
                "**Your Plan**:\n"
                "1.  Use the `image_generation_tool` with the prompt \"a majestic lion\". This will return an artifact with ID 'artifact-123'.\n"
                "2.  Use the `image_resizing_tool`, providing the `artifact_id` 'artifact-123' from the previous step. This will return a new artifact with ID 'artifact-456'.\n"
                "3.  The goal is now complete. I will format my final answer using the `AutomationResult` schema, including the final artifact ID 'artifact-456'."
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

    async def _execute_tool_logic(self, function: Function, **kwargs):
        """
        A helper method containing the actual logic for executing a tool.
        It creates a new, isolated database session for each execution.
        """
        # Create a new session within the async context to ensure thread safety.
        with get_db_session() as tool_db_session:
            try:
                logger.info(f"Executing tool: {function.name} with args: {kwargs}")
                return await execute_function(
                    tool_db_session, function.name, self.automation.user_id, kwargs, run_id=self.run_id
                )
            except Exception as e:
                logger.error(f"Error executing tool: {function.name} with args: {kwargs}, error: {e}")
                import traceback
                traceback.print_exc()
                return {"error": str(e)}

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
                func=lambda f=function, **kwargs: asyncio.run(
                    self._execute_tool_logic(f, **kwargs)
                ),
            )
            tools.append(tool)

        return tools

    def create_agent(self):
        agent = create_react_agent(
            model=ChatOpenAI(
                base_url=DEEPINFRA_BASE_URL,
                api_key=SecretStr(DEEPINFRA_API_KEY),
                model="Qwen/Qwen3-235B-A22B-Instruct-2507",
                timeout=300,
                max_retries=3,
            ),
            tools=self.get_tools(),
            response_format=AutomationResult,
            prompt=self.system_prompt,
            debug=True,
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
