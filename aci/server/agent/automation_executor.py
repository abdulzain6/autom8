from datetime import datetime
import json
from typing import Literal, cast
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field, SecretStr
from aci.common.db.sql_models import Automation, Function, AutomationRun
from aci.common.enums import RunStatus
from langchain_core.tools import StructuredTool
from aci.common.schemas.function import OpenAIFunction, OpenAIFunctionDefinition
from aci.server.config import XAI_API_KEY
from aci.server.dependencies import get_db_session
from aci.server.function_executors.function_utils import (
    format_function_definition,
    FunctionDefinitionFormat,
    execute_function,
)
from langchain_xai import ChatXAI
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
                "### CRITICAL SAFETY & SECURITY RULES\n"
                "**ALWAYS VERIFY BEFORE ACTION**: Never execute potentially dangerous operations without explicit confirmation in the goal.\n"
                "**URL SECURITY**: Block any URLs that appear suspicious, internal, or dangerous (localhost, private IPs, file://, etc.).\n"
                "**DATA PROTECTION**: Never delete, overwrite, or modify user data without clear instructions.\n"
                "**TOOL LIMITS**: Respect all tool restrictions and usage limits strictly.\n"
                "**VALIDATION FIRST**: Always validate inputs and check for errors before proceeding with complex operations.\n"
                "**CONSERVATIVE APPROACH**: When in doubt, ask for clarification rather than making assumptions.\n"
                "**ERROR HANDLING**: If any step fails, stop and report the issue rather than continuing blindly."
            ),
            (
                "### Detailed Task Instructions & Rules\n"
                "1.  **Safety First**: Before any potentially destructive operation, verify it's explicitly requested and safe.\n"
                "2.  **Efficiency with Caution**: Use the MINIMUM number of tool calls necessary, but prioritize safety over speed.\n"
                "3.  **Plan Carefully**: Think through ALL potential risks and failure points before executing.\n"
                "4.  **Tool Adherence**: You may ONLY use the tools provided. Do not invent tools or workarounds.\n"
                "5.  **Artifact Validation**: NEVER use artifact_ids that weren't explicitly returned by previous tool calls.\n"
                "6.  **Input Validation**: Always validate that your inputs are reasonable and safe before calling tools.\n"
                "7.  **Browser Tool Restriction**: The BROWSER__RUN_BROWSER_AUTOMATION tool can only be used once per automation run. Plan carefully.\n"
                "8.  **Error Recovery**: If a tool fails, analyze the error and determine if it's safe to retry or continue.\n"
                "9.  **Final Answer Formatting**: Your final answer MUST use the `AutomationResult` schema with a clear, human-readable summary."
            ),
            (
                "### Security Validation Checklist\n"
                "- [ ] URLs: Check for localhost, private IPs, suspicious domains, file:// schemes\n"
                "- [ ] File Operations: Verify file paths are safe and don't traverse directories\n"
                "- [ ] Data Operations: Confirm destructive actions are explicitly requested\n"
                "- [ ] Tool Limits: Respect browser tool restrictions and other limits\n"
                "- [ ] Input Sanity: Ensure parameters are reasonable and well-formed\n"
                "- [ ] Error Handling: Plan for potential failures and have recovery strategies"
            ),
            (
                # --- UPDATED EXEMPLAR ---
                "### Exemplar\n"
                '**User Goal**: "Generate a picture of a lion and resize it for a profile picture."\n\n'
                "**Your Safe Plan**:\n"
                "1.  Validate the image generation request is safe and appropriate.\n"
                "2.  Use the `image_generation_tool` with the prompt \"a majestic lion\". Verify the response contains a valid artifact_id.\n"
                "3.  Use the `image_resizing_tool` with the validated `artifact_id` from step 2.\n"
                "4.  Confirm the final result before reporting success.\n\n"
                "**Good `automation_output` Example:**\n"
                "\"I successfully generated an image of a lion and resized it to be suitable for a profile picture. The final image is available with artifact ID 'artifact-456'.\"\n\n"
                "**Bad `automation_output` Example (Do NOT do this):**\n"
                '"`{"status": "complete", "final_artifact": "artifact-456"}`" (This is incorrect because it is a JSON string, not a human-readable summary.)'
            ),
            (
                "### Performance Guidelines\n"
                "- **Be Conservative**: Prioritize safety and correctness over speed.\n"
                "- **Validate Everything**: Check tool responses for errors and validate artifact_ids.\n"
                "- **Fail Safely**: Stop execution if anything seems unsafe or unclear.\n"
                "- **Clear Communication**: Explain what you're doing and why at each step.\n"
                "- **Success Criteria**: Complete the task safely with validated results."
            ),
        ]
        self.system_prompt = "\n\n---\n\n".join(prompt_components)

    def get_previous_run_outputs(self, limit: int = 5) -> list[dict]:
        """Get the previous run outputs for this automation."""
        try:
            with get_db_session() as db_session:
                # Query for previous runs of this automation, excluding the current run if it exists
                query = db_session.query(AutomationRun).filter(
                    AutomationRun.automation_id == self.automation.id,
                    AutomationRun.status == RunStatus.success  # Only get successful runs
                )
                
                if self.run_id:
                    query = query.filter(AutomationRun.id != self.run_id)
                
                # Order by started_at descending and limit to the specified number
                previous_runs = query.order_by(AutomationRun.started_at.desc()).limit(limit).all()
                
                # Format the results
                run_outputs = []
                for run in previous_runs:
                    run_outputs.append({
                        "run_id": run.id,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                        "status": run.status.value if hasattr(run.status, 'value') else str(run.status),
                        "message": run.message,
                        "artifacts_count": len(run.artifacts) if run.artifacts else 0
                    })
                
                return run_outputs
                
        except Exception as e:
            logger.error(f"Error getting previous run outputs: {e}")
            return []

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
                    
                    # Ensure the result is always a string for LangGraph compatibility
                    if isinstance(trimmed_result, (dict, list)):
                        import json
                        final_result = json.dumps(trimmed_result, default=str, ensure_ascii=False)
                    else:
                        final_result = str(trimmed_result)
                    
                    logger.info(
                        f"Tool {function.name} executed successfully, response trimmed from {len(str(result))} to {len(final_result)} chars"
                    )
                    logger.info(f"Final result: {final_result}")
                    return final_result
                except Exception as e:
                    logger.error(
                        f"Error executing tool: {function.name} with args: {kwargs}, error: {e}"
                    )
                    import traceback
                    traceback.print_exc()
                    return f"Error: {str(e)}"
        except Exception as db_error:
            # Handle database session errors specifically
            logger.error(f"Database session error for tool {function.name}: {db_error}")
            if "pending rollback" in str(db_error).lower():
                logger.warning("Handled pending rollback error, operation may have completed successfully")
                return "Database transaction issue occurred, but operation may have completed"
            return f"Database connection error: {str(db_error)}"

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

        # Add built-in tool for getting previous run outputs
        class GetPreviousRunsInput(BaseModel):
            limit: int = Field(default=5, description="Number of previous runs to retrieve (max 10)")
        
        def get_previous_runs_tool(limit: int = 5) -> str:
            """Get the outputs from previous successful runs of this automation."""
            if limit > 10:
                limit = 10  # Cap at 10 for safety
            runs = self.get_previous_run_outputs(limit)
            return json.dumps(runs, default=str, ensure_ascii=False)
        
        tools.append(
            StructuredTool.from_function(
                name="GET_PREVIOUS_RUN_OUTPUTS",
                description="Retrieve the outputs from previous successful runs of this automation. Useful for learning from past executions and avoiding repeating the same work.",
                func=get_previous_runs_tool,
                args_schema=GetPreviousRunsInput,
            )
        )

        return tools

    def create_agent(self):
        model = ChatXAI(
            api_key=SecretStr(XAI_API_KEY),
            model="grok-4-fast-reasoning-latest",
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
                        "content": f"Execute the automation: {self.automation.name}\nGoal: {self.automation.goal}. Ensure you format the result according to the schema in the end.",
                    }
                ]
            }
        )
        return result["structured_response"]
