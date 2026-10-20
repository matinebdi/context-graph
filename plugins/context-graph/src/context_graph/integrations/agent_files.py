from pathlib import Path

from context_graph.integrations.base import ClientIntegration
from context_graph.integrations.cursor import CursorIntegration


class AgentFiles:
    """The plugin files generated from ``assets/agent-instructions.md``.

    The usage policy has a single source; the Claude Code skill and the Cursor plugin rule are
    that text plus each client's frontmatter. ``context-graph generate-agent-files`` rewrites
    them, and a test fails when they drift.

    Attributes:
        plugin_root: Folder holding the plugin manifests (``plugins/context-graph``).
    """

    CLAUDE_SKILL = Path("skills/context-graph/SKILL.md")
    CURSOR_RULE = Path("cursor/rules/context-graph.mdc")
    CLAUDE_SKILL_HEADER = (
        "---\n"
        "name: context-graph\n"
        "description: How to use the context graph as long-term memory. Use it when starting work in a "
        "project, when the user shares durable information about themselves or their work, when they "
        "correct you or state a preference, when a decision is made, when you learn how a part of the "
        "project works, or before asking something the user may already have told you.\n"
        "---\n\n"
        "<!-- Generated from src/context_graph/assets/agent-instructions.md; edit that file instead. -->\n\n"
    )

    def __init__(self, plugin_root: Path | None = None):
        self.plugin_root = plugin_root or Path(__file__).resolve().parents[3]

    def expected(self) -> dict[Path, str]:
        """Returns the expected content of each generated file, keyed by path relative to the plugin root."""
        instructions = ClientIntegration.agent_instructions()
        return {
            self.CLAUDE_SKILL: self.CLAUDE_SKILL_HEADER + instructions,
            self.CURSOR_RULE: CursorIntegration.RULE_HEADER.replace(
                "`context-graph install cursor`", "`context-graph generate-agent-files`") + instructions,
        }

    def stale(self) -> list[Path]:
        """Returns the generated files whose content differs from what it should be."""
        return [path for path, content in self.expected().items()
                if not (self.plugin_root / path).exists()
                or (self.plugin_root / path).read_text(encoding="utf-8") != content]

    def write(self) -> list[Path]:
        """Rewrites the stale generated files. Returns the paths written."""
        written = self.stale()
        for path in written:
            target = self.plugin_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.expected()[path], encoding="utf-8", newline="\n")
        return written
