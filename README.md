# coding-agent

A local terminal agent that reads and edits files, runs allowlisted commands, and calls OpenAI-compatible LLMs. When a provider hits a rate limit or quota error, the router cools that model down and tries the next model, then the next provider.

Free tiers reset on their own schedules and may log prompts. Do not send secrets.

## Setup

Python 3.11 or newer.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

macOS and Linux:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Put keys in `.env`. `providers.yaml` stores the variable name (`GROQ_API_KEY`), never the key itself. Providers with a missing required key are skipped. LLM7 works without a key. Kilo does not use a key.

Check what is configured:

```bash
agent --providers
```

If `agent` is not on your PATH, use `python -m coding_agent`.

## Use

Options can come before or after the task.

```bash
agent "Add tests for utils.py" --cwd ./my-project
agent --cwd ./my-project --dry-run "Describe how main.py loads config"
agent "Refactor parser.py" --cwd ./my-project --yes
```

- `--cwd` is the only directory the agent may read or write. Paths outside it are rejected.
- `--dry-run` still reads and searches. `write_file` and `run_shell` are described and not executed.
- Writes and shell commands ask for confirmation unless you pass `--yes`.
- `--allow name` adds an executable to the shell allowlist (repeat the flag). The default allowlist is `python`, `py`, `pytest`, `git`, `rg`, and `pip`.
- `--max-steps` caps the tool loop (default 25).

`agent` with no task prints help.

## Failover

`providers.yaml` lists free-tier model ids only.

1. Groq: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`
2. OpenRouter: `openrouter/free` or a model id ending in `:free`
3. LLM7
4. Kilo
5. Mistral: `codestral-latest`
6. Gemini: `gemini-3.8-flash`

Within a provider, models are tried in list order.

| Failure | What happens |
| --- | --- |
| 429, or body mentions rate limit, quota, tokens, TPM, RPD | That model cools down, then the next model is tried |
| 402, or `insufficient_quota` | The whole provider cools down |
| 401 / 403 auth failure | That provider is skipped until the process exits |
| 404 unknown model | That model is skipped for the session |
| 5xx or network error | One short retry, then the next model |

Cooldowns are stored in `~/.coding-agent/cooldowns.json` (`%USERPROFILE%\.coding-agent\cooldowns.json` on Windows), so a restart does not immediately retry a dead key. Override the file with `--cooldown-file`.

Not every model supports native tool calls. If a reply has no tool calls but contains a fenced JSON object `{"action","arguments"}` naming a known tool, that object is executed as one tool call.

If the OpenAI package cannot load its native JSON library, requests fall back to the Python standard library. The request shape stays the same.

## Tools

- `read_file`, `write_file`, `list_dir`, `search_code`
- `run_shell` takes an argument vector, uses `shell=False`, times out after 60 seconds, and caps output. The first argument must be on the allowlist.

`search_code` uses `rg` when it is on `PATH`, otherwise a Python walk.

## Tests

```bash
pytest
```

Tests do not call live APIs.
