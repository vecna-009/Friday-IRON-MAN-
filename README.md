# F.R.I.D.A.Y. — Tony Stark Demo

> *"Fully Responsive Intelligent Digital Assistant for You"*

A Tony Stark-inspired AI assistant split into two cooperating pieces:

| Component | What it is |
|-----------|-----------|
| **MCP Server** (`uv run friday`) | A [FastMCP](https://github.com/jlowin/fastmcp) server that exposes tools (news, web search, system info, …) over SSE. Think of it as the Stark Industries backend — it does the actual work. |
| **Voice Agent** (`uv run friday_voice`) | A [LiveKit Agents](https://github.com/livekit/agents) voice pipeline that listens to your microphone, uses OpenRouter as the main LLM endpoint for fast and precise replies, and speaks back with Sarvam TTS via the HTTP synth path while pulling tools from the MCP server in real time. |


---

## How it works

```
Microphone ──► STT (Sarvam Saaras v3)
                    │
                    ▼
            LLM (OpenRouter)     ◄──────► MCP Server (FastMCP / SSE)
                    │                              ├─ get_world_news
                    ▼                              ├─ open_world_monitor
       TTS (Sarvam)                          ├─ search_web
                    │                              └─ …more tools
                    ▼
             Speaker / LiveKit room
```

The voice agent connects to the MCP server via SSE at `http://127.0.0.1:8000/sse` (auto-resolved to the Windows host IP when running inside WSL).

---

## Project structure

```
friday-tony-stark-demo/
├── server.py           # uv run friday  → starts the MCP server (SSE on :8000)
├── agent_friday.py     # uv run friday_voice → starts the LiveKit voice agent
├── pyproject.toml
├── .env.example        # copy → .env and fill in your keys
│
└── friday/             # MCP server package
    ├── config.py       # env-var loading & app-wide settings
    ├── tools/          # MCP tools (callable by the LLM)
    │   ├── web.py      # search_web, fetch_url, get_world_news, open_world_monitor
    │   ├── system.py   # get_current_time, get_system_info
    │   └── utils.py    # format_json, word_count
    ├── prompts/        # MCP prompt templates (summarize, explain_code, …)
    └── resources/      # MCP resources exposed to clients (friday://info)
```

---

## Quick start

### 1. Prerequisites

- Python ≥ 3.11
- [`uv`](https://github.com/astral-sh/uv) — `pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`
- A [LiveKit Cloud](https://cloud.livekit.io) project (free tier works)

### 2. Clone & install

```bash
git clone https://github.com/SAGAR-TAMANG/friday-tony-stark-demo.git
cd friday-tony-stark-demo
uv sync          # creates .venv and installs all dependencies
```

### 3. Set up environment

```bash
uv run friday_init_env
# Open .env and fill in your API keys (see the section below)
```

If you prefer, you can still copy `.env.example` to `.env` manually.

### 4. Run — two terminals

**Terminal 1 — MCP server** (must start first)

```bash
uv run friday
```

Starts the FastMCP server on `http://127.0.0.1:8000/sse`. The voice agent connects here to fetch its tools.

**Terminal 2 — Voice agent**

```bash
uv run friday_voice
```

Starts the LiveKit voice agent in **dev mode** — it joins a LiveKit room and begins listening. Open the [LiveKit Agents Playground](https://agents-playground.livekit.io) and connect to your room to talk to FRIDAY.

---

## `uv run friday` vs `uv run friday_voice`

| Command | Entry point | What it does |
|---------|------------|--------------|
| `uv run friday` | `server.py → main()` | Launches the **FastMCP server** over SSE transport on port 8000. This is the "brain backend" — it registers all tools, prompts, and resources that the LLM can call. |
| `uv run friday_voice` | `agent_friday.py → dev()` | Launches the **LiveKit voice agent**. It builds the STT / LLM / TTS pipeline, connects to your LiveKit room, and wires up the MCP server as a tool source. The `dev()` wrapper auto-injects the `dev` CLI flag so you don't have to type it manually. |

> Both processes must run **simultaneously**. The voice agent calls the MCP server in real time whenever it needs a tool (e.g. fetching news).

---

## Environment variables

Copy `.env.example` → `.env` and fill in the values below.

| Variable | Required | Where to get it |
|----------|----------|----------------|
| `LIVEKIT_URL` | ✅ | [LiveKit Cloud dashboard](https://cloud.livekit.io) → your project URL |
| `LIVEKIT_API_KEY` | ✅ | LiveKit Cloud → API Keys |
| `LIVEKIT_API_SECRET` | ✅ | LiveKit Cloud → API Keys |
| `OPENROUTER_API_KEY` | ✅ (main LLM) | [OpenRouter Keys](https://openrouter.ai/keys) |
| `OPENROUTER_LLM_MODEL` | optional | Default: `openai/gpt-4o-mini` (fast and reliable). |
| `OPENROUTER_VISION_MODEL` | optional | Model used by screen analysis tool. Default: same as `OPENROUTER_LLM_MODEL`. |
| `OPENROUTER_HTTP_REFERER` | optional | Recommended for OpenRouter app attribution/routing. |
| `OPENROUTER_X_TITLE` | optional | Friendly app name sent in request headers. |
| `FRIDAY_ENABLE_PC_AUTOMATION` | optional | `1` enables local file/app automation tools. Default `0`. |
| `FRIDAY_ENABLE_SHELL` | optional | `1` enables guarded PowerShell tool. Default `0`. |
| `FRIDAY_ENABLE_AUTOPILOT` | optional | `1` enables multi-step instruction runner. Default `0`. |
| `FRIDAY_ALLOWED_ROOTS` | optional | Semicolon-separated folder allowlist for file tools. |
| `FRIDAY_ALLOWED_APPS` | optional | Comma-separated allowlist for app launches. |
| `SARVAM_API_KEY` | ✅ (default STT) | [dashboard.sarvam.ai](https://dashboard.sarvam.ai) |
| `SUPABASE_URL` | optional | [supabase.com](https://supabase.com) — for the ticketing tool |
| `SUPABASE_API_KEY` | optional | Supabase project → API settings |

### Local desktop tools added

When `FRIDAY_ENABLE_PC_AUTOMATION=1`, FRIDAY can use:

- `list_files(path)`
- `search_files(root_path, pattern, limit)`
- `read_text_file(path)`
- `write_text_file(path, content, append)`
- `launch_app(command)` (allowlisted apps only)
- `open_url(url)`
- `search_youtube(query)`
- `save_instruction(note)`
- `run_instruction_plan(plan, stop_on_error)`

If `FRIDAY_ENABLE_SHELL=1`, FRIDAY can also use:

- `run_powershell(command, timeout_seconds)` with destructive command blocking.

Autopilot plan format example:

```text
open_url youtube.com
search_youtube believer imagine dragons
launch_app notepad
run_powershell Get-Process | Select-Object -First 5
```

---

## Adding a new tool

1. Create or open a file in `friday/tools/`
2. Define a `register(mcp)` function and decorate tools with `@mcp.tool()`
3. Import and call `register(mcp)` inside `friday/tools/__init__.py`

The MCP server will pick it up on next start.

---

## Tech stack

- **[FastMCP](https://github.com/jlowin/fastmcp)** — MCP server framework
- **[LiveKit Agents](https://github.com/livekit/agents)** — real-time voice pipeline
- **Sarvam Saaras v3** — STT (Indian-English optimised)
- **OpenRouter (OpenAI-compatible)** — primary reasoning endpoint with flexible model routing
- **Sarvam TTS** — TTS via the HTTP synth path, tuned for the female Friday-style voice
- **[uv](https://github.com/astral-sh/uv)** — fast Python package manager

---

## License

MIT

---

## Push to GitHub (Windows)

If Git is not installed yet, install **Git for Windows** first.

Then from the project root run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_github_remote.ps1 -RemoteUrl "https://github.com/<your-username>/<your-repo>.git" -Branch "main"
```

This script will:

- initialize git (if needed)
- set branch to `main`
- add/update `origin`
- create an initial commit (if there are uncommitted files)
- push to GitHub

## Auto-commit and auto-push on local changes

### Option A: Run now in a terminal (quick test)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\auto_git_sync.ps1 -RepoPath "." -Branch "main" -IntervalSeconds 30
```

The script checks for changes every 30 seconds, commits them, and pushes to GitHub.

### Option B: Start automatically when you log in (recommended)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_auto_git_task.ps1 -RepoPath "." -Branch "main" -IntervalSeconds 30
```

This registers a Windows Scheduled Task named `FridayAutoGitSync`.

To remove it later:

```powershell
Unregister-ScheduledTask -TaskName "FridayAutoGitSync" -Confirm:$false
```
