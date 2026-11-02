# Plan 5 — benchmarks.law consumes the ndb and et releases

Issue: arthrod/benchmarks-law#72. Depends on Plan 2 (ndb release), Plan 3 (bench-viewer), Plan 6 (et release). Tracking: arthrod/bench-ops#8.

## Revision 2 (Arthur, 2026-11-02)

The case browser, speed page and leaderboard all render from the ndb release bundle through bench-viewer (Plan 3b), so `public/neurotic-docx-bench/` (the 22 MB copy, `patchNeuroticHtml`) is deleted outright, not moved to `/cases/`. Page images stay on R2 as today (the bundle lists their URLs). `bench deploy blaw` is restored: `bench fetch --check`, then `pnpm run deploy`.

Before the copied viewer is deleted, both versions run locally side by side for Arthur: the current `main` (old copied viewer) and the branch (bundle-rendered), each with `pnpm dev` (or `wrangler dev`) on its own port, same pages listed, so he compares them in a browser and approves the deletion. gh-pages force-push: approved by Arthur (2026-11-02) once `build:pages` output has been shown the same way.

The sections below are revision 1; where they disagree with this paragraph, this paragraph wins.

## What the survey found (revision 1)

- `public/neurotic-docx-bench/` (22 MB, 13 tracked files) is three different things:
  1. `index.html`, `redlines/`, `speed/`, `_static/`, `data.json` (13 MB, a plain git blob): the **per-case browser**, the gh-pages viewer restyled in the arthur.law design. It is not a leaderboard and `scores.json` cannot replace it.
  2. `editors/index.html`: the **editors-trial** scorecard, rebuilt from editors_trial's report by `rebuildScoreTable` (`src/lib/scorecard.ts`). It belongs to the `et` producer, not ndb.
  3. `home/index.html`: a landing page.
  `patchNeuroticHtml` only fixes dead editors_trial links in these copies.
- Moving the 21 MB of case-browser data out of git needs a served copy (R2) with a content witness. Plan 2 made the R2 mirror fail closed, so that move waits for the R2 witness. Until then the case browser stays tracked; the size guard only blocks new or growing files.

## Decisions

- `bench.lock` at the repo root: `ndb=<tag> manifest=<sha>` and `et=<tag> manifest=<sha>`; `bench fetch` fills `.bench/ndb/` and `.bench/et/` (Plan 4 tooling).
- New leaderboard page `src/pages/neurotic-docx-bench/index.astro`: renders `.bench/ndb/scores.json` with bench-viewer (`renderBadge`, `renderHeadlines`, `renderTable` per table, `renderStates`, `renderMethod`, `renderLegend`) and `themes/arthur-law.css`. The case browser moves from `/neurotic-docx-bench/` to `/neurotic-docx-bench/cases/` unchanged (a redirect keeps old links).
- The editors scorecard is generated from `.bench/et/facts.json` at build time instead of patching a copied HTML file: `rebuildScoreTable` and the Markdown-table parsing are deleted with `editors/index.html`.
- `src/lib/root.ts`: `.bench/et/` is the default source; `REPORTS_ROOT` stays as a dev override. A fresh clone with no sibling checkouts builds.
- `patchNeuroticHtml` is deleted once nothing it patches is left; until the case browser moves to R2 it stays, scoped to the case browser.

## Acceptance

- Fresh clone, no sibling checkouts: `bench fetch && pnpm build` passes.
- `/neurotic-docx-bench/` shows the same figures and the same tag as jubarte.pro/benchmark, in the arthur.law skin (checked in a real browser, side by side).
- `/neurotic-docx-bench/cases/` and the old URL still open the case browser.
- Deploy stays `pnpm run deploy` (Arthur's call).
