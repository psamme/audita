# Agents working in this repo

Read `docs/CODEX_BRIEF.md` before doing anything. The rules that matter most:

1. Never open anything under `keys/` or any `runs/*/grades*.json`. They hold hidden answer keys.
2. Nothing under `shadow/` may import from `sim/`, read `sim/POLICIES.md` or `data/*/truth.db`, or contain client-specific knowledge.
3. Never push to `main`. Work in your own clone on a `codex/<topic>` branch and open a PR.
4. Anything that calls the LLM costs money. Prefer tests and `--no-llm` runs; ask before large runs.
5. Respect the cut list in the brief. No em dashes in docs or UI copy.
6. The product is called **Audita** everywhere a person reads it: page titles, the `.mark` link in every header, UI copy, README and submission text. Never write "Shadow Onboarding" in new pages or copy. Internal names do not change: the repo, the `shadow/` package, the `so-` storage keys and the `SO` JS global stay as they are, so do not rename them. `tests/test_company_routes.py` asserts the brand string in the served page.
