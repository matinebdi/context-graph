from mcp.server.mcpserver.exceptions import ToolError


class GraphError(ToolError):
    """An anticipated failure the caller can act on, such as invalid input or an unreachable database.

    It subclasses the MCP SDK's ``ToolError`` so that, raised from a tool, its message reaches the
    client as a regular tool error instead of being masked as an unexpected crash.
    """
