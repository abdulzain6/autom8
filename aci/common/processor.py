import copy
from typing import Any

from aci.common.logging_setup import get_logger

logger = get_logger(__name__)
def filter_visible_properties(parameters_schema: dict) -> dict:
    """
    Filter the schema to include only visible properties and remove the 'visible' field itself.
    This version is updated to handle 'anyOf' and nested objects inside array 'items'.
    """

    def filter(schema: dict) -> dict:
        # Safety check for cases where a non-dict (like null) is passed
        if not isinstance(schema, dict):
            return schema

        # Handle schemas composed with 'anyOf'
        if "anyOf" in schema and isinstance(schema.get("anyOf"), list):
            schema["anyOf"] = [filter(sub_schema) for sub_schema in schema["anyOf"]]
            return schema

        # --- FIX: Recursively filter the schema for items in an array ---
        if schema.get("type") == "array" and "items" in schema:
            schema["items"] = filter(schema["items"])
            return schema

        # Handle standard object definitions
        if schema.get("type") != "object":
            return schema

        # This part only runs for a standard {"type": "object"}
        visible: list[str] = schema.pop("visible", [])
        properties: dict | None = schema.get("properties")
        required: list[str] | None = schema.get("required")

        if properties is not None:
            filtered_properties = {
                key: value for key, value in properties.items() if key in visible
            }

            if required is not None:
                schema["required"] = [key for key in required if key in visible]

            # Recursively filter the properties of the object
            for key, value in filtered_properties.items():
                filtered_properties[key] = filter(value)

            schema["properties"] = filtered_properties

        return schema

    # Create a deep copy to avoid modifying the original schema in place
    filtered_parameters_schema = copy.deepcopy(parameters_schema)
    return filter(filtered_parameters_schema)

def inject_required_but_invisible_defaults(parameters_schema: dict, input_data: dict) -> dict:
    """
    Recursively injects required but invisible properties with their default values into the input data.
    """
    for prop, subschema in parameters_schema.get("properties", {}).items():
        # check if the property is not set by user and is required but invisible
        if (
            prop not in input_data
            and prop in parameters_schema.get("required", [])
            and prop not in parameters_schema.get("visible", [])
        ):
            # check if it has a default value, which should exist for non-object types
            if "default" in subschema:
                input_data[prop] = subschema["default"]
            else:
                # If no default value, but it's an object, initialize it as an empty dict
                if subschema.get("type") == "object":
                    input_data[prop] = {}
                else:
                    raise Exception(
                        f"No default value found for property: {prop}, type: {subschema.get('type')}"
                    )
        # Recursively inject defaults for nested objects
        if isinstance(input_data.get(prop), dict):
            inject_required_but_invisible_defaults(subschema, input_data[prop])

    return input_data


def remove_none_values(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: remove_none_values(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [remove_none_values(item) for item in data if item is not None]
    else:
        return data
