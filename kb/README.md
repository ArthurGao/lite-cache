# lite-cache KB

The project's knowledge base, kept in this repository (owner's choice, design §0). The design says
what is built and why, and holds the owner's decisions (§0) and the project status (its first line).
This directory keeps what the design does not hold well.

| Page | What |
| --- | --- |
| `eval-results.md` | lite-eval runs: L1 / L2 hit rate, false-hit rate, tokens saved, chosen thresholds (from S2); S3b L2 calibration (2026-11-20: L2 stays off) |
| `lessons.md` | traps met while building it, each with the check that now catches it |

Rules:
- Record the implemented state; one conclusion per page.
- Numbers carry the date, the commit and the lite-eval run id.
- No keys, no customer material.
- A lesson that holds beyond this project also goes to
  `/Users/arthurgao/Docs/Knowledgebase/global/engineering/` (skill `kb-maintain`).
