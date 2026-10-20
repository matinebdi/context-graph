import datetime as dt
import json

import pytest

from context_graph.cli import StopHook, TranscriptActivity
from context_graph.storage import Ranking

NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
GRAPH_WRITE = "mcp__plugin_context-graph_context-graph__upsert_node"


def transcript(tmp_path, names: list[str]):
    """Writes a Claude Code style transcript with one assistant entry per tool call."""
    path = tmp_path / "session.jsonl"
    lines = [json.dumps({"type": "user", "message": {"content": "hi"}})]
    for i, name in enumerate(names):
        content = [{"type": "text", "text": "..."}, {"type": "tool_use", "id": f"t{i}", "name": name, "input": {}}]
        lines.append(json.dumps({"type": "assistant", "message": {"content": content}}))
        # API request logs repeat earlier calls; they must not be counted again.
        lines.append(json.dumps({"type": "api-request", "messages": [{"content": content}]}))
    path.write_text("\n".join(lines) + "\nnot json\n", encoding="utf-8")
    return path


class TestRanking:
    def test_fusion_rewards_agreement(self):
        scores = Ranking.fuse(["a", "b", "c"], ["c", "d"])
        assert max(scores, key=scores.get) == "c"
        assert scores["a"] > scores["b"] == scores["d"] > 0  # same rank, same score

    def test_penalties_shift_ranks_without_overpowering_agreement(self):
        scores = Ranking.fuse(["a", "b"], ["a", "b"], penalties={"a": Ranking.OTHER_PROJECT_PENALTY})
        assert scores["b"] > scores["a"]
        scores = Ranking.fuse(["a", "b", "c"], ["a"], penalties={"a": Ranking.OTHER_PROJECT_PENALTY})
        assert max(scores, key=scores.get) == "a"  # found by both rankings: still first

    def test_importance_mixes_degree_use_and_recency(self):
        fresh = Ranking.importance(0, 0, "2026-10-20T00:00:00Z", NOW)
        month_old = Ranking.importance(0, 0, "2026-09-20T00:00:00Z", NOW)
        assert fresh == pytest.approx(2.0) and month_old == pytest.approx(1.0, rel=0.05)
        assert Ranking.importance(5, 3, "2026-09-20T00:00:00Z", NOW) > fresh
        # legacy offsets and garbage are tolerated
        assert Ranking.importance(0, 0, "2026-10-20T00:00+0200", NOW) > 1.9
        assert Ranking.importance(1, 0, "not a date", NOW) == pytest.approx(0.693, rel=0.01)


class TestTranscriptActivity:
    def test_counts_calls_since_last_graph_write(self, tmp_path):
        path = transcript(tmp_path, ["Read", GRAPH_WRITE, "Bash", "Edit", "Bash"])
        assert TranscriptActivity(path).tool_calls() == ["Read", GRAPH_WRITE, "Bash", "Edit", "Bash"]
        assert TranscriptActivity(path).progress() == (2, 3)

    def test_openai_style_and_missing_file(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text(json.dumps({"role": "assistant", "tool_calls": [
            {"id": "1", "function": {"name": "edit_file"}}, {"id": "2", "function": {"name": "log_session"}}]}))
        assert TranscriptActivity(path).progress() == (2, 0)
        assert TranscriptActivity(tmp_path / "missing.jsonl").progress() == (0, 0)


class TestStopHook:
    def payload(self, path, **extra):
        return json.dumps({"session_id": "s1", "transcript_path": str(path), **extra})

    def test_nudges_once_after_threshold_then_again_only_after_a_write(self, tmp_path):
        hook = StopHook("claude", state_dir=tmp_path / "state")
        quiet = transcript(tmp_path, ["Bash"] * (StopHook.THRESHOLD - 1))
        assert hook.respond(self.payload(quiet)) == ""

        busy = transcript(tmp_path, ["Bash"] * StopHook.THRESHOLD)
        output = json.loads(hook.respond(self.payload(busy)))
        assert output["hookSpecificOutput"]["hookEventName"] == "Stop"
        assert "log_session" in output["hookSpecificOutput"]["additionalContext"]
        assert hook.respond(self.payload(busy)) == ""  # already nudged for this stretch

        after_write = transcript(tmp_path, ["Bash"] * StopHook.THRESHOLD + [GRAPH_WRITE] + ["Edit"] * StopHook.THRESHOLD)
        assert hook.respond(self.payload(after_write)) != ""

    def test_respects_loop_guards(self, tmp_path):
        busy = transcript(tmp_path, ["Bash"] * 40)
        assert StopHook("claude", tmp_path / "a").respond(self.payload(busy, stop_hook_active=True)) == ""
        cursor = StopHook("cursor", tmp_path / "b")
        assert json.loads(cursor.respond(self.payload(busy, loop_count=1))) == {}
        assert json.loads(cursor.respond(self.payload(busy, status="aborted"))) == {}
        assert "followup_message" in json.loads(cursor.respond(self.payload(busy, status="completed")))

    def test_without_transcript_it_stays_silent(self, tmp_path):
        assert StopHook("claude", tmp_path).respond("{}") == ""
        assert StopHook("claude", tmp_path).respond("not json") == ""
