# Adversarial review — Plan 2 (release bundle: publish, ledger hook, gc)

Target: `docs/plans/2026-11-02-plan2-release-bundle.md`
Code reviewed: `src/bench_ops/{models.py,hf.py,status.py,cli.py,config.py,gitx.py,hooks.py}`, `docs/CONTRACT.md`,
`huggingface_hub 2.4.0` in `/Users/arthrod/bench/.venv`, live repo `arthrod/neurotic_docx_bench` (public dataset),
`scripts/publish/upload_r2.py` in the ndb checkout.

Everything below was checked against the installed library and the live Hub (evidence in §E). 22 defects:
**6 critical, 8 major, 8 minor.**

---

## CRITICAL

### D1 — `gc` is not required to take its authority from the HF-pinned manifest; a locally edited manifest authorises a delete
**Severity: critical** · plan §Interface → `gc.py: verdicts(paths, manifests: list[tuple[LedgerEntry, Manifest, Witness]], r2_head)` · §Decisions bullet 6

The contract's rule is *"its sha256 is listed in a release manifest **and** that manifest has been verified against Hugging Face"*.
The interface makes the `Manifest` an *input*. Nothing in it requires (a) the manifest bytes to come from HF, (b)
`sha256(manifest_bytes) == entry.manifest_sha256`, (c) `manifest.tag == entry.tag`, or (d) that the `Witness` was computed
against `manifest.hf.revision`. A `Manifest` is a plain pydantic model (`models.py:32-46`) — it can be constructed from any bytes.

**Failure scenario.** The gc is handed the manifest from the producer's working tree (`release/<tag>/manifest.json`) or from a
manifest that was fetched once and cached. An operator (or a bad merge, or a script) edits one sha256 in it — to the sha of
`~/bench/.work/ndb/run/expensive-output.bin`. The gc sees "sha256 listed in a release manifest" + a `Witness` tuple it was
handed (which was `ok` for the *real* manifest of an unrelated tag) → `hf=True`, `deletable=True` → `--yes` unlinks the only
copy of a 4 GB bench output. Nothing ever touches HF. This is precisely the October-2026 grok_run loss the contract exists
to prevent, reintroduced through the gc.

**Fix.** `gc` must not accept a `Manifest` as an authority-bearing input. Make the type unforgeable by construction:
`VerifiedManifest(entry: LedgerEntry, manifest: Manifest, witness: Witness)` produced only by a function that
(a) downloads `release/<tag>/manifest.json` at `entry.hf.revision` (never from disk), (b) asserts
`sha256_of(downloaded_bytes) == entry.manifest_sha256`, (c) asserts `manifest.tag == entry.tag` and
`manifest.hf.repo == entry.hf.repo`, (d) runs `verify_manifest(manifest, hub)` **at `manifest.hf.revision`** and requires
`witness.ok`. `verdicts()` then accepts only `VerifiedManifest`. A test must assert that a tampered local manifest
produces `deletable=False`.

---

### D2 — the R2 witness is "same size", which is not a witness
**Severity: critical** · §Decisions bullet 6 (`--yes` deletes only files with both witnesses (HF manifest verified; R2 object present with the same size when the file is served)) · `Verdict.r2: bool | None` · `r2_head: Callable[[str], int | None]`

`hf.py`'s own module docstring states the rule the plan breaks: *"The witness never trusts a size or a name."*
The size check is also what the existing producer code does — `scripts/publish/upload_r2.py:102-138` builds
`{key: size}` and `:231-236` verifies "keys + sizes" only — and that uploader **refuses to overwrite an existing key**
(`:194`, "the Worker writes non-overwriting"). So a wrong-bytes object is permanent.

**Failure scenario.** Tag `ndb-v0.0.1` serves `site/convert/page-0042.pdf`. An earlier generation of that page (a re-render
after a font change) happens to be byte-length-identical (very common for PDF: same page count, same embedded font metrics,
different bytes). The uploader skips the key because it exists. `r2_head` returns the size → matches → the gc deletes the
local copy. The site now serves the stale page forever; the local copy is gone; the manifest's sha256 never matched the
served bytes and nothing noticed.

**Fix.** Make the R2 witness content-based:
- R2's list API already returns `etag`/`httpEtag` per object (the current code discards them) — for the single-part
  `PUT` the uploader uses (`upload_api`, `:164`) the ETag is the MD5 of the content, so compare it with the MD5 of the local file.
- Better: upload with `x-amz-checksum-sha256` (R2 supports S3-style checksums) or write the manifest's `sha256` into the
  object's custom metadata, and have `gc` compare **the manifest's sha256** against the stored checksum.
- Store the R2 checksum in `FileEntry` (add `r2_sha256: Sha256 | None = None` next to `served`, `models.py:25-30`) so the
  witness is a manifest-vs-R2 comparison, not a size guess. A size check may remain as a cheap pre-filter, never as the gate.

---

### D3 — "served" is resolved per manifest, so a file served by one tag can be deleted with no R2 witness at all
**Severity: critical** · §Interface `verdicts(...)` · §Decisions bullet 6 · `models.py:25-30` (`FileEntry.served`)

`served` is a per-`FileEntry` boolean and the plan never states the aggregation rule when *several* manifests list the same
sha256. "the same sha in two manifests" is exactly the case where the rule matters:

**Failure scenario.** sha `S` (the bytes of `page-0042.pdf`) is listed in manifest A (`ndb-v0.0.1`) as `served=False`
(it was published as part of the case corpus but the site does not serve it) and in manifest B (`ndb-v0.0.2`) as
`served=True` (the site serves it). `verdicts` iterates the manifest list; the natural implementation takes the **first**
match, computes `r2=None` ("not applicable, not served"), and marks the file deletable with **only one** witness. The file
*is* served by tag B; R2 was never checked; the local copy is deleted. The mirror rule "and against R2 too if the file is
served" is violated.

The mirror-image bug is just as easy: if the implementation ORs the `hf` verdicts but takes `r2` from the last manifest
examined, a file can be deleted with a *stale* R2 verdict computed for a different prefix.

**Fix.** State the rule as an invariant and implement it that way:
> A local file is deletable iff **every** manifest that lists its sha256 has an ok HF witness, **and** for **every** such
> manifest that marks the file `served`, the R2 object for *that manifest's* prefix and *that manifest's* path exists with
> matching content.

Never let `served=False` in one manifest cancel `served=True` in another. Add the three-manifest regression test
(same sha, served in one manifest only) to the plan's acceptance list.

---

### D4 — `stage()` expands globs with no containment, symlink or dotfile policy: the repo's own `.git` and files outside the repo get published to a **public** dataset
**Severity: critical** · §Interface `stage(repo, tag) -> list[StagedFile]` ("expands globs") · §Decisions bullet 2 (`files` (globs, relative to the repo root))

Measured on this machine (Python 3.14, §E.3):
- `Path.glob("../*")` **works** — it returned files from the *parent* of the root (`.../tmpXXXX/../TelemetryUploadFile*.txt`).
  `Path.glob` only rejects absolute patterns.
- `Path.glob("**/*")` **includes** `.git` and `.git/config` (dotfiles are not special to pathlib).
- `Path.glob("**/*.txt")` **matches symlinked files** (`link.txt -> /etc/hosts` was returned); `**` does not descend into
  symlinked *directories*, but a symlinked file is a first-class match and `is_file()` follows it.

**Failure scenarios.**
1. `files = ["**/*"]` (the obvious first attempt) stages `.git/objects/**` — the **entire git history** — plus `.git/config`
   (remote URL; a token embedded in a push URL is a credential), plus `.git/index`, plus every symlink in the tree. All of it
   is uploaded to `arthrod/neurotic_docx_bench`, a **public** dataset (`"private": false` confirmed live, §E.4).
2. A symlink in the tree pointing at `~/.ssh/id_rsa` is followed, hashed and published — and mirrored to the public R2 bucket.
3. `files = ["../*"]` publishes the user's home directory.

**Fix.** In `stage()`, for every glob match, before staging:
- `p.is_symlink()` → **refuse the whole publish** (do not silently skip; a symlink in a release tree is a bug);
- `p.resolve()` must be `is_relative_to(repo_root.resolve())` → else refuse;
- reject any path whose first component is `.git`, `.hg`, `.svn`, or that begins with `.` → refuse (dotfiles are excluded by
  default in git, so excluding them matches the producer's mental model);
- reject absolute glob patterns (`Path.glob` raises `NotImplementedError` — catch it and report, don't crash);
- skip directories, and print the full staged list for the operator to eyeball before commit 1.
Add tests: a symlink to an outside file → publish refuses; `files=["../*"]` → refuses; `files=["**/*"]` → `.git` absent.

---

### D5 — the manifest (commit 2) is written *before* anything proves commit 1's bytes match it: a wrong manifest becomes immutable
**Severity: critical** · §Interface `publish(...)`: "commit 1; manifest; commit 2; witness must be ok; …"

The witness runs **after** commit 2. The manifest is therefore already in the "immutable record" before it is known to be
true. Two concrete ways it is false:

1. **TOCTOU.** `stage()` computes the sha256 of the local file; `CommitOperationAdd` re-reads the file when it is constructed
   (`upload_info` is computed in `__post_init__`) and again when the bytes are sent (`b64content()` / `as_file()` read the
   file). A bench run that is still writing `results/` (or an editor, or a `git checkout` racing the publish) changes the file
   between the two reads → the uploaded bytes ≠ the manifest's sha256 → the witness fails *after* the manifest is published.
2. **Silent skips.** `create_commit` **silently skips** files the Hub's `.gitignore` matches — `_commit_api.py:938-941`
   ("Skipping file … ignored by gitignore file"), driven by the server's per-file `shouldIgnore` flag from the preupload
   endpoint (verified live, §E.5). The commit succeeds; the file is not there; the manifest claims it is.

**Failure scenario.** The manifest for `ndb-v0.0.1` is published listing `results/run/output-0007.bin` with sha `X`, but the
Hub holds different bytes (case 1) or no file at all (case 2). The tag path `release/ndb-v0.0.1/` now holds a manifest that
can never be corrected under that tag (D6). A consumer that reads the manifest by path believes the bundle is on HF.

**Fix.** Reorder the state machine:
```
stage → commit 1 → VERIFY commit 1 against the staged shas (paths_info + hash, at R1)
       → (only now) write the manifest → commit 2 → verify manifest bytes == sha
       → mirror + verify → append ledger → git commit + tag
```
- Build the manifest from the sha256 the uploader **reported** (`CommitOperationAdd.upload_info.sha256`), never from a
  separate pre-read.
- Before uploading, call `preupload_lfs_files`/`_fetch_upload_modes` and **refuse** any file with `shouldIgnore == True`.
- On any mismatch, abort *before* commit 2, and report which file differs.

---

### D6 — the tag path on the Hub is mutable and the plan has no HF-side tag and no "already published" check
**Severity: critical** · §Decisions bullet 1 ("Both revisions are immutable") · bullet 4 ("refuse a tag already in the ledger or as a git tag")

The two *commit shas* are immutable, but the **paths** the contract pins consumers to (`release/<tag>/manifest.json`,
`release/<tag>/scores.json`) live on a branch and can be rewritten by a second publish of the same tag. The plan's
"already published" check consults only the **local ledger** and the **local git tag** — neither of which is the Hub.

**Failure scenario.** commit 2 succeeds → the witness fails (or the R2 mirror fails) → no ledger line is appended → the
operator re-runs `bench publish ndb ndb-v0.0.1` → the tag is in neither the ledger nor git → allowed → a **second** manifest
is written to the same path at a new revision R2'. Two manifests now exist for one tag; the ledger records one; a consumer
that fetches `release/ndb-v0.0.1/scores.json` by path (the CONTRACT's "Consumers pin tags in `bench.lock`" implies
path-based resolution somewhere) gets whichever is at the branch head. "Immutable once a tag's manifest is written"
(`CONTRACT.md`, HF row) is violated by the plan's own retry path.

**Fix.**
- Create an **immutable Hub ref per release**: `api.create_git_ref(repo, "refs/tags/<tag>", R2)` and record that ref in the
  ledger; consumers pin the ref/sha, not a branch path.
- Before commit 1, check `paths_info(repo, "main", [f"release/{tag}/manifest.json"])`; if it exists, **refuse** unless the
  operator passes `--supersede` with a *new* tag (`models.Tag` already allows `ndb-vX.Y.Z.1`).
- Never reuse a tag path.

---

### D7 — no atomicity, no lock, no journal: concurrent publishes lose ledger lines, and a torn line breaks the hook in **every** repo
**Severity: critical** · §Interface `publish(...)`: "append ledger; git commit + tag" · `models.py:56-60` (`LedgerEntry.last`) · `status.py:33-36`

- The ledger append is a read-modify-write of a git-tracked file with no lock and no atomic write.
- `LedgerEntry.last()` does `from_line(lines[-1])` with **no error handling**: a torn last line raises `ValidationError`
  out of `_last_release` → `status.py:63+` → `bench status` crashes. The `release state` pre-commit job
  (`lefthook/common.yml`: `bench status --offline --fail-on-stale --repo .`) runs in **every** repo — one torn line in one
  producer blocks commits everywhere, with a traceback instead of a message.

**Failure scenarios.**
- *Concurrency:* two `bench publish ndb ndb-v0.0.1` (two terminals, or two machines) both see the tag as free, both upload,
  both append → one ledger line is lost, or the file ends up with two lines for one tag / interleaved bytes.
- *Torn write:* a crash, a full disk, or a `kill -9` mid-append leaves a partial JSON line → every `bench status` in the
  workspace raises `ValidationError`.

**Fix.**
- Append with a single `os.write()` on a descriptor opened `O_APPEND` (atomic for a short line), or write the whole file to a
  temp file and `os.replace()` it.
- Take an exclusive lock: `release/.publish.lock` created with `O_CREAT|O_EXCL` for the duration of the publish.
- Make `LedgerEntry.last()` tolerant: skip an unparsable **last** line and report "ledger corrupt: line N" in `bench status`
  rather than raising.
- Write a journal `release/.publish/<tag>.json` recording R1/R2/manifest sha as each step completes, so a crashed publish is
  **resumable** (`bench publish --resume <tag>`) instead of re-run (which D6 shows is not safe).

---

## MAJOR

### D8 — "one commit" for the whole bundle exceeds `create_commit`'s documented limits
**Severity: major** · §Decisions bullet 1 ("Commit 1 uploads every file of the bundle under `release/<tag>/` (one commit, batched; the hub allows 320 commits/h)")

`huggingface_hub/hf_api.py:5048`: *"create_commit is limited to 25k LFS files and a 1GB payload for regular files."*
The ndb checkout has **78,129** files under `results/` (6.9 GB) and **2,266** under `site/` (419 MB) (§E.6).
"one commit" and "batched" also contradict each other in the same sentence, and 320 commits/h is a *commit-count* limit,
not a size limit.

**Failure scenario.** The commit is rejected *after* every LFS object was uploaded (the limit is enforced at commit time, not
upload time): 6.9 GB uploaded, nothing recorded, no chunking, no resume, no cleanup path.

**Fix.** Chunk: ≤ ~2,000 files and ≤ ~500 MB of *regular* payload per commit; keep every chunk revision; set
`Manifest.hf.revision` to the **last** chunk commit (the tree is cumulative, so all files resolve at it — the witness still
works). `Uploader.commit` must return the list of revisions, or the publisher must loop and aggregate.

### D9 — the plan assumes LFS sha256s will be available; for this repo most release files will be **git-stored**, so the witness downloads the whole release
**Severity: major** · §Decisions bullet 1 ("The witness … checks every file at R1") · `hf.py:35-45, 82`

Measured live against `arthrod/neurotic_docx_bench` (§E.2, §E.5):
- `paths_info` → `README.md`: `None` (git-stored); `outputs/docx_to_pdf_v1_apryse/pdf/clean__00163e55a0_….pdf`: `None`
  (git-stored!); `site/redline/r-01b889d1c8/jubarte.pdf`: LFS sha; `bench.jsonl`: LFS sha.
- The server's `preupload` endpoint for the **planned** paths: `release/ndb-v1.0.1/scores.json` → `uploadMode:"regular"`,
  `…/doc.pdf` → `regular`, `…/case.json` → `regular`, `…/page.png` → `lfs` (the repo's `.gitattributes` has a global
  `*.png filter=lfs`), `release/x/big.pdf` (5 MB) → `regular`, `release/x/big2.pdf` (50 MB) → `lfs`,
  `release/x/j.docx` (120 MB) → `lfs`.

The Hub repo's `.gitattributes` carries **per-path** LFS entries for the files that already exist; the new
`release/<tag>/` tree matches no global pattern except png/jpg/bin/zip/… So the plan's implicit cost model is wrong:
for every non-LFS file `hf.py:82` falls back to *download and hash*. Publishing the ndb bundle = upload GBs + **download
GBs**, on the publish path and on every online `bench status`.

**Fix.** Decide storage mode *before* the first publish: add `release/** filter=lfs diff=lfs merge=lfs -text` (or
`*.pdf`/`*.docx`/`*.csv`/`*.json` globals) to the Hub repo's `.gitattributes`, so the witness can use the server's sha256
metadata and never download. If globals are unacceptable, state the download cost in the plan and cap it (the witness must
not be O(bundle) on every `bench status`; see D19).

### D10 — `create_commit` returns a `CommitInfo` whose `str()` is the **commit URL**, not the sha
**Severity: major** · §Interface `Uploader.commit(...) -> str  # returns the new revision` · `models.py:11` (`GitSha`), `models.py:22`

`CommitInfo` is a `str` **subclass** whose string value is the commit URL (`hf_api.py:362-412`); the sha is `.oid`.
Verified: `str(ci)` → `https://huggingface.co/datasets/x/y/commit/abc123`, `ci.oid` → 40 hex.

**Failure scenario.** `Uploader` implementations naturally write `return str(info)`; the manifest then fails
`GitSha` validation with a confusing "string does not match pattern ^[0-9a-f]{40}$" that names the URL. Worse, an
implementation that coerces with `[:40]` produces a *valid-looking* 40-char string that is not a revision → the witness
downloads at a nonexistent revision and reports "missing" for every file.

**Fix.** The protocol must say "the commit **oid**"; the implementation returns `info.oid`; add a test asserting
`re.fullmatch(r"[0-9a-f]{40}", result)` and that the value resolves via `hf_hub_download`.

### D11 — the R2 key layout is undefined and the natural implementation collides
**Severity: major** · §Interface `mirror(files: list[StagedFile], str)` · §Decisions bullet 2 (`served`, `r2_upload`)

The producer's uploader computes `key = f"{prefix.rstrip('/')}/{p.relative_to(root).as_posix()}"` where `root` is the
**directory passed on the command line** (`scripts/publish/upload_r2.py:218`), and the Worker path *refuses to overwrite an
existing key* (`:194`). The plan gives `mirror` a flat list of files and a `served` list of globs that may span directories.

**Failure scenario.** `served = ["site/convert/**", "site/redline/**"]` → the mirror invokes the script once per directory
with prefix `<tag>` → `site/convert/x.pdf` and `site/redline/x.pdf` both map to `<tag>/x.pdf` → the second is refused
(or, on the API path, overwrites) → the site serves the wrong PDF. The plan reports success because the mirror is never
verified (D12).

**Fix.** Define the key as `<r2_prefix>/<repo-relative path>` (one flat namespace, e.g. `<tag>/site/convert/x.pdf`) and change
the uploader to accept an explicit **file list → key map** (or a single root + a key-prefix transform) instead of a directory.
State the rule in `Manifest` (`r2_prefix` is the key prefix, not a URL — see D14).

### D12 — the R2 mirror is never verified during publish, and the existing uploader silently skips file types it does not know
**Severity: major** · §Interface `publish(...)`: "mirror served files (skipped when none)"

`upload_r2.py:98-99` uploads only files whose suffix is in `TYPES` (webp/pdf/json/docx/csv/md/txt/png/svg/html/log/tsv) and
skips `.DS_Store`. A `served` glob matching anything else (`.zip`, `.webm`, `.parquet`) is **silently not uploaded**.

**Failure scenario.** `bench publish` succeeds, the ledger line is appended, the tag is "released", and the site 404s on the
missing object. The failure surfaces only when a user hits the page — or much later, in `bench gc` (which then refuses to
delete, fail-closed but after the fact).

**Fix.** After the mirror, verify **every** served file's key on R2 (the script already has `--verify`; extend it to compare
content, D2) and **fail the publish before the ledger append** if any object is missing or wrong.

### D13 — the `served` flag is derived from config globs, not from what was actually mirrored
**Severity: major** · §Interface `stage(...) -> StagedFile(served: bool)` · `models.py:29`

`served` is computed from the `served` globs, independently of the mirror's actual result. When the two disagree (D11
collision, D12 skipped type), the manifest asserts "served" for something that was never mirrored, or (worse) asserts
"not served" for something the site does serve — which converts "needs the R2 witness" into "R2 witness not applicable" and
opens the D3 delete hole.

**Fix.** Derive `served` from the mirror's **result** (the set of keys the mirror reported as written), not from the
config, and write that set into the manifest. Alternatively, cross-check the flag against the R2 listing before the manifest
is committed.

### D14 — the ledger hook may validate bytes that are not the bytes committed, and the "ledger only grows" rule rests on a hook that is optional by design
**Severity: major** · §Interface: "`bench hook ledger` (reads the staged `release/index.jsonl` and `git show HEAD:release/index.jsonl`)" · §Decisions bullet 3

- "the staged `release/index.jsonl`" is ambiguous. Reading the **working-tree file** instead of the **index** is a hole:
  `git add` a tampered ledger, restore the working tree, commit → the hook validates the good bytes, the commit records the
  bad ones. The index must be read with `git show :release/index.jsonl`.
- `bench publish` appends to the working tree and then commits. If it uses `git commit <path>` (path-limited), git commits
  the **working tree** content of that path, ignoring the index — so index-vs-HEAD validation and the committed bytes can
  differ.
- The hook is optional by design: `lefthook/common.yml` is pulled through `remotes:` and "fails open" (CONTRACT, Hooks);
  `LEFTHOOK=0` skips it; `git commit --no-verify` skips it; a machine that never fetched the remote config has **no jobs at
  all** (only `bench sync` complains). `bench publish` itself never calls `check_ledger` before appending.
- A *deletion* or *rename* of the ledger must also be caught; a `glob:` filter matching a deleted path is a lefthook semantic
  that must be tested, not assumed.

**Fix.** (1) the hook compares `git show :release/index.jsonl` with `git show HEAD:release/index.jsonl`, and also inspects
`git diff --cached --name-status` for a deletion/rename of the ledger; (2) `bench publish` calls `check_ledger` itself before
appending; (3) `bench gc` re-derives the ledger's authority from the Hub (D1), so a rewritten line is caught by the witness,
not by the hook; (4) add a unit test for `check_ledger` over crafted old/new pairs and a config test asserting the job exists
in `common.yml` with the glob.

### D15 — the "tag already published" check is local-only, so two machines can publish the same tag
**Severity: major** · §Interface `publish(...)`: "refuse a tag already in the ledger or as a git tag"

The ledger is read from the local checkout and the tag from the local git. A fresh clone on another machine has neither.

**Failure scenario.** Machine A publishes `ndb-v0.0.1` (ledger committed locally, not pushed — §Decisions bullet 4 says
publish never pushes). Machine B (fresh clone) publishes `ndb-v0.0.1` → allowed → a second manifest at the same Hub path
(D6) and a second ledger line; when A pushes, B's push is rejected as non-fast-forward and the two ledgers diverge for the
same tag.

**Fix.** The authoritative check is the **Hub** (D6): does `release/<tag>/manifest.json` exist at the target branch? Refuse
on that alone; treat the local ledger/git tag as an optimisation, not the guard.

### D16 — a broken witness is not "stale", so `--fail-on-stale` stays green with a broken release
**Severity: major** · `status.py:29-30` (`stale` = `self.witness.startswith("stale")`), `status.py:52-60` (`_describe` →
"N missing"/"N mismatched"), `cli.py:93-140`

For a producer, a failed witness renders as "3 missing" / "2 mismatched" — never "stale" — so `bench status --fail-on-stale`
exits 0. The plan's acceptance #2 ("bench status showing `verified N files on HF`") is therefore an *advisory* signal that
no gate enforces, and the only automated gate (the ledger hook) never talks to the Hub.

**Fix.** Add `--fail-on-witness` (or fold "witness not ok" into `--fail-on-stale`) and call it from the ledger hook and from
`bench publish`'s final verification.

---

## MINOR

### D17 — the plan names the R2 prefix two different ways, and the manifest's `r2_prefix` is a *key prefix* while the config's `r2_public` is a *URL*
**Severity: minor** · §Decisions bullet 2 (`r2_prefix_base`) vs §Interface (`r2_public`, `r2_upload`) vs `models.py:37` (`r2_prefix: str`)

The gc's `r2_head: Callable[[str], int | None]` needs a **key** (bucket + prefix + object path); the config gives a **public
base URL**. They are not derivable from each other (the URL is `https://pub-<account>.r2.dev/`, the key is
`<bucket-prefix>/<path>`), and the bucket is not in the plan at all — the live bucket is
`neurotic-docx-bench-pages` (`upload_r2.py:56`) with `ACCOUNT=5651b2fe…`. Also `Manifest.r2_prefix` is **required**, so a
producer that serves nothing must still invent a value.

**Fix.** One vocabulary, two explicit fields: `r2_bucket`, `r2_key_prefix`, `r2_public_base` (all optional in
`ReleaseSpec`; `r2_prefix` optional in the manifest). State the key formula in the contract:
`key = f"{r2_key_prefix}/{repo_relative_path}"`.

### D18 — `Repo`/`ReleaseSpec` are not strict, so a typo in `[repos.release]` silently disables publishing
**Severity: minor** · `config.py:16-26` (`class Repo(BaseModel)`, default `extra="ignore"`), `config.py:77-84`

`r2_publik` (typo) is dropped silently → `release=None` → "A producer without `[repos.release]` cannot publish" surfaces as
a missing feature instead of a config error, at the worst possible moment (mid-release).

**Fix.** `model_config = ConfigDict(extra="forbid")` on `Repo` and `ReleaseSpec`; validate the release section at load time
with a message naming the offending key.

### D19 — the online witness is O(bundle) on every `bench status`, with no memo
**Severity: minor** · `cli.py:84-90`, `hf.py:73-92`

For a git-stored bundle (D9) every `bench status` re-downloads and re-hashes the entire release (78k paths ⇒ 157
`paths_info` requests per call, plus a download per regular file). The pre-commit hook uses `--offline` today, but the
operator-facing command does not, and `bench publish` runs the witness inline.

**Fix.** Cache the witness result per `(repo, revision, manifest_sha256)` in a local state file; re-run only when the
manifest sha changes. Consider a Merkle root in the manifest so the witness is O(1) metadata.

### D20 — `bench publish` never pushes git, so `bench status` reports tags that do not exist on the remote
**Severity: minor** · §Decisions bullet 4 ("bench publish never pushes git … prints the push command") · `status.py:33-48`

`latest_tags` reads the **local** ledger, so a consumer is told "stale: ndb latest is ndb-v0.0.2" for a tag whose ledger
commit has not been pushed — and the consumer's own lock bump cannot be pushed either, because the producer's commit it
depends on is not on the remote yet. `gitx.clone_or_pull` fast-forwards only clean, non-diverged checkouts, so the producer
stays diverged until the operator pushes.

**Fix.** Print the exact push command **and** the ordering ("push the producer's ledger commit first, then bump
`bench.lock`"); have `bench status` flag a producer whose ledger commit is unpushed (`git log origin/main..HEAD`).

### D21 — `served`/`files` globs can cover `release/**`, so commit 1 can upload a stale manifest and the manifest can list itself
**Severity: minor** · §Interface `stage(...)`

A `files` glob of `release/**` (or a leftover `release/<tag>/manifest.json` from a previous attempt) stages the manifest
into commit 1. The manifest's `files` list would then contain an entry for `release/<tag>/manifest.json` whose sha256 is the
sha256 of a document that contains that sha256 — unsatisfiable by construction.

**Fix.** Exclude `release/<tag>/manifest.json` from the staged set by construction and assert it; reject any `files` glob
that can match under `release/`.

### D22 — `bench gc` may delete paths it should never touch, and `FileEntry.bytes` allows 0-byte entries
**Severity: minor** · §Interface `Verdict(path, sha256, …)`, `models.py:28` (`bytes: int = Field(ge=0)`)

sha256 of zero bytes is a constant, so a 0-byte entry in any manifest matches **every** 0-byte file on disk. And nothing
stops `bench gc release/index.jsonl --yes` (if its sha ever appears in a manifest) or a file a running bench is still
writing.

**Fix.** Refuse to delete anything under a tracked git path (at minimum `release/index.jsonl`, `bench.lock`, `.git/`),
refuse 0-byte candidates, and require the local file's mtime to be older than the manifest's `created`.

---

## Things I checked and found **not** defective (so nobody re-litigates them)

- `create_commit` does return the new revision — but only via `CommitInfo.oid`, not `str(info)` (D10).
- LFS sha256 **is** the content sha256 (verified: downloaded `site/redline/r-01b889d1c8/jubarte.pdf`, sha256 ==
  `paths_info`'s `lfs.sha256`) — the LFS-metadata shortcut is sound *for LFS-stored files* (D9 is about which files those are).
- `hf_hub_download` validates the filename (`file_download.py:1095` → `_local_folder.py:195`), so a hostile manifest path
  cannot escape the cache dir; and `CommitOperationAdd` rejects `..` segments in `path_in_repo` (`_commit_api.py:308-325`).
  Neither protects `stage()`'s *local* side (D4).
- `get_paths_info` ignores missing paths without raising, and `hf.py:35-45` maps that to "missing" correctly — the witness
  logic in `hf.py:73-92` is correct as written; D9/D19 are about cost and storage mode, not about the logic.
- `models.dumps` (sorted keys, 2-space indent, trailing newline) is deterministic and is the right thing to hash — but the
  manifest bytes **uploaded** must be exactly those bytes (see the fix in D5); `model_dump_json()` produces different bytes.

---

## E. Evidence (reproducible)

**E.1 — library facts** (`/Users/arthrod/bench/.venv/lib/python3.14/site-packages/huggingface_hub/`)
- `hf_api.py:5048` — "create_commit is limited to 25k LFS files and a 1GB payload for regular files."
- `hf_api.py:362-412` — `CommitInfo(str)`: string value is the commit **URL**; `.oid` is the 40-hex sha.
- `_commit_api.py:938-941` — `create_commit` **skips** files with `shouldIgnore` (Hub `.gitignore`) with only a log line.
- `_commit_api.py:280-300` — `_local_oid`: sha256 for LFS, **git sha1** for regular files (regular files have no sha256 in the API).
- `_commit_api.py:308-325` — `_validate_path_in_repo` rejects `..` segments and forbidden folders.
- `file_download.py:1095` → `_local_folder.py:195-215` — `_validate_relative_filename` blocks cache-dir escapes.
- `hf_api.py:4270-4344` — `get_paths_info` ignores absent paths (no exception).

**E.2 — live `paths_info` against `arthrod/neurotic_docx_bench` @ `a3bbe053c3ec4e07d5c58697f1e8fca10542839f`** (via `bench_ops.hf.HfHub`)
```
README.md                       lfs_sha=None
site/redline/r-01b889d1c8/jubarte.pdf   lfs_sha=e838969c7681de175756…
outputs/docx_to_pdf_v1_apryse/pdf/clean__00163e55a0_….pdf  lfs_sha=None      <-- git-stored PDF
bench.jsonl                     lfs_sha=3b1a5f892b141873411e…
```
downloaded `jubarte.pdf` → sha256 == the reported LFS sha256 (**match**).

**E.3 — pathlib glob semantics (Python 3.14, this machine)**
```
glob **/*     -> ['.git', '.git/config', 'link.txt', 'linkdir', 'sub', 'sub/a.txt']
glob *        -> ['.git', 'link.txt', 'linkdir', 'sub']
glob **/*.txt -> ['link.txt', 'sub/a.txt']            # link.txt is a symlink to /etc/hosts
glob ../*     -> ['/var/folders/.../tmpXXXX/../.com.openai.codex.*', ...]   # escapes the root
glob /etc/*   -> NotImplementedError: Non-relative patterns are unsupported
```

**E.4 — the target dataset is public**: `GET /api/datasets/arthrod/neurotic_docx_bench` → `"private": false`,
`"gated": false`, 67,715 files, `sha a3bbe053…`, and the only dotfile in the tree is `.gitattributes` (no `.gitignore` today).

**E.5 — Hub `preupload` (authoritative storage-mode oracle), POST /api/datasets/…/preupload/main**
```
release/ndb-v1.0.1/scores.json  uploadMode=regular
release/ndb-v1.0.1/doc.pdf      uploadMode=regular
release/ndb-v1.0.1/case.json    uploadMode=regular
release/ndb-v1.0.1/page.png     uploadMode=lfs      (global *.png filter=lfs)
release/x/big.pdf  (5 MB)       uploadMode=regular
release/x/big2.pdf (50 MB)      uploadMode=lfs
release/x/j.docx   (120 MB)     uploadMode=lfs
release/x/c.csv | d.docx | e.jsonl | f.tsv | g.txt | h.log | i.json  -> regular
```
The repo's `.gitattributes` (27,395 lines) has global LFS for `*.png *.jpg *.gif *.bin *.zip *.parquet …` and **per-path**
entries for existing files only; `*.pdf`, `*.docx`, `*.json`, `*.csv` are **not** global → new `release/<tag>/` paths are
git-stored, and their sha256 is *not* available from the API.

**E.6 — ndb checkout size** (`/Users/arthrod/temp/T/neurotic_docx_bench`): `results/` 6.9 GB / **78,129** files;
`site/` 419 MB / 2,266 files; `out/` 596 MB; `corpus/` 1.4 GB.

**E.7 — R2 uploader** (`scripts/publish/upload_r2.py`): `listing()` returns `{key: size}` only (`:102-138`, the `etag` is
discarded); `keys = f"{prefix}/{p.relative_to(root)}"` (`:218`); the Worker path refuses to overwrite (`:194`);
`--verify` compares keys + sizes only (`:231-236`); `local_files()` only accepts the suffixes in `TYPES` (`:98-99`).

---

## Priority order for the implementer

1. D1 + D2 + D3 (the delete path — a delete can happen with no authority, wrong bytes, or no R2 witness).
2. D4 + D5 + D6 + D7 (the publish path — leaks, wrong immutable manifests, mutable tag paths, lost ledger lines).
3. D8 + D9 + D10 + D11 (the HF/R2 mechanics the plan gets factually wrong).
4. D13–D16 (mirror verification, hook correctness, gate behaviour).
5. D17–D22 (spec hygiene).

No part of this review required changing anything on disk in `/Users/arthrod/bench`; the only writes were to
`/tmp/p2-review.md` and read-only API calls (the `preupload` endpoint is non-mutating).
