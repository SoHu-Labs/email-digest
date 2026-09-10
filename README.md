# email-digest

Two email tools sharing one Gmail API backend:

- **unsubscribe** — automate unsubscribing from newsletters
- **digest** — topic-oriented email summaries with LLM synthesis and trending detection

## Setup

```bash
mamba env create -f environment.yml
mamba activate email-digest
pip install -e ".[dev]"
```

## Credentials

| What | Env var | Notes |
|---|---|---|
| Google OAuth token (Gmail API) | `GOOGLE_OAUTH_TOKEN` | Path to authorized-user JSON. Must grant **gmail.readonly** (unsubscribe + digest read) and **gmail.send** if you use topic `output.also_email_to` (digest emails yourself). Re-consent if you add `gmail.send` to an older token. |
| DeepSeek API key | `DEEPSEEK_API_KEY` | For digest LLM extraction/synthesis (`fast` / `smart`). If unset, the CLI also reads `deepseek.key` from `~/.local/share/opencode/auth.json`. Explicit env wins. |
| Local LLM (direct MLX via `mlx_lm`) | — | No env vars needed. Models loaded from `~/.lmstudio/models/`. `local` → Qwen3.5-2B, `local_smart` → Qwen3.5-4B. See **`docs/LM_STUDIO_DIGEST.md`** and `src/email_digest/llm.py` (`MLX_MODEL_VARIANTS`). |
| Cheap / MiniMax API key (for OpenCode Go) | `CHEAP_API_KEY` | Also auto-read from `~/.local/share/opencode/auth.json` (`opencode-go` block). Set up with `opencode /connect` for OpenCode Go, or export the key from [opencode.ai/auth](https://opencode.ai/auth). |
| Cheap model id override | `CHEAP_MODEL` | Default `openai/minimax-m2.5` (Go plan); set to `openai/minimax-m2.7` for improved quality. |
| Cheap API base URL | `CHEAP_API_BASE` | Default `https://opencode.ai/zen/go/v1` (Go plan). |
| Digest SQLite (optional override) | `DIGEST_CACHE_DB` | Defaults to `<repo>/cache/digest.sqlite` (gitignored) |

**Spark deep-links:** the digest uses `readdle-spark://openmessage?messageId=…` per **`src/email_digest/spark_link.py`**. Readdle may change URL schemes; use **`python -m email_digest digest spark-check`** and **`docs/SPARK_DEVICE_CHECK.md`** for a paste test on your device, then update **`spark_link.py`** if the contract differs.

## CLI

```
# Unsubscribe
python -m email_digest unsubscribe [--days DAYS]
python -m email_digest unsubscribe reauth

# Digest
python -m email_digest digest topics [--json]
python -m email_digest digest run <topic> [--dry-run]
python -m email_digest digest run --all [--dry-run]
python -m email_digest digest sources <topic> [--new] [--body]
python -m email_digest digest sources --all [--new] [--body]
python -m email_digest digest cost [--days N] [--json]
```

**`digest topics`** lists available topics from `topics/*.yaml`.

**`digest run`** collects, extracts, and synthesizes a topic. `--dry-run` skips synthesis and HTML (JSON only).

**`digest sources`** interactively reviews digest-source candidates for a topic.
Per candidate: **[Enter]** keep as source, **[u]** mark for unsubscribe, **[s]** skip,
**[q]** quit. `--new` shows only sources not yet on the keep list. `--body`
prefetches plain-text bodies for a preview. At the end, selected unsubscribe
items are sent through automated one-click + browser unsubscribe.

**`digest cost`** reports LLM API usage from the SQLite cache.

**`unsubscribe`** scans the last `--days` days
of Gmail (default 3, up to the 500 newest messages), shortlists newsletters with
unsubscribe links, and walks through them interactively. Fetches are paced to stay
within Gmail's per-user quota, so a 4-week scan takes a couple of minutes.
**[Enter]** keeps a sender, **[u]** marks it for unsubscribe, **[q]** stops the
walkthrough. `unsubscribe reauth` regenerates the OAuth token via the browser.

## Docs

- `docs/AGENT_PLAN_CONTRACT.md` — **how to write plan slices** (permissions, caveats, follow-ups, acceptance) so implementers do not guess
- `docs/PROJECT_BRIEF_EMAIL_SUMMARIES.md` — digest engine project brief
- `docs/IMPLEMENTATION_PLAN_EMAIL_SUMMARIES.md` — implementation plan
- `docs/LM_STUDIO_DIGEST.md` — LM Studio env vars and model id alignment for digest aliases
- `docs/INVENTORY.md` — code inventory
- `docs/SPARK_DEVICE_CHECK.md` — paste **`digest spark-check`** URL into Spark (manual R5 / F2)
