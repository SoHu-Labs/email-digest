# Implementation Plan — email-digest (merged repo)

**Agent / LLM implementers:** each slice in this document MUST use the section checklist and phrasing rules in **`docs/AGENT_PLAN_CONTRACT.md`** (invariants, permissions table, caveats four-liner, follow-up table, acceptance commands). Underspecified slices are invalid handoffs.

The `unsubscribe` project was renamed to `email-digest`. The existing Gmail API backend, façade, and utilities in `src/unsubscribe/` are **shared** by both the unsubscribe and digest features. This plan covers adding the digest engine.

## Implementation progress (canonical status — read this first)

**Last updated:** 2026-05-13 — bump this date whenever you change **Remaining scope** or ship a user-visible slice.

This section is the **continuity contract** for any implementer (human or LLM): status here overrides informal chat. Named slices below hold acceptance tests and invariants; this section holds **what is done vs what is still in scope**.

### Shipped (summary)

| Milestone / slice | Delivered (high level) |
|-------------------|-------------------------|
| M2–M4 (narrative in doc) | Digest engine: `src/email_digest/*`, topics YAML, templates, pipeline, cache, LLM, HTML, Spark links, optional Gmail send — **§7 retro (R8):** **Slice: M2 — Legacy milestone**, **Slice: M3 — Legacy milestone**, **Slice: M4 — Legacy milestone** under historical headings |
| **M5** | CLI: `version`, `cost`/`--json`, `topics`, `run`/`--all`/`--strict`, deferred Gmail for `--all` when nothing runs, failure logs, cron example |
| **C** | `docs/LM_STUDIO_DIGEST.md`, `resolve_model_alias()` in `llm.py`, tests |
| **D** | Extra Spark URL encoding regression tests (contract frozen in CI) |
| **R5 (tooling)** | **`digest spark-check`** prints a **`readdle-spark://`** URL for paste; **`docs/SPARK_DEVICE_CHECK.md`** runbook — on-device **F2** sign-off remains **Open** in **Remaining scope** |
| **B** | `is_digest_source_candidate()` → delegates to `is_unsubscribable_newsletter` |
| **A** | `digest candidates <topic>` — Gmail list + JSON, no LLM |
| **R2** | `digest candidates --all` — sorted `*.yaml`, one OAuth when any topic lists, deferred Gmail when every YAML fails before list, JSON mix of `{topic,file,query,rows}` and `{topic,file,error}`, exit **1** if any failure |
| **R1** | `digest keep add|remove|merge` — same `~/.unsubscribe_keep.json` as run/unsubscribe; `merge_keep_list()` in `keep_list.py`; exit **1** on bad `--from` / merge file |
| **R4** | **`digest walkthrough <topic>`** — terminal step-through of digest-source candidates only; **[Enter]** → **`add_to_keep_list`**; **[s]** skip; **[q]** quit; exit **130** on interrupt (same spirit as **`unsubscribe`**). No HTML body prefetch (optional **R4+** follow-up). |
| **R4+** | **`digest walkthrough --body`** — parallel plain-text body prefetch (**ThreadPoolExecutor**, same pattern as **`unsubscribe`**); body preview shown per message; no body fetch when flag omitted; kept senders excluded from prefetch |
| **R4++** | **`digest walkthrough --all`** — multi-topic walkthrough in sorted YAML filename order; two-phase scan + deferred Gmail (same pattern as `run --all` / `candidates --all`); config/strict errors printed to stderr, walkthrough continues to next topic; exit **1** if any topic failed, **130** on interrupt |
| **R4+++** | **Walkthrough shortlist** — numbered one-line summaries (`From : Subject :: snippet`) printed before the per-message loop, giving an overview of all candidates at a glance  |
| **R6** | **`topics/*.yaml`** — removed **`TODO-`** sender placeholders; RFC2606 **`example.com`** newsletter-shaped addresses + multi-sender examples; CI guard: no **`todo-`** in any topic **`senders`** list |
| **R7** | **`cheap` alias for MiniMax via OpenCode Go** — env-var-configurable model (`CHEAP_MODEL`, default `openai/minimax-m2.5`), base URL (`CHEAP_API_BASE`, default `https://opencode.ai/zen/go/v1`), API key (`CHEAP_API_KEY` or auto-read from OpenCode `opencode-go` block in `~/.local/share/opencode/auth.json`); injects `api_base`/`api_key` for litellm OpenAI-compatible endpoint; `resolve_model_alias` returns the active model string |
| **E** | `digest candidates` adds `sender_key`, `keep_list_kept`, `--keep-list` (parity with `digest run` gate) |
| **F** | Each `run_digest` / dry-run **`messages[]`** item includes **`digest_source_candidate`** (same classifier as slice B / `digest candidates`) |
| **G** (R3b) | When `digest_source_candidate` is false: **no** `get_message_html`, **no** `llm_complete`, **no** `put_extraction_json`; empty `key_claims` / `entities` / `numbers`. Cached extractions still honored. |

**Acceptance command for “repo still healthy”:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.

### Remaining scope (prioritized)

**Order for implementers:** **Pick** 1–2 below are **manual / deferred** work. There is **no** remaining mandatory docs retrofit for M2–M4 (**R8** shipped as §7 blocks under those headings).

| Pick | ID | Item | Type | Status | Next step when resuming |
|------|----|------|------|--------|---------------------------|
| 1 | R5 | **Spark scheme on-device check** | manual | **Verified (F2)** | `readdle-spark://` URL opens Spark on device; **`spark_link.py`** contract confirmed — no patch needed |

### Out of scope (unless plan is amended)

- Parallel Gmail collector (IMAP, etc.) — brief forbids
- Second keep-list / digest-only persistence file — brief forbids

### How to update this document when you ship work

1. Move or shrink rows in **Remaining scope**; add a row to **Shipped** (or extend an existing shipped row).
2. Set **Last updated** to the merge/commit date.
3. Add or refresh the **named slice** block (§7 template) for the work you merged; link it from **Shipped** if not already listed.

---

## Repo structure (target)

```
src/
  email_digest/              # NEW — digest engine
    __init__.py
    llm.py                   # litellm provider (DeepSeek + Claude + LM Studio)
    pipeline.py               # orchestrator
    embed.py                  # sentence-transformers
    cluster.py                # HDBSCAN
    spark_link.py             # readdle-spark:// deeplinks
    render.py                 # Jinja2 → HTML
    cache.py                  # SQLite cache
    config.py                 # YAML topic config loader
  unsubscribe/               # existing (shared Gmail API, facade, utils)
    gmail_api_backend.py      # SHARED: Gmail API read
    gmail_facade.py           # SHARED: Protocol + Facade
    timed_run.py              # SHARED: progress timer
    keep_list.py              # SHARED: JSON persistence
    classifier.py
    execution.py
    ... (existing files unchanged)
topics/                       # NEW
  ai.yaml
  health.yaml
templates/                    # NEW
  digest.html.j2
tests/
  test_spark_link.py          # NEW
  test_cluster.py             # NEW
  ... (existing tests unchanged)
src/unsubscribe/cli.py        # MODIFIED: add 'digest' subcommand
```

---

## M2: LLM module + fetch email candidates

### Step 1: Install litellm

Add `litellm` to the existing mamba env and `requirements.txt`.

### Step 2: `src/email_digest/llm.py`

```python
import mlx_lm
import litellm

_MODELS = Path.home() / ".lmstudio" / "models"

MLX_MODEL_VARIANTS = {
    "qwen3": str(_MODELS / "lmstudio-community" / "Qwen3-4B-Instruct-2507-MLX-4bit"),
    "0.8b":  str(_MODELS / "mlx-community" / "Qwen3.5-0.8B-MLX-4bit"),
    "2b":    str(_MODELS / "mlx-community" / "Qwen3.5-2B-MLX-4bit"),
    "4b":    str(_MODELS / "mlx-community" / "Qwen3.5-4B-MLX-4bit"),
}

MODEL_ALIASES = {
    "fast":  "deepseek/deepseek-v4-flash",
    "smart": "deepseek/deepseek-v4-pro",
    # local / local_smart: direct MLX (mirrors local-chat MODEL_VARIANTS).
    # No env vars — paths are hardcoded from ~/.lmstudio/models/.
    "local":       "2b",   # Qwen3.5-2B-MLX-4bit
    "local_smart": "4b",   # Qwen3.5-4B-MLX-4bit
    "cheap": "openai/minimax-m2.5",   # OpenCode Go endpoint
}
# local/local_smart bypass litellm entirely; fast/smart use DeepSeek API; cheap uses OpenCode Go.
# Lazy-loaded MLX singleton per variant (see local-chat/src/llm.py for the MlxLlm pattern).

def complete(
    messages: list[dict],
    alias: str = "smart",
    *,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> str:
    ...
```

### Step 3: `src/email_digest/config.py`

Port `_load_yaml()` from swim. Load `TopicConfig` frozen dataclass from `topics/<name>.yaml`.

### Step 4: Fetch email candidates (like unsubscribe workflow)

Reuse `src/unsubscribe/gmail_api_backend.py::GmailApiBackend.list_messages` to fetch emails matching sender patterns. Show the user a list of candidates. User selects which to keep (inverse of unsubscribe: kept = digest sources). Persist selected senders in the **same keep file as unsubscribe** (default **`~/.unsubscribe_keep.json`**, `DEFAULT_KEEP_LIST_PATH` in `src/unsubscribe/cli.py`); **no** separate digest persistence path.

### Step 5: Unified CLI — `src/unsubscribe/cli.py`

Add `digest` subcommand to the existing unsubscribe CLI:

```bash
python -m email_digest digest run ai
python -m email_digest digest run --all
python -m email_digest digest run ai --dry-run
python -m email_digest digest run ai --since 2026-05-01
python -m email_digest digest cost        # LLM cost last 7 days
python -m email_digest digest topics       # list / validate topic YAMLs

# Existing unsubscribe commands remain:
python -m email_digest unsubscribe list
python -m email_digest unsubscribe dry-run
...
```

---

## M2 verification

```bash
python -m email_digest digest run ai --dry-run
# Should: fetch emails matching ai.yaml senders, dump JSON of extracted emails
```

---

## M3: Embedding cache + clustering

### `src/email_digest/cache.py` — SQLite

Tables: `extractions`, `embeddings`, `llm_calls`

### `src/email_digest/embed.py` — sentence-transformers

`all-MiniLM-L6-v2`, cache by claim hash.

### `src/email_digest/cluster.py` — HDBSCAN

Group claims by embedding similarity. Output trending themes.

---

## M4: Synthesis + HTML render

### Extraction prompt

```
Extract structured data from this email. Output JSON:
{
  "key_claims": [ ... ],   // 5-10 bullets
  "entities": [ ... ],
  "numbers": [ ... ]
}
```

### Synthesis prompt — use persona_prompt from topic YAML

### `src/email_digest/spark_link.py`

```python
from urllib.parse import quote

def spark_deeplink(message_id: str) -> str:
    return f"readdle-spark://openmessage?messageId={quote(message_id, safe='')}"
```

### `src/email_digest/render.py` + `templates/digest.html.j2`

Jinja2, single HTML file, inline CSS, dark mode default.

### Gmail API send for `also_email_to`

Send the rendered HTML with `users.messages.send` using the same OAuth token as read (`GOOGLE_OAUTH_TOKEN`); token must include **`gmail.send`** in addition to **`gmail.readonly`**. `"self"` resolves to `users.getProfile` → `emailAddress`.

---

## Plan status (scope outside individual slices)

Canonical rows for **done vs remaining** live in **Implementation progress** at the top of this file. The table below is a short index.

| Area | Status | Notes |
|------|--------|--------|
| M2–M4 (digest engine, cache, synthesis, HTML) | **Shipped** in repo | Narrative sections only; see **Implementation progress → Shipped**. |
| M5 | **Shipped** | See **Slice: M5** |
| Slices C, D, B, A, E | **Shipped** | See **Implementation progress** + Post-M5 slice headings |
| **Remaining** | See **Implementation progress → Remaining scope** | Do not duplicate long lists here—edit the **Remaining scope** table there. |

---

## M5: Cron, polish, cost dashboard

### Slice: M5 — Cron, polish, cost dashboard

- **Goal:** Operators can run digests on a schedule with predictable exit codes, machine-readable cost usage, validated topic YAMLs, and resilient per-message error logging—without loading Gmail OAuth when no topic work will run.

- **Non-goals:** Changing LLM models, Spark URL scheme, unsubscribe behavior, default topic YAML content, or widening Gmail query semantics. No live Gmail or live LLM calls in the default CI test suite.

- **Invariants:**
  - `python -m email_digest --version` and `python -m email_digest digest version` print the installed **`unsubscribe`** distribution version from `pyproject.toml` (exit **0**).
  - `digest cost` and `digest cost --json` exit **0** even when the cache file is missing; JSON includes `cache_missing`, `days`, `cache_db`, aggregate `calls` / `input_tokens` / `output_tokens` / `cost_usd`, plus **`by_alias`** and **`by_model`** arrays (same shapes as `email_digest.cache.cost_report_payload`).
  - `digest topics` exits **0** on success, **1** on invalid YAML or `--strict` stem mismatch, **2** if `--topics-dir` is missing or not a directory. `--json` emits a JSON array of `{ name, file, display_name }`; text mode prints tab-separated `name` and `display_name` per line.
  - `digest run <topic>`: success prints one JSON object and exits **0**; missing/invalid YAML, `--strict` stem mismatch, or pipeline exception prints `{ "topic", "file", "error" }` and exits **1**; malformed `--since` exits **2** with stderr. **Single-topic** config/strict failures MUST NOT call `GmailApiBackend.from_env`.
  - `digest run --all`: prints a JSON array in **sorted `*.yaml` filename order**; elements are either normal pipeline dicts or `{ "topic", "file", "error" }`; exit **1** if any topic failed, **0** if none failed. **Gmail OAuth MUST NOT load** when there is no topic that reaches `run_digest` (empty topics dir, all config failures, or all `--strict` stem mismatches).
  - Per-message collect/extract failures append one line to `<output_dir>/_failures/<YYYY-MM-DD>.log` (fields tab-separated: UTC timestamp, topic, Gmail message id, exception type, message); the topic run continues.

- **Coupling:** `src/email_digest/cli.py`, `src/email_digest/cache.py`, `src/email_digest/pipeline.py`, `scripts/digest-cron.example.sh`, `README.md` (CLI / cron section), `tests/test_digest_cli.py`, `tests/test_digest_pipeline.py`, `tests/test_cache.py` (if cost payload shape changes).

- **Preconditions:** Branch with M4 pipeline merged; env **`email-digest`** (`mamba`); `pip install -e ".[dev]"` from repo root. Secrets: **`GOOGLE_OAUTH_TOKEN`** (path), optional **`DEEPSEEK_API_KEY`**, **`DIGEST_CACHE_DB`** — names only.

- **Permissions & environment:**

| Class | Rule |
|--------|------|
| **Network** | MUST NOT require live Gmail or LLM in CI; tests use mocks / tmp SQLite / tmp dirs. |
| **Filesystem** | MAY write under `tmp_path`, `tests/`, `cache/`, `output/` in tests; example cron script documents repo paths. |
| **Git** | No `Co-authored-by` or `--trailer`; if hooks inject trailers, commit with empty `core.hooksPath` for that invocation. |
| **Shell** | Acceptance uses `mamba run -n email-digest python -m pytest tests/ -q` from repo root. |
| **Credentials** | No token values in repo or plan. |

- **Caveats & footguns:**
  1. **Symptom:** Cron job touches OAuth even when every topic failed at YAML load. **Cause:** `digest run --all` called `from_env` before the per-file loop. **Wrong fix:** skip OAuth only in tests. **Right fix:** build an ordered action list (error vs run); call `from_env` only if at least one `run` action exists (see `cli._digest_run`).
  2. **Symptom:** `digest cost --json` consumers break. **Cause:** Renaming or dropping keys in `cost_report_payload`. **Wrong fix:** partial JSON tests. **Right fix:** extend `tests/test_digest_cli.py` (and `test_cache.py` if needed) whenever the payload contract changes.
  3. **Symptom:** Strict mismatch still hits Gmail. **Cause:** `from_env` ordered before strict check on single-topic path. **Wrong fix:** assert in docs only. **Right fix:** keep config + strict validation before `from_env` for single-topic; tests assert `from_env` not called.
  4. **Symptom:** Failure log not found. **Cause:** `_failures` uses `output_dir` default vs override. **Wrong fix:** hard-code `output/`. **Right fix:** pass `output_dir` through CLI to `run_digest` consistently; test uses `tmp_path`.

- **Procedure:**
  1. Ensure `digest version` / `--version`, `digest cost [--json]`, `digest topics [--json|--strict]`, `digest run` / `--all` / `--strict`, per-message `_failures` logging, and `scripts/digest-cron.example.sh` match **Invariants** above.
  2. For `digest run --all`, implement ordered two-phase handling so Gmail loads only when at least one `run_digest` is required; preserve JSON array order.
  3. Add or adjust tests for Gmail skip on `--all` with zero runnable topics and for empty topics dir (`from_env` not called).
  4. Align README CLI/cron copy with behavior.
  5. Run full fast test suite.

- **Acceptance:** From repo root, `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.

- **Follow-ups:**

| ID | Item | Type | Blocker for next slice? |
|----|------|------|-------------------------|
| F1 | ~~LM Studio model id alignment~~ → Shipped: direct MLX, no env vars | code | no |
| F2 | On-device verification of Spark `readdle-spark://` scheme | manual | no |
| F3 | Sender allowlist UX (`~/.unsubscribe_keep.json`) | product | no |

**Post-M5:** Any new behavior needs its **own** named slice using the §7 template in `docs/AGENT_PLAN_CONTRACT.md` before implementation; do not extend M5 retroactively.

Implementation order for digest follow-ups (**smallest scope first**): **Slice C → Slice D → Slice B → Slice A** (each closes or narrows plan follow-ups F1–F3 where noted).

### Slice: C — ~~LM Studio operator runbook~~ → Shipped: direct MLX, see `src/email_digest/llm.py` MLX_MODEL_VARIANTS

- **Status:** Shipped. `local` / `local_smart` now load Qwen models directly via `mlx_lm` (matching `local-chat/src/llm.py`). No LM Studio HTTP endpoint, no env vars needed. Model paths are hardcoded in `MLX_MODEL_VARIANTS`.
- **Goal:** ~~Operators can see exactly which litellm model string digest aliases `local` / `local_smart` resolve to from the environment~~
- **Invariants:** `MODEL_ALIASES` keys `fast`, `smart`, `local`, `local_smart` unchanged. `local` → `"2b"`, `local_smart` → `"4b"`. `_resolve_model` returns full paths for diagnostics. `complete` behavior unchanged at call site.
- **Coupling:** `src/email_digest/llm.py`, `docs/LM_STUDIO_DIGEST.md` (new), `README.md` (one link under Credentials), `tests/test_llm_resolve_alias.py` (new).
- **Preconditions:** M5 merged; env `email-digest`; `pip install -e ".[dev]"`.
- **Permissions & environment:**

| Class | Rule |
|--------|------|
| **Network** | MUST NOT (no LM Studio in tests). |
| **Filesystem** | Doc + tests only under repo. |
| **Git** | No trailers; empty `core.hooksPath` on commit if hooks inject. |
| **Shell** | `mamba run -n email-digest python -m pytest tests/ -q` → **0**. |

- **Caveats & footguns:**
  1. **Symptom:** Docs list folder names but LM Studio shows different strings. **Cause:** UI vs on-disk path mismatch. **Wrong fix:** hard-code UI strings in Python. **Right fix:** runbook tells operator to copy the **Local Server** model id into env vars.
  2. **Symptom:** `resolve_model_alias` diverges from `complete`. **Cause:** duplicate resolution logic. **Wrong fix:** two code paths. **Right fix:** one public wrapper calling existing `_resolve_model`.

- **Procedure:** 1) Add `resolve_model_alias` in `llm.py` delegating to `_resolve_model`. 2) Add `docs/LM_STUDIO_DIGEST.md` (env vars, id copy steps, Qwen preset targets, `digest cost` pointer). 3) Link from README. 4) Tests for env override + `local_smart` fallback chain.
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** none (F1 narrowed to “operator follows runbook”; no code blocker).

### Slice: D — Spark deeplink contract (tests + frozen URL shape)

- **Goal:** The `readdle-spark://openmessage?messageId=<url-encoded RFC822>` contract is encoded in tests so accidental regressions fail CI; documentation states encoding rules.
- **Non-goals:** Changing the URL scheme or query key without on-device verification (F2); adding Spark app automation.
- **Invariants:** `spark_deeplink` return shape unchanged for existing callers; empty/whitespace input → `""`; non-empty uses `quote(..., safe='')` on the full RFC822 string (angle brackets included).
- **Coupling:** `tests/test_spark_link.py`, `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md` (this slice), `README.md` only if the slice adds a one-line pointer to tests as contract.
- **Preconditions:** Slice C optional merge order independent.
- **Permissions & environment:** Same as C (pytest only).

- **Caveats & footguns:**
  1. **Symptom:** Links open wrong thread. **Cause:** double-encoding or stripping angle brackets. **Wrong fix:** guess encoding. **Right fix:** tests assert decoded `messageId` equals raw input `mid` (existing); add reserved-character cases only if they round-trip.

- **Procedure:** 1) Add tests for characters that require percent-encoding beyond angle brackets (e.g. `&`, space). 2) No `spark_link.py` change unless a test proves a bug (none expected).
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** F2 remains manual device check.

### Slice: B — Digest-source classification helper

- **Goal:** Digest code can ask “does this header row look like a list/newsletter source?” using the **same** heuristics as unsubscribe’s `is_unsubscribable_newsletter`, without duplicating logic (brief: digest needs the inverse at **keep-list** semantics; classification signal is the same bulk/list shape).
- **Non-goals:** Wiring classification into `run_digest` filtering (that belongs in slice A or a later pipeline slice); changing `is_unsubscribable_newsletter` behavior; body HTML prefetch in CI.
- **Invariants:** `is_unsubscribable_newsletter` unchanged; new public API is a thin wrapper with an explicit digest-oriented name; all existing `test_classifier.py` tests still pass.
- **Coupling:** `src/unsubscribe/classifier.py`, `tests/test_digest_classifier.py` (new), `docs/INVENTORY.md` (optional one-line update).
- **Preconditions:** Slice D merged or parallel (no code dependency).
- **Permissions & environment:** pytest only; no Gmail.

- **Caveats & footguns:**
  1. **Symptom:** Digest marks personal mail as candidate. **Cause:** copied heuristics diverged from unsubscribe. **Wrong fix:** fork logic. **Right fix:** delegate to `is_unsubscribable_newsletter` only.

- **Procedure:** 1) Add `is_digest_source_candidate(...) -> bool` delegating to `is_unsubscribable_newsletter`. 2) Tests: equality to newsletter helper on representative headers; body-link flag parity.
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** ~~Pipeline may later filter or rank by this flag~~ → **Slice F** annotates pipeline messages; **R3b** tracks optional filter/down-rank.

### Slice: A — `digest candidates` CLI (topic-scoped list + classification JSON)

- **Goal:** For a topic YAML, list Gmail messages matching the digest query and emit **JSON** with per-row `digest_source_candidate` (from slice B) plus headers needed to decide keep-list updates, without running extraction/LLM.
- **Non-goals:** Interactive TUI; mutating keep list from this command; `--all` topics in one invocation (defer); synthesis/HTML.
- **Invariants:** Exit codes align with `digest run` where applicable: missing topic file / config error → **1** with stderr or JSON error shape per CLI style; invalid `--since` → **2**; success → **0** + stdout JSON array. Gmail loads only after config parse succeeds (same spirit as single-topic `run`). JSON keys stable: at least `id`, `from`, `subject`, `date`, `rfc_message_id`, `digest_source_candidate` (**slice E** adds `sender_key`, `keep_list_kept`, and **`--keep-list`**).
- **Coupling:** `src/email_digest/cli.py`, `tests/test_digest_cli.py`, `README.md` (CLI one-liner), `docs/INVENTORY.md` (CLI table row optional).
- **Preconditions:** Slice B merged (import `is_digest_source_candidate`); `headers_from_summary` from `gmail_facade`.
- **Permissions & environment:** Tests mock `GmailApiBackend.from_env` / `GmailFacade`; no live Gmail in CI.

- **Caveats & footguns:**
  1. **Symptom:** Classification always false. **Cause:** `headers_from_summary` omits fields classifier needs. **Wrong fix:** re-fetch full messages. **Right fix:** map `GmailHeaderSummary` fields into the same header dict shape as unsubscribe uses (`headers_from_summary` already includes List-Unsubscribe when present).
  2. **Symptom:** OAuth on typo topic name. **Cause:** `from_env` before config load. **Wrong fix:** swallow errors. **Right fix:** load YAML first, then `from_env` (mirror `digest run` single-topic ordering).

- **Procedure:** 1) Add `digest candidates <topic>` argparse + `_digest_candidates`. 2) build query via `build_digest_gmail_query`; `facade.list_messages`. 3) Emit JSON array. 4) Tests with mocks. 5) README example.
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** Interactive keep-list merge (F3 remainder); `digest candidates --all` (optional).

### Slice: E — `digest candidates` keep-list preview fields

- **Goal:** Each `digest candidates` JSON row shows whether the message’s **From** address is already in the shared keep file (same semantics as `run_digest` ingestion) plus a normalized **`sender_key`** for scripting, without mutating the keep file from this command.
- **Non-goals:** CLI flags to add/remove keep entries; changing `run_digest` filtering; avoiding `load_keep_list`’s “create empty JSON if missing” behavior (must match pipeline).
- **Invariants:** Exit codes unchanged from slice A. Every successful candidates row includes **`sender_key`** (`string` or JSON **`null`** when `From` cannot be parsed) and **`keep_list_kept`** (`bool`, same as `is_kept(keep_list, from_)`). **`--keep-list`** defaults to the same path as **`digest run`** (`~/.unsubscribe_keep.json`).
- **Coupling:** `src/email_digest/cli.py`, `tests/test_digest_cli.py`, `README.md`, `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md`.
- **Preconditions:** Slice A merged.
- **Permissions & environment:** Same as slice A (mocked Gmail in CI).

- **Caveats & footguns:**
  1. **Symptom:** `keep_list_kept` always false. **Cause:** wrong `--keep-list` path vs the file used for `digest run`. **Wrong fix:** guess home path. **Right fix:** pass explicit `--keep-list` or document default parity with `digest run`.
  2. **Symptom:** `sender_key` null for valid mail. **Cause:** malformed `From` not parseable by `parseaddr`. **Wrong fix:** substring hacks. **Right fix:** treat as null and false for keep (matches `sender_key` / `is_kept` contract).

- **Procedure:** 1) Add `--keep-list` to `digest candidates` (default = `DEFAULT_KEEP_LIST_PATH`). 2) After topic config + strict checks, `keep = load_keep_list(path)`; each row adds `sender_key` and `keep_list_kept`. 3) Tests with two mocked rows and a temp keep file. 4) README one-line note.
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** Optional `digest keep add …` slice (mutating).

### Slice: F — Pipeline messages carry `digest_source_candidate`

- **Goal:** Every message object in `run_digest` / `run_digest_dry_run` output includes **`digest_source_candidate`** (same boolean as `digest candidates` / slice B) so JSON consumers can rank or filter without a second Gmail list call. **No** change to keep-list gating, query construction, or extraction **policy** in this slice (annotation only).
- **Non-goals:** Skipping LLM extraction, dropping rows from `messages`, or changing trending inputs; mutating keep list.
- **Invariants:** `messages[i]` always includes **`digest_source_candidate`** (`bool`) once the message is appended (success path). Classifier uses **`headers_from_summary`** only (no body prefetch). Existing keys on each message unchanged.
- **Coupling:** `src/email_digest/pipeline.py`, `tests/test_digest_pipeline.py`, `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md` (this slice + **Implementation progress**).
- **Preconditions:** Slice B merged.
- **Permissions & environment:** pytest only; mocked Gmail / LLM as today.

- **Caveats & footguns:**
  1. **Symptom:** Flag disagrees with `digest candidates` for same id. **Cause:** different header fields available in `GmailHeaderSummary` vs list response. **Wrong fix:** duplicate classifier logic. **Right fix:** same `headers_from_summary` + `is_digest_source_candidate` path as CLI.
  2. **Symptom:** Trending changes when annotation added. **Cause:** accidentally filtering `out_messages` in the same change. **Wrong fix:** ship silent behavior change. **Right fix:** this slice is metadata-only; extraction skip is **Slice G**.

- **Procedure:** 1) After extraction resolved for a kept row, `h = headers_from_summary(m)`; append `digest_source_candidate=is_digest_source_candidate(h)`. 2) Tests: two rows same keep key, one with List-Unsubscribe → true/false split. 3) Update **Implementation progress**.
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** **Slice G** — skip body fetch + LLM when `digest_source_candidate` is false (R3b).

### Slice: G — Skip extraction for non-`digest_source_candidate` rows (R3b)

- **Goal:** Save Gmail body fetches and extraction LLM calls for kept senders whose **list** metadata does not classify as a digest-source candidate, while still returning the row in **`messages`** with an empty structured extraction. **Cached** extractions from SQLite are unchanged (still loaded and used when present).
- **Non-goals:** Removing rows from `messages`; changing keep-list or Gmail query; synthesis-only skips (handled implicitly when `key_claims` empty).
- **Invariants:** If `digest_source_candidate` is **false** and there is **no** cache hit, the code **must not** call `facade.get_message_html`, **`llm_complete`**, or **`put_extraction_json`** for that message. Extraction object is exactly `{"key_claims": [], "entities": [], "numbers": []}`. If cache hit, behavior matches pre–slice G. **`digest_source_candidate`** on the row still reflects headers-only classification.
- **Coupling:** `src/email_digest/pipeline.py`, `tests/test_digest_pipeline.py`, `README.md`, `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md`, `docs/INVENTORY.md`.
- **Preconditions:** Slice F merged.
- **Permissions & environment:** pytest only; no live Gmail/LLM.

- **Caveats & footguns:**
  1. **Symptom:** User expects extraction for a kept newsletter. **Cause:** missing `List-Unsubscribe` in list metadata so candidate is false. **Wrong fix:** widen classifier in this slice. **Right fix:** fix topic/query or headers; or clear cache after fixing metadata.
  2. **Symptom:** Stale empty row after sender becomes “real” newsletter. **Cause:** empty extraction not cached, then metadata improves—should pick up LLM on next run. **Right fix:** no cache row for skip path; next run calls LLM when candidate true.

- **Procedure:** 1) Branch `elif not digest_source_candidate` before `get_message_html`. 2) Assert `llm_complete` call count in two-row test. 3) Update **Implementation progress** (remove R3b from **Remaining**; add **G** to **Shipped**).
- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** Optional flag to **disable** skip (policy toggle) if operators need body fetch anyway—new slice.

---

### Slice: R4+ — Walkthrough body prefetch (`--body` flag)

- **Goal:** When **`--body`** is passed, **`digest walkthrough`** fetches plain-text message bodies in parallel (ThreadPoolExecutor, same pattern as **`unsubscribe`**) and displays a preview alongside From/Subject/Date during the interactive prompt, giving the operator more context before deciding to keep a sender.
- **Non-goals:** Refactoring **`unsubscribe/cli.py`** internals or making **`_body_preview_lines`** / **`_start_body_prefetch`** shared; **`--all`** topics walkthrough (separate follow-up → **Slice R4++**); changing the prompt keys or keep-list semantics; HTML rendering in the terminal.
- **Invariants:** Without **`--body`**, walkthrough behavior is **identical** to pre-slice R4 (no Gmail **`get_message_body_text`** calls). With **`--body`**, each non-kept candidate message gets one **`facade.get_message_body_text(id)`** call (via thread pool); fetch errors print a stderr line but do not stop the walkthrough. Exit codes unchanged (**0**, **1**, **130**). ThreadPoolExecutor is shut down with **`cancel_futures=True`** on early quit or interrupt. **`_BODY_PREFETCH_WORKERS`** = 8 (same as unsubscribe). Preview width 72, max 5 lines (same as unsubscribe).
- **Coupling:** `src/email_digest/walkthrough.py`, `src/email_digest/cli.py` (**`--body`** flag), `tests/test_digest_walkthrough.py`, `README.md`, `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md`, `docs/INVENTORY.md`.
- **Preconditions:** Slice R4 merged; env **`email-digest`**; `pip install -e ".[dev]"`.

- **Permissions & environment:**

| Class | State |
|--------|--------|
| **Network** | MUST NOT (mock Gmail in CI) |
| **Filesystem** | MAY write keep JSON under **`tmp_path`** in tests |

- **Caveats & footguns:**
  1. **Symptom:** Walkthrough slow even without **`--body`**. **Cause:** prefetch started unconditionally. **Wrong fix:** always prefetch. **Right fix:** only start ThreadPoolExecutor when **`--body`** is set.
  2. **Symptom:** Body fetch hangs for one message. **Cause:** Gmail API timeout. **Wrong fix:** no timeout, block forever. **Right fix:** **`get_message_body_text`** already wraps errors via **`GmailFacade`**; print stderr and continue with "(could not load body)" preview.
  3. **Symptom:** Unsubscribe **`_body_preview_lines`** imported and then unsubscribe refactors it. **Cause:** cross-module private import. **Wrong fix:** import private names from **`unsubscribe.cli`**. **Right fix:** self-contained **`_body_preview_lines`** in **`walkthrough.py`** (same logic, independent maintenance).
  4. **Symptom:** Already-kept senders trigger body fetches. **Cause:** prefetch started on full candidate list before filtering kept. **Wrong fix:** prefetch kept bodies and discard. **Right fix:** load keep list before the pool, filter to non-kept only, then start prefetch.

- **Procedure:**
  1. Add **`_BODY_PREFETCH_WORKERS = 8`**, **`_PREVIEW_WIDTH = 72`**, **`_PREVIEW_MAX_LINES = 5`** constants to **`walkthrough.py`**.
  2. Add **`_body_preview_lines(text, *, width=72, max_lines=5) -> str`** to **`walkthrough.py`** (same wrapping logic as **`unsubscribe.cli._body_preview_lines`**).
  3. Add **`_fetch_one_body_plain(facade, message_id) -> str`** to **`walkthrough.py`** (try/except, return **`""`** on error, print stderr).
  4. Add **`_start_body_prefetch(facade, messages) -> tuple[ThreadPoolExecutor, dict[str, Future[str]]]`** to **`walkthrough.py`**.
  5. Add **`body: bool = False`** parameter to **`run_digest_walkthrough`**.
  6. When **`body=True`**, load keep list, filter candidates to non-kept, start prefetch pool for those; during loop, **`body_futures[m.id].result()`** and show preview; **`finally: pool.shutdown(wait=False, cancel_futures=True)`**.
  7. When **`body=False`**, no pool, no body fetch, display unchanged from pre-slice R4.
  8. Add **`--body`** flag to **`walk_p`** argparse in **`cli.py`**; pass **`body=ns.body`** to **`run_digest_walkthrough`**.
  9. Tests: (a) **`--body`** not set → **`get_message_body_text`** not called; (b) **`--body`** set with two candidates → body shown in output, both bodies fetched; (c) body fetch error → stderr line, walkthrough continues; (d) early quit with **`--body`** → pool shutdown; (e) kept sender excluded from prefetch → only non-kept msg called; (f) `_body_preview_lines` unit tests.
  10. Update README CLI block, INVENTORY, Implementation progress.

- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:**

| ID | Item | Type | Blocker? |
|----|------|------|----------|
| R4++ | **`digest walkthrough --all`** (multi-topic) → **Slice R4++** | feature | no |
| R4+++ | ~~Snippet-based one-line summary in shortlist (before per-message walkthrough)~~ → **Shipped** | UX | no |

### Slice: R4++ — Walkthrough `--all` (multi-topic)

- **Goal:** Operators can run **`digest walkthrough --all`** to step through every topic in sorted YAML filename order in one session, reusing a single Gmail façade, with config/strict errors printed to stderr and walkthrough continuing to the next topic—same two-phase scan + deferred Gmail pattern as **`digest run --all`** and **`digest candidates --all`**.
- **Non-goals:** Changing single-topic walkthrough behavior; JSON output (walkthrough is interactive, not machine-parsed); parallel topic walkthroughs (sequential only); **`--body`** semantics (passes through unchanged).
- **Invariants:** **`--all`** scans topics in **sorted `*.yaml`** filename order. Config/strict errors printed to stderr as `"Walkthrough — topic … (file): error"` — walkthrough continues to next topic. **`GmailApiBackend.from_env`** called only when at least one topic is runnable. Exit **1** if any topic failed (config/strict error or walkthrough non-zero return), **0** if all succeeded (including empty topics dir), **130** on **`KeyboardInterrupt`**. **`--since`** and **`--body`** pass through unchanged. Single-topic mode unchanged: requires `topic` arg and exits **2** if missing.
- **Coupling:** `src/email_digest/cli.py` (`_digest_walkthrough`, `walk_p` argparse), `tests/test_digest_walkthrough.py`, `README.md`, `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md`, `docs/INVENTORY.md`.
- **Preconditions:** Slices R4 and R4+ merged; env **`email-digest`**; `pip install -e ".[dev]"`.

- **Permissions & environment:**

| Class | State |
|--------|--------|
| **Network** | MUST NOT (mock Gmail in CI) |
| **Filesystem** | MAY write keep JSON under **`tmp_path`** in tests |

- **Caveats & footguns:**
  1. **Symptom:** Tests hang on **`--all`** walkthrough. **Cause:** **`input_fn=input`** expects real stdin. **Wrong fix:** remove **`--all`** tests. **Right fix:** patch **`builtins.input`** with **`return_value="s"`** in CLI tests.
  2. **Symptom:** OAuth prompt on cron when all YAML broken. **Cause:** **`from_env`** before scanning actions. **Wrong fix:** swallow init errors. **Right fix:** two-phase scan first; call **`from_env`** only when **`need_gmail`** (same as **`digest run --all`**).
  3. **Symptom:** Walkthrough stops after first topic error. **Cause:** early return on walkthrough non-zero. **Wrong fix:** ignore all errors. **Right fix:** track **`any_failed`**, continue to next topic, return **1** at end if any failed.

- **Procedure:**
  1. Add **`--all`** flag to **`walk_p`** argparse; update `topic` help text to mention `--all`.
  2. In **`_digest_walkthrough`**, guard **`not ns.all and not ns.topic`** (was `not ns.topic`); print error mentioning `--all` → exit **2**.
  3. Add **`--all`** branch: two-phase scan (`_ERR` / `_RUN` sentinels), deferred Gmail loading, config/strict errors to stderr, sequential walkthrough calls.
  4. Track **`any_failed`** for config/strict errors and walkthrough non-zero returns.
  5. Propagate **`KeyboardInterrupt`** as exit **130**.
  6. Tests: (a) no topic and no `--all` → exit 2 with `--all` mention; (b) `--all` empty dir → exit 0, no from_env; (c) `--all` all config errors → exit 1, no from_env; (d) `--all` mixed success/error → exit 1, from_env called once; (e) `--all` both topics good → exit 0; (f) `--all` invalid since → exit 2 before scan.
  7. Update README CLI block, INVENTORY, Implementation progress.

- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:**

| ID | Item | Type | Blocker? |
|----|------|------|----------|
| R4+++ | ~~Snippet-based one-line summary in shortlist (before per-message walkthrough)~~ → **Shipped** | UX | no |

### Slice: R7 — `cheap` alias for MiniMax via OpenCode Go

- **Goal:** Operators can use MiniMax models through OpenCode Go's OpenAI-compatible endpoint (`https://opencode.ai/zen/go/v1`) for low-cost extraction included in the Go subscription ($10/mo), via a **`cheap`** alias in topic YAML (`extract_model: cheap`), without embedding a second provider SDK — reuses litellm's existing `openai/` model prefix with custom `api_base` and `api_key`.
- **Non-goals:** Changing default DeepSeek aliases; adding live API calls in CI; bundling a second SDK; full MiniMax model catalog in the codebase.
- **Invariants:** **`MODEL_ALIASES["cheap"]`** defaults to `"openai/minimax-m2.5"`. **`CHEAP_MODEL`** env var overrides the model id. **`CHEAP_API_BASE`** env var overrides the endpoint (default `"https://opencode.ai/zen/go/v1"`). **`CHEAP_API_KEY`** env var supplies the key; if unset, falls back to reading the `opencode-go` block from OpenCode's **`~/.local/share/opencode/auth.json`** (tries `key` then `apiKey` sub-keys). Existing aliases (`fast`, `smart`, `local`, `local_smart`) unchanged. **`resolve_model_alias("cheap")`** returns the active model string.
- **Coupling:** `src/email_digest/llm.py`, `tests/test_llm_resolve_alias.py`, `README.md` (credentials + CLI), `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md`, `docs/INVENTORY.md`.
- **Preconditions:** Go subscription at `https://opencode.ai/auth`; API key auto-detected from `opencode-go` block in auth.json after running `opencode /connect`.
- **Permissions & environment:**

| Class | State |
|--------|--------|
| **Network** | MUST NOT (no live API calls in tests) |
| **Filesystem** | MAY read `auth.json` via `tmp_path`-monkeypatched tests |

- **Caveats & footguns:**
  1. **Symptom:** `cheap` calls fail with auth error. **Cause:** No Go API key set. **Wrong fix:** hard-code a key. **Right fix:** run `opencode /connect` for OpenCode Go, or export `CHEAP_API_KEY` from `https://opencode.ai/auth`.
  2. **Symptom:** Topic YAML uses `extract_model: cheap` but `synthesize_model: smart` — DeepSeek key still required. **Cause:** `_require_deepseek_key_if_needed` only checks for `deepseek` in model string. **Wrong fix:** remove key check. **Right fix:** set DeepSeek key separately; `cheap` alias does not conflict.
  3. **Symptom:** `digest cost --json` shows "unknown" cost for cheap calls. **Cause:** litellm may not have pricing for custom `openai/` models. **Wrong fix:** patch cost tables. **Right fix:** cost will be `null` in JSON (harmless); pre-calculated pricing requires litellm update.

- **Procedure:**
  1. Add `"cheap": "openai/minimax-m2.5"` to **`MODEL_ALIASES`** in `llm.py`.
  2. Add `cheap` branch to **`_resolve_model`** reading `CHEAP_MODEL` env var with default fallback.
  3. Add **`_read_opencode_zen_auth_key`** — reads auth.json for `opencode-go` block (also `opencode`/`zen`/`opencode-zen` for Zen compatibility), tries `key` then `apiKey` sub-keys.
  4. In **`complete()`**, when `alias == "cheap"`: inject `api_base` from `CHEAP_API_BASE` env (default `https://opencode.ai/zen/go/v1`), `api_key` from `CHEAP_API_KEY` env or `_read_opencode_zen_auth_key()` fallback.
  5. Tests: `resolve_model_alias("cheap")` default; env override; fallback to default when env unset; `_read_opencode_zen_auth_key` for missing file, no-match, `opencode`/`zen`/`opencode-zen`/`opencode-go` blocks, `apiKey` field.
  6. Update README credentials table.
  7. Update INVENTORY and Implementation progress.

- **Acceptance:** `mamba run -n email-digest python -m pytest tests/ -q` → exit **0**.
- **Follow-ups:** None (R7 resolved).

---

## RESOLVED QUESTIONS

1. ~~Minimax/cheap~~ → **Shipped (R7):** `cheap` alias for MiniMax via OpenCode Go (`openai/minimax-m2.5` default, `https://opencode.ai/zen/go/v1` endpoint, env-var-configurable).
2. ~~Gmail OAuth token~~ → Same as billing-glugglejug, `GOOGLE_OAUTH_TOKEN` env var
3. ~~Gmail API porting~~ → Already in this repo (src/unsubscribe/)
4. ~~Sender allowlists~~ → Use unsubscribe-style candidate selection workflow
5. ~~Repo name~~ → email-digest (unsubscribe renamed)

## STILL OPEN

Cross-check with **Implementation progress → Remaining scope** (canonical). Bullets here are non-normative reminders.

- **Local LLM** — `local` / `local_smart` use direct MLX (see `MLX_MODEL_VARIANTS` in `src/email_digest/llm.py`). Defaults: 2B / 4B Qwen3.5 MLX. No env vars.
- **Spark URL scheme** — ship `readdle-spark://openmessage?messageId=<url-encoded RFC822 Message-ID>`; **`digest spark-check`** + **`docs/SPARK_DEVICE_CHECK.md`** for paste verification (**F2 / R5**); adjust `spark_link.py` if Readdle changes.
- **Sender selection** — `digest candidates` + keep flags (**A, E**); **`digest keep`** (**R1**); **`digest walkthrough`** (**R4**); `digest run` / dry-run **`messages[]`** carry **`digest_source_candidate`** (**F**).
