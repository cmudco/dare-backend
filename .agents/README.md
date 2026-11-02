# Agent configuration

This directory holds skills for AI coding agents, in the [Agent Skills](https://agentskills.io)
format that Codex, Claude Code and other tools share.

## Layout

```
AGENTS.md                          project instructions (source of truth)
.claude/CLAUDE.md                  imports AGENTS.md for Claude Code
.agents/skills/<name>/SKILL.md     skills (source of truth)
.claude/skills/<name>              symlink -> ../../.agents/skills/<name>
```

Each skill is a directory with a `SKILL.md`: YAML frontmatter (`name`, `description`) and a
Markdown body. Agents match on the description, so it says when the skill applies, not only what
it does.

## Why two directories

Codex reads `.agents/skills/`; Claude Code reads `.claude/skills/`. The real files live in
`.agents/skills/`, and each `.claude/skills/` entry is a symlink to one, so both tools load the
same copy.

## Add a skill

```bash
mkdir -p .agents/skills/my-skill
$EDITOR .agents/skills/my-skill/SKILL.md
ln -s ../../.agents/skills/my-skill .claude/skills/my-skill
```

Commit the directory and the symlink.

## Windows

Symlinks need `core.symlinks=true`, which needs Developer Mode or an elevated shell:

```bash
git config --global core.symlinks true
```

Without it, each `.claude/skills/<name>` checks out as a text file and Claude Code skips the
skill. Codex is unaffected.
