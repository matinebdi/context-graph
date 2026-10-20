from pathlib import Path

from context_graph.integrations.base import ClientIntegration, PythonRuntime


class ClaudeCodeIntegration(ClientIntegration):
    """Claude Code: installed as a plugin from this repository's marketplace.

    Plugin installation is owned by Claude Code itself, so this integration writes no files and
    only explains the commands to run.
    """

    NAME = "claude"

    def install(self) -> list[Path]:
        return []

    def uninstall(self) -> list[Path]:
        return []

    def next_steps(self) -> str:
        return (
            "  Claude Code uses the plugin from this repository's marketplace. In a terminal:\n\n"
            f"    claude plugin marketplace add {PythonRuntime.REPOSITORY}\n"
            f"    cd {self.project}\n"
            "    claude plugin install context-graph@context-graph --scope project\n\n"
            "  Then, inside Claude Code: /context-graph:setup (once per machine).\n"
            "  To remove it: claude plugin uninstall context-graph@context-graph"
        )
