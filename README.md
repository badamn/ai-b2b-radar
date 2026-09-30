# ai-b2b-radar

Agent skill: finds new AI technologies, models and products in free sources (GitHub, Habr, Hugging Face, Hacker News, YC, AINews) and scores their potential as B2B products — need, buyer, value, repeatability and advantage over competitors. Output is a typed JSON digest and a standalone HTML report.

**Usage guide (RU):** [USAGE.md](USAGE.md) — prompts, parameters, output.

## Install

```bash
npx skills add badamn/ai-b2b-radar
```

Pick agents with `-a` (e.g. `-a codex -a cursor`), install globally with `-g`.

### Ouroboros (razzant/ouroboros)

`SKILL.md` carries an Ouroboros `type: script` manifest (`net`, `subprocess`). Output files stay inside `OUROBOROS_SKILL_STATE_DIR`.

Easiest: ask Ouroboros in chat — *"Install the skill https://github.com/badamn/ai-b2b-radar into the external bucket"*. Manual install (do not use `/tmp`: Ouroboros blocks it as a working directory):

```bash
git clone --depth 1 https://github.com/badamn/ai-b2b-radar ~/ai-b2b-radar-src
cp -R ~/ai-b2b-radar-src/ai-b2b-radar ~/Ouroboros/data/skills/external/
```

Then run review and enable it in **Skills**. To update, replace the folder with a newer version and review again.

Requires Python 3.9+ (standard library only) for the collector, scoring and report scripts.

## Check

```bash
python3 -B ai-b2b-radar/scripts/check.py
```

See [ai-b2b-radar/SKILL.md](ai-b2b-radar/SKILL.md) for the workflow and `references/` for the report contract (schema v4) and scoring method.
