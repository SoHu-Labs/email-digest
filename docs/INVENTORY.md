# Code Inventory — email-digest

This repo was created by merging the former `unsubscribe` project. Gmail API + façade live in `src/unsubscribe/`; digest email (when enabled) uses the same OAuth token and `users.messages.send`, not SMTP. The digest engine is `src/email_digest/`. Milestone plan: `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md`. **Plan slices** MUST follow `docs/AGENT_PLAN_CONTRACT.md` so handoffs are machine-checkable.

---

## Already in repo (reusable as-is)

| File | Purpose | Used by |
|---|---|---|
| `src/unsubscribe/gmail_api_backend.py` | Gmail API: `list_messages`, `get_message_html`, `get_message_body_text`, `get_profile_email`, `send_html_email`, helpers, OAuth (`gmail.readonly` + `gmail.send`), threaded fetch | Both |
| `src/unsubscribe/gmail_facade.py` | `GmailBackend` Protocol, `GmailFacade` error-wrapping, `GmailHeaderSummary` (Gmail id, RFC822 `Message-ID` when listed, From, Subject, Date, List-Unsubscribe, snippet). | Both |
| `src/unsubscribe/timed_run.py` | `TimedRun` monotonic progress timer, `format_progress_line` | Both |
| `src/unsubscribe/keep_list.py` | JSON persistent set (`load`, `save`, `add`, `remove`, **`merge_keep_list`**, `is_kept`) — **same file as unsubscribe** (`~/.unsubscribe_keep.json` by default in `cli.py`); digest uses **inverse semantics** (kept senders = digest sources, not unsubscribe targets). No second persistence file. | Both |
| `src/unsubscribe/classifier.py` | `is_unsubscribable_newsletter` / `is_digest_source_candidate` (delegates to the same heuristics; digest `candidates` subcommand). | Both |

## Digest engine (shipped under `src/email_digest/`)

Entry points: `python -m email_digest` (`__main__` → `cli.main`), console script `email-digest` in `pyproject.toml`. **`digest run --all`** defers `GmailApiBackend.from_env()` until at least one topic reaches `run_digest` (see M5 in the implementation plan).

| File | Purpose |
|---|---|
| `cli.py` | `digest` subcommands: `version`, `cost`, `topics`, `candidates` / `candidates --all`, `keep` (`add`/`remove`/`merge`), **`walkthrough`**, **`spark-check`**, `run` (`--all`, …); passes through `unsubscribe` argv |
| `walkthrough.py` | `run_digest_walkthrough` — topic Gmail list filtered to **`digest_source_candidate`**; terminal prompts; **`add_to_keep_list`** on **[Enter]**; **`--body`** flag enables parallel plain-text body prefetch + preview (ThreadPoolExecutor, same pattern as `unsubscribe`); **`--all`** in CLI walks every topic in sorted filename order |
| `pipeline.py` | Orchestrates query → list → keep-list filter → extract (LLM) or empty extraction when not **`digest_source_candidate`** → cache → trending → optional synthesis + HTML + `maybe_email_digest`; each **`messages[]`** item includes **`digest_source_candidate`** |
| `gmail_query.py` | Builds Gmail `q` strings from topic YAML (`window_days`, senders, folders, `since`) |
| `config.py` | `TopicConfig` + `load_topic_config` from `topics/<stem>.yaml` |
| `llm.py` | `complete` (litellm for `fast`/`smart`/`cheap`; direct `mlx_lm` for `local`/`local_smart`), aliases, `resolve_model_alias`, `MLX_MODEL_VARIANTS` (mirrors `local-chat`), LLM call logging into SQLite |
| `cache.py` | SQLite: `extractions`, `embeddings`, `llm_calls`; `cost_report_payload` / rollups for `digest cost --json` |
| `embed.py` | sentence-transformers embeddings keyed by claim hash |
| `cluster.py` | HDBSCAN-based trending groups |
| `synthesis.py` | Persona synthesis JSON via LLM |
| `render.py` | Jinja2 HTML digest |
| `spark_link.py` | `readdle-spark://` deeplinks from RFC822 ids |
| `digest_mail.py` | Optional Gmail API send for `also_email_to` |
| `paths.py` | `repo_root()`, default cache path under `<repo>/cache/` |

## Unsubscribe CLI only (digest entry is `email_digest.cli`)

| File | Notes |
|---|---|
| `src/unsubscribe/cli.py` | Owns the **`unsubscribe`** console script and walkthrough commands; use `python -m email_digest unsubscribe …` for passthrough. |

## Patterns to borrow from sibling projects

| Pattern | Source project | File |
|---|---|---|
| `_load_yaml()` — YAML → dict | swim | `src/swim/config/paths.py:76-83` |
| `repo_root()` — pyproject.toml walk-up | swim | `src/swim/common.py:10-37` |
| `load_prompt()` — Markdown section extraction | local-chat | `src/prompts.py:14-42` |
| Protocol+Facade pattern | billing-glugglejug | `src/googleads_invoice/gmail_facade.py` |
| DI-based orchestrator | billing-glugglejug | `src/googleads_invoice/run_month.py` |
| Secret-from-file-or-env | billing-glugglejug | `src/googleads_invoice/cli.py:66-84` |
| Qwen / LM Studio on-disk model dirs (`~/.lmstudio/models/...`) | local-chat | `src/llm.py` (`MODEL_VARIANTS` — defaults: **`local`** → `"2b"` (Qwen3.5-2B), **`local_smart`** → `"4b"` (Qwen3.5-4B)) |

## Performance

After `users.messages.list`, avoid long sequential `users.messages.get` chains — see `docs/LESSONS_LEARNED.md` (thread pool, thread-local clients, `max_workers == 1` for tests).
