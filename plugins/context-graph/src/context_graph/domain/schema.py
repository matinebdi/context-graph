import datetime as dt
import os
import re

from context_graph.domain.errors import GraphError


class Schema:
    """Ontology defaults and the naming rules for ids, types, labels and relationships.

    Labels and relationship types cannot be passed to Cypher as parameters, so every name that is
    interpolated into a query must first go through ``label`` or ``relationship``.
    """

    DEFAULT_TYPES = {
        "person": "the user and people they work with",
        "project": "a codebase or long-running effort (carries a filesystem `path`)",
        "decision": "a choice that was made, and why",
        "preference": "how the user likes things done",
        "concept": "domain knowledge, architecture, glossary terms",
        "resource": "URLs, docs, dashboards, tickets",
        "tool": "software, services, libraries in use",
        "task": "open work that spans sessions",
        "session": "a log of one working session",
    }
    # Types that describe the user or the wider world rather than one project.
    GLOBAL_TYPES = frozenset({"person", "preference", "tool", "project"})
    SUGGESTED_RELATIONS = (
        "WORKS_ON", "OWNS", "PREFERS", "DECIDED", "PART_OF", "DEPENDS_ON", "USES", "RELATES_TO",
        "SUPERSEDES", "ABOUT", "DOCUMENTED_IN", "BLOCKS", "FOLLOWS_UP",
    )
    # Managed by the store for project scoping; never set directly.
    SYSTEM_RELATIONS = frozenset({"IN_PROJECT"})
    STATUSES = frozenset({"active", "done", "superseded", "archived"})

    _ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
    _TYPE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
    _RELATION = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")

    @classmethod
    def node_id(cls, value: str) -> str:
        """Validates a node id.

        Args:
            value: Candidate id.

        Returns:
            The id, unchanged.

        Raises:
            GraphError: If it is not a lowercase-dash slug of at most 80 characters.
        """
        if not isinstance(value, str) or not cls._ID.match(value):
            raise GraphError(f"invalid id {value!r}: use lowercase letters, digits and dashes (max 80)")
        return value

    @classmethod
    def node_type(cls, value: str) -> str:
        """Validates and normalizes a node type.

        Args:
            value: Candidate type, any case.

        Returns:
            The lowercase snake_case type.

        Raises:
            GraphError: If it is not snake_case.
        """
        normalized = (value or "").strip().lower()
        if not cls._TYPE.match(normalized):
            raise GraphError(
                f"invalid type {value!r}: use snake_case; defaults are {sorted(cls.DEFAULT_TYPES)}"
            )
        return normalized

    @classmethod
    def status(cls, value: str) -> str:
        """Validates a node status.

        Args:
            value: Candidate status.

        Returns:
            The status, unchanged.

        Raises:
            GraphError: If it is not one of ``STATUSES``.
        """
        if value not in cls.STATUSES:
            raise GraphError(f"invalid status {value!r}; expected one of {sorted(cls.STATUSES)}")
        return value

    @classmethod
    def label(cls, node_type: str) -> str:
        """Converts a node type to its Cypher label, e.g. ``user_story`` to ``UserStory``.

        Args:
            node_type: Node type to convert.

        Returns:
            A label that is safe to interpolate into Cypher.

        Raises:
            GraphError: If the type is invalid.
        """
        return "".join(part.capitalize() for part in cls.node_type(node_type).split("_"))

    @classmethod
    def relationship(cls, value: str) -> str:
        """Converts a user relation name to its Cypher relationship type, e.g. ``works_on`` to ``WORKS_ON``.

        Args:
            value: Relation name, any case.

        Returns:
            An upper snake case type that is safe to interpolate into Cypher.

        Raises:
            GraphError: If the name is invalid or reserved for the system.
        """
        if not isinstance(value, str) or not cls._RELATION.match(value):
            raise GraphError(
                f"invalid relation {value!r}: use snake_case, e.g. {list(cls.SUGGESTED_RELATIONS[:5])}"
            )
        upper = value.upper()
        if upper in cls.SYSTEM_RELATIONS:
            raise GraphError(f"{upper} is managed automatically; use the scope/project arguments instead")
        return upper

    @staticmethod
    def slug(text: str) -> str:
        """Turns free text into an id-compatible slug.

        Args:
            text: Any text, e.g. a folder name.

        Returns:
            A lowercase-dash slug of at most 80 characters, never empty.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug[:80].rstrip("-") or "node"


class Clock:
    """Timestamps in the one format stored in the graph."""

    @staticmethod
    def now() -> str:
        """Returns the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``, which sorts lexicographically."""
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PathKey:
    """Comparable form of filesystem paths, used to match directories to projects."""

    @staticmethod
    def normalize(path: str) -> str:
        """Normalizes Windows, Git Bash (``/c/...``) and POSIX paths for prefix comparison.

        Args:
            path: A filesystem path in any of those forms.

        Returns:
            The path with forward slashes, no trailing slash, case-folded where the OS is
            case-insensitive.
        """
        path = path.strip().replace("\\", "/")
        drive = re.match(r"^/([a-zA-Z])/(.*)$", path)
        if drive:
            path = f"{drive.group(1)}:/{drive.group(2)}"
        return os.path.normcase(os.path.normpath(path)).replace("\\", "/").rstrip("/")
