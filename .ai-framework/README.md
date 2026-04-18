# .ai-framework — Bundled Framework Reference

This folder contains a local copy of the **AI Task Generation Documentation Framework** so that everything you need — templates, prompts, and guides — lives inside your project.

## What's Inside

```
.ai-framework/
├── VERSION                    # Framework version (for upgrade tracking)
├── README.md                  # This file
├── templates/                 # Full reference templates (detailed guidance)
│   ├── persona.md
│   ├── stakeholder.md
│   ├── architecture.md
│   ├── claude-md.md
│   ├── data-model.md
│   ├── api-spec.md
│   └── ui-specification.md
├── prompts/                   # Prompt templates for AI task generation
│   ├── base-template.md
│   ├── feature-tasks.md
│   ├── bugfix-tasks.md
│   ├── refactor-tasks.md
│   ├── spec-generation.md
│   ├── ui-spec-generation.md
│   ├── mockup-generation.md
│   ├── plan-generation.md
│   ├── compile-adrs.md
│   └── compile-ddrs.md
└── guides/                    # Workflow guides
    ├── getting-started.md
    ├── context-compilation.md
    ├── maintenance.md
    └── release-lifecycle.md
```

## How to Use

- **Don't edit these files.** They are framework reference material. Edit your project docs instead (`CLAUDE.md`, `docs/`).
- **Use templates as reference** when filling in your project docs — they contain detailed guidance and examples for every section.
- **Use prompts** to generate AI tasks from your documentation:
  - **AI agents (Claude Code, etc.):** Read the routing table in CLAUDE.md — it lists which files to read and which prompt template to use for each task type. Read files directly; no copy-paste needed.
  - **Chat workflows:** Copy the prompt structure from `.ai-framework/prompts/`, paste in your project docs as context.
- **Use ADR compilation** to pre-fill templates from a shared Architecture Decision Records repo — select ADRs for your tech stack, run `compile-adrs.md`, and paste the output into your project docs.
- **Use DDR compilation** to pre-fill the Design System from a shared Design Decision Records repo — select a profile or individual DDRs, run `compile-ddrs.md`, and paste the output into your UI spec and CLAUDE.md.
- **Use guides** for workflow help — getting started, assembling context, and maintaining docs.

## Upgrading

The `VERSION` file tracks which framework version is installed. To upgrade:

1. Check the latest version of `ai-task-framework-v2/`
2. Replace this `.ai-framework/` folder with the updated `scaffold/.ai-framework/`
3. Your project docs (`CLAUDE.md`, `docs/`) are unaffected — only the reference material updates
