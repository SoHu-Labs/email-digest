# email-digest

Run `mamba run -n email-digest python -m email_digest digest <command>` or just ask me directly. I'll run the right commands for you — dry-runs, sources, full runs, cost reports, whatever you need.

Available topics: `ai`, `health` (defined in `topics/*.yaml` with sender lists, keywords, and model aliases).

## Commands

| Task | TUI slash command |
|------|-------------------|
| Dry-run a topic | `/digest run <topic> --dry-run` |
| Dry-run all topics | `/digest run --all --dry-run` |
| Review digest sources (interactive) | `/digest sources <topic> [--new] [--body]` |
| Full run (extract + HTML) | `/digest run <topic>` |
| Cost report | `/digest cost --json` |
| Validate topics | `/digest topics --strict` |
| Unsubscribe check (4-week window) | `/unsubscribe --days 28` |
| Regenerate OAuth token | `/unsubscribe reauth` |

## Browser environment

### Invariant: chromedriver version must match Brave's Chromium version, not system Chrome

The unsubscribe flow attaches to Brave via `--remote-debugging-port=9222`. Chromedriver in PATH must match **Brave's embedded Chromium major version**, not whatever `Google Chrome for Testing` Selenium auto-caches.

**Check both versions:**
```bash
# Brave's Chromium version (the "148" in "148.1.90.124")
"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --version
# chromedriver version (must share the same major)
chromedriver --version
```

**When Brave auto-updates** and the Chromium major bumps:
1. Brew installs the new Brave cask; chromedriver does NOT auto-update with it
2. Download the matching chromedriver from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) and replace `/opt/homebrew/bin/chromedriver`
3. Clear the Selenium Chrome cache: `rm -rf ~/.cache/selenium/chrome/mac-arm64/`
4. `xattr -d com.apple.quarantine /opt/homebrew/bin/chromedriver`
5. Start Brave with `--remote-debugging-port=9222` before running unsubscribe

**Where the coupling lives:**
- `agentkit/src/agentkit/browser/_browser.py` — `build_chrome_options_for_remote_debugging` sets `binary_location` to Brave's path so Selenium Manager checks compatibility against Brave, not auto-detected Chrome for Testing
- `/opt/homebrew/bin/chromedriver` — the driver binary itself (brew cask is deprecated; use manual download)
