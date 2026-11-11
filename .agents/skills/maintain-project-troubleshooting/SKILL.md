---
name: maintain-project-troubleshooting
description: Maintain verified, domain-specific troubleshooting knowledge for this repository. Use when developing locally, running tests, debugging Codex or tooling issues, deploying or rolling back on jx, integrating Dify, cleaning or migrating data, or diagnosing a failure; query only the relevant domain document before work and automatically promote verified reusable fixes after work.
---

# Maintain project troubleshooting

Use this skill to keep repeatable failure knowledge discoverable without loading every historical report or enlarging `AGENTS.md`.

## Domain routing

Read only the matching section of `docs/troubleshooting/README.md`, then the linked domain file:

- local code, dependencies, packaging, Git or Codex tooling → `development.md`
- pytest, fixtures, runners, browser checks or acceptance metrics → `testing.md`
- SSH, Docker, wheel, migration, health check, release or rollback → `deployment.md`
- Dify App/Workflow, Dataset, DSL, Prompt, Sandbox or RAG → `dify.md`
- PostgreSQL, Redis, uploads, cleaning, privacy, metadata, deletion or rollback data → `data-cleaning.md`

For a cross-domain task, read only the domains that the change can affect. Search the exact error text, command, component and version before reading more.

## Before changing state

1. Record the task domain, current version/environment and the relevant known pitfalls in the task notes.
2. Identify the expected evidence and the safe rollback or cleanup boundary.
3. Do not treat an old symptom as the current cause; verify it against code, configuration and a reproducible check.
4. For remote or destructive work, verify identity, exact project ownership and authorized scope. Follow the deployment modes in `dev-workflow` and project `AGENTS.md`: disposable test data needs no backup or restore rehearsal; production requires preserved live data, prior runnable version/configuration and a complete/readable same-target backup. Stop only the affected action when a required boundary or production recovery prerequisite is missing; do not ask for repeated command approval within an already authorized scope.

## Capture and promote issues

During a task, write a redacted candidate record to `var/reports/<run-id>/issues.jsonl` when that report directory exists. Use fields:

`domain`, `issue_id` (if known), `trigger`, `symptom`, `evidence_path`, `root_cause`, `resolution`, `verification`, `scope`, `sensitive_data_removed`.

After the fix is verified:

- update an existing domain entry when the trigger and cause match;
- add a new stable ID only when the solution is repeatable and useful to the next task;
- keep one-off environment noise in the dated acceptance record only;
- put product/architecture decisions in an ADR and stable repository routing in `AGENTS.md`;
- link the dated evidence, but never copy secrets, tokens,正文、真实用户 ID、原始敏感文件名 or protected mappings.

Never publish a suspected cause or an unverified workaround as a solution. Mark unresolved items as `open` in the run report and leave the living document unchanged.

Within an authorized local repository task, update the matching living document automatically after verification without asking for a second confirmation. This does not authorize commits, pushes, deployments, production writes or destructive cleanup.

## Entry format

Each living entry must contain:

- stable ID and domain;
- applies-to version/environment;
- trigger and observable symptom;
- verified root cause;
- safe resolution and explicit non-solution;
- verification command or observable result;
- prevention/checklist item;
- dated evidence link and last verified date.

Keep entries short. Prefer a link to a detailed dated record over copying the whole incident.

## Completion check

Before handing off a task, confirm:

1. the relevant living document was searched;
2. every new issue is either promoted after verification or retained as redacted dated evidence;
3. no dynamic release facts were added to `AGENTS.md`;
4. `docs/README.md` links any new or renamed troubleshooting document;
5. the domain file still contains no secrets or unverified claims.
