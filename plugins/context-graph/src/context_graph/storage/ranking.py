import datetime as dt
import math


class Ranking:
    """Scoring rules for search results and for the overview.

    Search fuses the keyword and the vector ranking with reciprocal rank fusion, which needs no
    calibration between the two score scales. The overview orders nodes by an importance score
    that mixes how connected a node is, how often it is used and how recently it changed.
    """

    # Standard RRF damping constant: higher values flatten the advantage of the top ranks.
    RRF_K = 60
    # Minimum cosine similarity for a semantic match to count as a search hit at all.
    SEMANTIC_FLOOR = 0.3
    # Penalties are rank shifts: such a node scores as if found that many places lower. Nodes of
    # the current project and global nodes are not penalized.
    OTHER_PROJECT_PENALTY = 5
    INACTIVE_PENALTY = 10
    # Days after which the recency part of the importance score has halved.
    RECENCY_HALF_LIFE_DAYS = 30

    @classmethod
    def fuse(cls, *rankings: list[str], penalties: dict[str, int] | None = None) -> dict[str, float]:
        """Combines ranked id lists with reciprocal rank fusion.

        Args:
            *rankings: Lists of node ids, best first.
            penalties: Rank shift per id (see ``OTHER_PROJECT_PENALTY``); missing ids get none.

        Returns:
            Fused score per id; an id found by several rankings scores higher.
        """
        penalties = penalties or {}
        scores: dict[str, float] = {}
        for ranking in rankings:
            for rank, node_id in enumerate(ranking, start=1):
                shifted = rank + penalties.get(node_id, 0)
                scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (cls.RRF_K + shifted)
        return scores

    @classmethod
    def importance(cls, degree: int, use_count: int, updated: str, now: dt.datetime | None = None) -> float:
        """Scores how important a node is for the overview.

        Args:
            degree: Number of relationships of the node (project links excluded).
            use_count: How often the node was returned by a search or read.
            updated: Last update timestamp, ISO 8601.
            now: Reference time; defaults to the current UTC time.

        Returns:
            A non-negative score; higher is more important.
        """
        now = now or dt.datetime.now(dt.timezone.utc)
        age_days = cls._age_days(updated, now)
        recency = 0.5 ** (age_days / cls.RECENCY_HALF_LIFE_DAYS) if age_days is not None else 0.0
        return math.log1p(degree) + math.log1p(use_count) + 2.0 * recency

    @staticmethod
    def _age_days(timestamp: str, now: dt.datetime) -> float | None:
        if not timestamp:
            return None
        text = timestamp.replace("Z", "+00:00")
        if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
            text = text[:-2] + ":" + text[-2:]  # +0200 -> +02:00
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
        return max((now - moment).total_seconds() / 86400, 0.0)
