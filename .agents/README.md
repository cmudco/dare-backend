# Agent configuration

This directory holds configuration for AI coding agents, in the
[Agent Skills](https://agentskills.io) open format that Codex, Claude Code, and other tools share.

## Layout

```
AGENTS.md                          project instructions (source of truth)
CLAUDE.md                          imports AGENTS.md, plus Claude Code specifics
.agents/skills/<name>/SKILL.md     skills (source of truth)
.claude/skills/<name>              symlink -> ../../.agents/skills/<name>
```

Skills are one directory each, holding a `SKILL.md` with YAML frontmatter (`name`, `description`)
and a Markdown body. The description is what an agent matches against to decide when to load the
skill, so it should say when the skill applies — not just what it does.

## Why two directories

The two tools scan different paths and neither reads the other's:

| Tool        | Scans                          |
| ----------- | ------------------------------ |
| Codex CLI   | `.agents/skills/<name>/SKILL.md` |
| Claude Code | `.claude/skills/<name>/SKILL.md` |

`.agents/skills/` holds the real files. Each entry under `.claude/skills/` is a symlink to its
`.agents/skills/` counterpart — Claude Code follows the symlink and reads `SKILL.md` from the
target. One copy of every skill, both tools pick it up.

## Adding a skill

```bash
mkdir -p .agents/skills/my-skill
$EDITOR .agents/skills/my-skill/SKILL.md
ln -s ../../.agents/skills/my-skill .claude/skills/my-skill
```

Commit both the directory and the symlink.

## Windows

Git stores symlinks natively, but checking them out on Windows needs `core.symlinks=true`, which
requires either Developer Mode or an elevated shell:

```bash
git config --global core.symlinks true
```

Without it, each `.claude/skills/<name>` entry checks out as a plain text file containing the link
target, and Claude Code will not find the skill. Codex is unaffected — it reads `.agents/skills/`
directly.
