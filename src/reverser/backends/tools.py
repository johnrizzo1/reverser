"""Utilities for converting MCP tools to OpenAI function calling format."""

import json


def mcp_tools_to_openai(tools: list) -> tuple[list[dict], dict]:
    """Convert SdkMcpTool instances to OpenAI tool definitions.

    Returns:
        A tuple of (openai_tool_defs, handler_map) where handler_map
        maps tool name -> async handler function.
    """
    openai_tools = []
    handlers = {}

    for t in tools:
        schema = t.input_schema

        # Normalize schema: if it's just a properties dict (no "type" key),
        # wrap it into a proper JSON Schema object.
        if isinstance(schema, dict):
            if "type" not in schema:
                # Simple form: {"path": {"type": "string", ...}}
                schema = {
                    "type": "object",
                    "properties": schema,
                    "required": list(schema.keys()),
                }
            elif schema.get("type") == "object" and "properties" not in schema:
                schema["properties"] = {}
        else:
            # TypedDict or other class — skip for now
            schema = {"type": "object", "properties": {}}

        # Ensure additionalProperties is false (OpenAI strict mode likes this)
        if "additionalProperties" not in schema:
            schema["additionalProperties"] = False

        openai_tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": schema,
            },
        })
        handlers[t.name] = t.handler

    return openai_tools, handlers


async def execute_tool(handlers: dict, name: str, arguments: str) -> tuple[str, bool]:
    """Execute an MCP tool and return (result_text, is_error).

    Args:
        handlers: Map of tool name -> async handler.
        name: Tool name to execute.
        arguments: JSON string of arguments from the model.

    Returns:
        Tuple of (result_text, is_error).
    """
    handler = handlers.get(name)
    if handler is None:
        return f"Unknown tool: {name}", True

    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as e:
        return f"Invalid JSON arguments: {e}", True

    try:
        result = await handler(args)
    except Exception as e:
        return f"Tool error: {e}", True

    return extract_tool_result_text(result), result.get("is_error", False)


def extract_tool_result_text(result: dict) -> str:
    """Extract plain text from an MCP tool result dict."""
    content = result.get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item["text"])
        return "\n".join(parts)
    return str(result)
