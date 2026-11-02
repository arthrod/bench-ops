# Plan 3 — `@jandira-tech/bench-viewer`

Issue: jandira-tech/neurotic_docx_bench#115. Tracking: arthrod/bench-ops#8.

## What the survey found (changes the scope)

- jubarte.pro already renders the leaderboard from data shaped exactly like `Scores` (`jubarte-site/site/pages/benchmark.ts` `row()`, headlines, states; `pages/home.ts` bar groups). This is the markup bench-viewer must reproduce **byte for byte**, so Plan 4 is a swap with no visual change.
- The gh-pages site (`ndb/site/index.html`, 18 MB) is **not** a leaderboard. It is a per-case browser with all case data inlined; its "scores table" is one row per case, built from case data that `scores.json` does not carry. The plan 1 acceptance "snapshots equal the gh-pages table markup" cannot hold. bench-viewer reproduces the jubarte markup only; the case browser stays as it is.
- `renderCaseStrip` needs per-case data (a `cases/*.json` contract that does not exist yet). Dropped from this plan (YAGNI); it comes with the cases contract if Plan 4 needs it.
- Rewriting gh-pages as an orphan commit means force-pushing a public branch. `build:pages` builds the directory; the push stays a manual step Arthur approves.

## Decisions

- Package at `ndb/packages/bench-viewer`, ESM, TypeScript, no runtime dependencies, pnpm workspace member. vitest for tests, biome for lint and format (scoped to the package).
- Ships compiled JavaScript: `tsc` builds `dist/` (`.js` + `.d.ts`) and `exports` points there. Node refuses to strip types from files under `node_modules` (ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING), and jubarte-site runs its build with plain `node`. Consumers install it before npm publication as a git dependency: `github:jandira-tech/neurotic_docx_bench#<sha>&path:packages/bench-viewer` (pnpm), so `dist/` is committed or built in `prepare`.
- Pure functions returning HTML strings. Every text value goes through `esc`. Numbers formatted exactly as jubarte does (`toFixed(2)`, `toLocaleString("en-US")`).
- One markup, two skins: class names are the jubarte ones (`bench-table`, `t-row`, `bench-cols`, `bar`, `bar-cell`, `headline`, `state-row`, `bar-row`…). `themes/tokens.css` declares the custom properties the components read; `themes/jubarte.css` is the bench rules lifted from `jubarte-site/site/static/css/site.css`; `themes/arthur-law.css` maps the same classes onto the arthur.law tokens (`--bg #FAFAF8`, `--ink #0A0A0A`, `--mark #FFD400`, Libre Franklin + Roboto Mono, square cells, 1px rules; source `benchmarks-law/src/styles/site.css`).
- `types.ts` mirrors `bench_ops.models.Scores` field for field; `parseScores(json: unknown): Scores` validates shape at runtime (no zod: hand-written guards, throws `TypeError` naming the bad path).
- `scripts/build-pages.ts <scores.json> <outdir>` writes a self-contained `index.html` (leaderboard, arthur-law theme, badge `bench <version> · generated <date>`) and copies the CSS. No git operations.

## Interface (`src/index.ts`)

```ts
export type { Scores, Table, Row, Headline, StateCell, StateRow, HomeGroup, HomeBar, MethodPoint } from "./types.ts";
export function parseScores(json: unknown): Scores;
export function esc(s: string): string;                        // & < > " '
export function renderRow(r: Row): string;                     // == jubarte benchmark.ts row(r)
export function renderTable(t: Table): string;                 // == jubarte's <section id=…> block for one table
export function renderHeadlines(h: Headline[]): string;        // == the <div class="cells cols-auto-200 mt-56"> block
export function renderStates(s: StateCell[], label?: (name: string) => string): string; // == the states <section>
export function renderBars(groups: HomeGroup[]): string;       // == home.ts bench-groups inner markup
export function renderMethod(m: MethodPoint[]): string;        // == the METHOD <section>
export function renderBadge(s: Scores, tag?: string): string;  // <p class="bench-badge">bench <tag|version> · generated <date></p>
export function renderLegend(): string;                        // "† author-affiliated · failures count as 0"
```

## Acceptance

- Golden tests: for a fixture `Scores` (`test/fixtures/scores.json`, taken from jubarte's current `bench.*` facts), each render function's output equals a golden string produced by jubarte-site's own code for the same data (`test/golden/*.html`, generated once by `test/golden.ts`, which holds a verbatim copy of jubarte's template code — `row()` and the inline template blocks are not exported — with the jubarte-app path, line range and commit SHA in a header comment, and is committed).
- `esc` is applied to every text field (a test injects `<script>` in each field and finds no raw tag).
- `parseScores` rejects a missing field and a wrong type with the path in the message.
- `pnpm --filter @jandira-tech/bench-viewer test` and `biome check` pass; `build-pages` output opens in a real browser with the arthur-law skin.
- Publishing to npm is a manual `pnpm publish` Arthur runs (outward-facing).
