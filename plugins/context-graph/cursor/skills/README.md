The Cursor plugin points its `skills` path here, so that Cursor doesn't load the Claude Code skills
in `../../skills/`, whose commands use Claude-only variables. The usage policy reaches Cursor as an
always-on rule (`../rules/context-graph.mdc`) instead.
