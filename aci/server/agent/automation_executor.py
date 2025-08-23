from typing import Literal, cast
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel
from aci.common.db.sql_models import Automation, Function
from langchain_core.tools import StructuredTool
from aci.common.schemas.function import OpenAIFunction, OpenAIFunctionDefinition
from aci.server.function_executors.function_utils import (
    format_function_definition,
    FunctionDefinitionFormat,
    execute_function,
)
from sqlalchemy.orm import Session


class AutomationResult(BaseModel):
    """Schema for the result of an automation run."""

    status: Literal["success", "failure"]
    message: str
    artifact_ids: list[str] = []


class AutomationExecutor:
    """Executor for running automations using LangGraph."""

    def __init__(
        self,
        automation: Automation,
        db_session: Session,
    ):
        self.automation = automation
        self.db_session = db_session
        prompt_components = [
            (
                "You are an expert automation agent named Autom8. Your primary objective is to "
                "successfully execute the user's defined task by formulating a plan and using the provided tools."
            ),
            (f'You must accomplish the following goal:\n"{self.automation.goal}"'),
            (
                "### Detailed Task Instructions & Rules\n"
                "1.  **Plan First (Chain of Thought)**: Before executing any tools, you MUST formulate a clear, "
                "step-by-step plan. Think about the sequence of tools required to achieve the goal.\n"
                "2.  **Tool Adherence**: You may ONLY use the tools provided to you. Do not invent tools or "
                "assume functionality that isn't explicitly available.\n"
                "3.  **Artifact Chaining**: Tools may produce or consume artifacts (files or data) identified by an "
                "`artifact_id`. If a task requires multiple steps (e.g., create a file, then edit it), you MUST use "
                "the `artifact_id` from the first step as an input to the second.\n"
                "4.  **Error Handling**: If a tool fails or you cannot complete the task for any reason, you must "
                "clearly report the failure and the reason for it.\n"
                "5.  **Goal Completion**: Once the user's goal is fully met, your task is complete."
            ),
            (
                "Here is an example of how to approach a task:\n\n"
                '**User Goal**: "Generate a picture of a majestic lion and resize it for a social media profile picture."\n\n'
                "**Your Plan**:\n"
                "1.  The goal requires two steps: generation and resizing.\n"
                '2.  First, I will use the `image_generation_tool` with the prompt "a majestic lion" to create the '
                "initial image. This should return an `artifact_id` for the new image.\n"
                "3.  Second, I will use the `image_resizing_tool`, providing the `artifact_id` from the previous step "
                "and specifying the dimensions for a profile picture (e.g., 400x400).\n"
                "4.  After the second tool call succeeds, the goal is complete."
                "5. You must return the final message along with any artifacts created."
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

    def get_tools(self) -> list[StructuredTool]:
        """Convert the automation's functions into LangChain tools."""
        functions = self.get_functions()
        tools = []

        for function in functions:

            def execute_tool(**kwargs):
                return execute_function(
                    self.db_session, function.name, self.automation.user_id, kwargs
                )

            formatted_function: OpenAIFunction = cast(
                OpenAIFunctionDefinition,
                format_function_definition(function, FunctionDefinitionFormat.OPENAI),
            ).function

            tool = StructuredTool.from_function(
                name=function.name,
                description=function.description,
                infer_schema=False,
                args_schema=formatted_function.parameters,
                func=execute_tool,
            )
            tools.append(tool)

        return tools

    def create_agent(self):
        agent = create_react_agent(
            model="",
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
        return AutomationResult.model_validate(result)
