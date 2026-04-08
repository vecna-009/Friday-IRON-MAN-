"""
System tools — time, environment info, shell commands, etc.
"""

import base64
import ctypes
import datetime
import io
import os
import platform
import re
import subprocess
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from dotenv import load_dotenv

load_dotenv()


def _openrouter_headers() -> dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
    title = os.getenv("OPENROUTER_X_TITLE", "Friday Tony Stark Demo").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


def _active_window_title() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""

    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def _pc_automation_enabled() -> bool:
    return os.getenv("FRIDAY_ENABLE_PC_AUTOMATION", "0").lower() in {"1", "true", "yes", "on"}


def _shell_enabled() -> bool:
    return os.getenv("FRIDAY_ENABLE_SHELL", "0").lower() in {"1", "true", "yes", "on"}


def _autopilot_enabled() -> bool:
    return os.getenv("FRIDAY_ENABLE_AUTOPILOT", "0").lower() in {"1", "true", "yes", "on"}


def _deep_browser_enabled() -> bool:
    return os.getenv("FRIDAY_ENABLE_DEEP_BROWSER_CONTROL", "0").lower() in {"1", "true", "yes", "on"}


def _allowed_roots() -> list[Path]:
    configured = os.getenv("FRIDAY_ALLOWED_ROOTS", "")
    if not configured.strip():
        return [Path.cwd().resolve()]

    roots: list[Path] = []
    for part in configured.split(";"):
        item = part.strip()
        if not item:
            continue
        try:
            roots.append(Path(item).resolve())
        except Exception:
            continue
    return roots or [Path.cwd().resolve()]


def _ensure_path_allowed(raw_path: str) -> Path:
    target = Path(raw_path).expanduser().resolve()
    for root in _allowed_roots():
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    raise PermissionError(
        f"Path not allowed: {target}. Allowed roots: {', '.join(str(r) for r in _allowed_roots())}"
    )


def _allowed_apps() -> set[str]:
    defaults = "notepad,explorer,chrome,msedge,code,cmd,powershell"
    configured = os.getenv("FRIDAY_ALLOWED_APPS", defaults)
    return {app.strip().lower() for app in configured.split(",") if app.strip()}


def _command_denied(command: str) -> str | None:
    lowered = command.lower()
    blocked_patterns = [
        r"\bformat-volume\b",
        r"\bremove-item\b.*-recurse",
        r"\bshutdown\b",
        r"\brestart-computer\b",
        r"\bstop-computer\b",
        r"\brmdir\b",
        r"\bdel\b\s+/s",
        r"\bcipher\b\s+/w",
        r"\breg\b\s+delete",
    ]
    for pattern in blocked_patterns:
        if re.search(pattern, lowered):
            return pattern
    return None


def _load_pyautogui():
    try:
        import pyautogui  # type: ignore

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.03
        return pyautogui, None
    except Exception as exc:
        return None, str(exc)


async def _openrouter_chat(messages: list[dict], model: str, max_tokens: int = 220) -> str:
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, headers=_openrouter_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()

    message = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts = [item.get("text", "") for item in message if isinstance(item, dict)]
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def register(mcp):

    @mcp.tool()
    def get_current_time() -> str:
        """Return the current date and time in ISO 8601 format."""
        return datetime.datetime.now().isoformat()

    @mcp.tool()
    def get_system_info() -> dict:
        """Return basic information about the host system."""
        return {
            "os": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        }

    @mcp.tool()
    def get_active_window() -> dict:
        """Return the current active/foreground window title."""
        return {"active_window_title": _active_window_title()}

    @mcp.tool()
    def list_files(path: str, max_entries: int = 200) -> dict:
        """List files and folders in a directory (guarded by allowed roots)."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        try:
            target = _ensure_path_allowed(path)
            if not target.exists() or not target.is_dir():
                return {"error": f"Directory not found: {target}"}

            entries = []
            for i, child in enumerate(sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))):
                if i >= max(1, min(max_entries, 1000)):
                    break
                info = {
                    "name": child.name,
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                }
                if child.is_file():
                    try:
                        info["size"] = child.stat().st_size
                    except Exception:
                        pass
                entries.append(info)

            return {"path": str(target), "count": len(entries), "entries": entries}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def search_files(root_path: str, pattern: str = "*", limit: int = 100) -> dict:
        """Search for files under an allowed root using glob pattern."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        try:
            root = _ensure_path_allowed(root_path)
            if not root.exists() or not root.is_dir():
                return {"error": f"Directory not found: {root}"}

            matches: list[str] = []
            bounded = max(1, min(limit, 1000))
            for p in root.rglob(pattern):
                if len(matches) >= bounded:
                    break
                try:
                    _ensure_path_allowed(str(p))
                except PermissionError:
                    continue
                matches.append(str(p))

            return {"root": str(root), "pattern": pattern, "count": len(matches), "matches": matches}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def read_text_file(path: str, max_chars: int = 6000) -> dict:
        """Read text content from a file within allowed roots."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        try:
            target = _ensure_path_allowed(path)
            if not target.exists() or not target.is_file():
                return {"error": f"File not found: {target}"}

            text = target.read_text(encoding="utf-8", errors="ignore")
            clipped = text[: max(200, min(max_chars, 50000))]
            return {
                "path": str(target),
                "chars_returned": len(clipped),
                "truncated": len(clipped) < len(text),
                "content": clipped,
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def write_text_file(path: str, content: str, append: bool = False) -> dict:
        """Write text to a file within allowed roots."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        try:
            target = _ensure_path_allowed(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with target.open(mode, encoding="utf-8", errors="ignore") as f:
                f.write(content)
            return {"ok": True, "path": str(target), "append": append, "written_chars": len(content)}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def launch_app(command: str) -> dict:
        """Launch an allowed desktop application command (guarded allowlist)."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        cmd = command.strip()
        if not cmd:
            return {"error": "Empty command"}

        first_token = cmd.split()[0].lower().replace(".exe", "")
        if first_token not in _allowed_apps():
            return {
                "error": (
                    f"App not allowed: {first_token}. "
                    f"Allowed apps: {', '.join(sorted(_allowed_apps()))}"
                )
            }

        try:
            subprocess.Popen(cmd, shell=True)
            return {"ok": True, "launched": cmd}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def open_url(url: str) -> dict:
        """Open a URL in the system's default browser."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        target = url.strip()
        if not target:
            return {"error": "URL cannot be empty."}
        if not (target.startswith("http://") or target.startswith("https://")):
            target = "https://" + target

        try:
            ok = webbrowser.open(target)
            return {"ok": bool(ok), "opened_url": target}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def search_youtube(query: str) -> dict:
        """Open YouTube search results for a query in the browser."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        q = query.strip()
        if not q:
            return {"error": "Search query cannot be empty."}

        url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
        try:
            ok = webbrowser.open(url)
            return {"ok": bool(ok), "opened_url": url, "query": q}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def browser_scroll(direction: str = "down", amount: int = 700) -> dict:
        """Scroll active browser tab up or down by pixel-like amount."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}
        if not _deep_browser_enabled():
            return {"error": "Deep browser control disabled. Set FRIDAY_ENABLE_DEEP_BROWSER_CONTROL=1."}

        pyautogui, err = _load_pyautogui()
        if pyautogui is None:
            return {"error": f"pyautogui not available: {err}"}

        step = max(50, min(abs(int(amount)), 3000))
        signed = -step if direction.strip().lower() == "down" else step
        try:
            pyautogui.scroll(signed)
            return {"ok": True, "direction": direction, "amount": step}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def browser_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
        """Click at screen coordinates to control browser UI elements."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}
        if not _deep_browser_enabled():
            return {"error": "Deep browser control disabled. Set FRIDAY_ENABLE_DEEP_BROWSER_CONTROL=1."}

        pyautogui, err = _load_pyautogui()
        if pyautogui is None:
            return {"error": f"pyautogui not available: {err}"}

        btn = button.strip().lower()
        if btn not in {"left", "right", "middle"}:
            return {"error": "button must be one of: left, right, middle"}

        try:
            pyautogui.click(int(x), int(y), clicks=max(1, min(int(clicks), 5)), button=btn)
            return {"ok": True, "x": int(x), "y": int(y), "button": btn, "clicks": int(clicks)}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def browser_hotkey(keys: str) -> dict:
        """Send hotkey combo to active browser, e.g. 'ctrl+l' or 'ctrl+tab'."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}
        if not _deep_browser_enabled():
            return {"error": "Deep browser control disabled. Set FRIDAY_ENABLE_DEEP_BROWSER_CONTROL=1."}

        pyautogui, err = _load_pyautogui()
        if pyautogui is None:
            return {"error": f"pyautogui not available: {err}"}

        parts = [p.strip().lower() for p in keys.split("+") if p.strip()]
        if not parts:
            return {"error": "keys cannot be empty"}

        allowed = {
            "ctrl", "shift", "alt", "win", "tab", "enter", "space", "k", "j", "l", "f", "m", "t", "n", "p", "left", "right", "up", "down", "home", "end", "pgup", "pgdn", "esc", "backspace", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
        }
        unknown = [k for k in parts if k not in allowed]
        if unknown:
            return {"error": f"Unsupported key(s): {unknown}"}

        try:
            pyautogui.hotkey(*parts)
            return {"ok": True, "sent": parts}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def youtube_control(action: str) -> dict:
        """Control active YouTube tab: play_pause, next, previous, seek_forward, seek_back, fullscreen, mute, theater."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}
        if not _deep_browser_enabled():
            return {"error": "Deep browser control disabled. Set FRIDAY_ENABLE_DEEP_BROWSER_CONTROL=1."}

        pyautogui, err = _load_pyautogui()
        if pyautogui is None:
            return {"error": f"pyautogui not available: {err}"}

        action_key = action.strip().lower()
        mapping: dict[str, tuple[str, ...]] = {
            "play_pause": ("k",),
            "next": ("shift", "n"),
            "previous": ("shift", "p"),
            "seek_forward": ("l",),
            "seek_back": ("j",),
            "fullscreen": ("f",),
            "mute": ("m",),
            "theater": ("t",),
        }

        combo = mapping.get(action_key)
        if not combo:
            return {"error": f"Unsupported action: {action_key}", "supported": sorted(mapping.keys())}

        try:
            if len(combo) == 1:
                pyautogui.press(combo[0])
            else:
                pyautogui.hotkey(*combo)
            return {"ok": True, "action": action_key}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def run_powershell(command: str, timeout_seconds: int = 12) -> dict:
        """Run a guarded PowerShell command. Disabled by default."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}
        if not _shell_enabled():
            return {"error": "Shell execution disabled. Set FRIDAY_ENABLE_SHELL=1."}

        denied = _command_denied(command)
        if denied:
            return {"error": f"Command blocked by safety rule: {denied}"}

        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout_seconds, 120)),
            )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout[:12000],
                "stderr": completed.stderr[:8000],
            }
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def save_instruction(note: str) -> dict:
        """Persist a user instruction for FRIDAY's local task memory."""
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}

        try:
            memory_dir = _ensure_path_allowed(str(Path.cwd() / ".friday"))
            memory_dir.mkdir(parents=True, exist_ok=True)
            file_path = memory_dir / "instructions.txt"
            timestamp = datetime.datetime.now().isoformat(timespec="seconds")
            with file_path.open("a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {note}\n")
            return {"ok": True, "saved_to": str(file_path)}
        except Exception as exc:
            return {"error": str(exc)}

    @mcp.tool()
    async def analyze_current_screen(question: str = "What is on my screen right now?") -> str:
        """
        Capture the current screen and analyze it with OpenRouter vision.
        Use this when the user asks about visible content on their display.
        """
        try:
            from PIL import ImageGrab  # type: ignore
        except Exception as exc:
            return f"Screen analysis is unavailable because Pillow ImageGrab could not load: {exc}"

        try:
            img = ImageGrab.grab(all_screens=True)
            max_width = 1440
            if img.width > max_width:
                ratio = max_width / float(img.width)
                img = img.resize((max_width, int(img.height * ratio)))

            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=70, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            model = os.getenv("OPENROUTER_VISION_MODEL", os.getenv("OPENROUTER_LLM_MODEL", "openai/gpt-4o-mini"))
            active_window = _active_window_title()
            context_note = f"Active window title: {active_window}" if active_window else "Active window title: unknown"

            messages = [
                {
                    "role": "system",
                    "content": "You are a precise screen analyst. Be concise and actionable.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{context_note}\nQuestion: {question}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                },
            ]

            result = await _openrouter_chat(messages, model=model, max_tokens=260)
            return result or "I captured the screen, but could not extract a useful analysis."
        except Exception as exc:
            return f"Screen analysis failed: {exc}"

    @mcp.tool()
    async def analyze_browser_url(url: str, question: str = "Summarize this page and key points.") -> str:
        """
        Fetch a web page and answer a user question about it with OpenRouter.
        Use this when the user asks to inspect or analyze browser/page content.
        """
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
                resp = await client.get(url, headers={"User-Agent": "Friday-AI/1.0"})
                resp.raise_for_status()
                html = resp.text

            text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
            text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            text = text[:14000]

            model = os.getenv("OPENROUTER_LLM_MODEL", "openai/gpt-4o-mini")
            messages = [
                {
                    "role": "system",
                    "content": "Answer accurately from the provided webpage text. If missing, say so.",
                },
                {
                    "role": "user",
                    "content": (
                        f"URL: {url}\n"
                        f"Question: {question}\n"
                        f"Page text excerpt:\n{text}"
                    ),
                },
            ]

            result = await _openrouter_chat(messages, model=model, max_tokens=280)
            return result or "I fetched the page, but could not produce a reliable analysis."
        except Exception as exc:
            return f"Browser URL analysis failed: {exc}"

    @mcp.tool()
    def run_instruction_plan(plan: str, stop_on_error: bool = True) -> dict:
        """
        Execute a multi-step plan line-by-line in autopilot mode.

        Supported steps (one per line):
        - open_url <url>
        - search_youtube <query>
        - launch_app <command>
        - run_powershell <command>
        - save_instruction <text>
        - wait <seconds>
        """
        if not _pc_automation_enabled():
            return {"error": "PC automation is disabled. Set FRIDAY_ENABLE_PC_AUTOMATION=1."}
        if not _autopilot_enabled():
            return {"error": "Autopilot is disabled. Set FRIDAY_ENABLE_AUTOPILOT=1."}

        lines = [ln.strip() for ln in plan.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            return {"error": "No executable steps found in plan."}

        results: list[dict] = []
        ok_count = 0

        for idx, line in enumerate(lines, start=1):
            action, arg = (line.split(" ", 1) + [""])[:2]
            action = action.strip().lower()
            arg = arg.strip()
            step_result: dict = {"step": idx, "input": line, "action": action}

            try:
                if action == "open_url":
                    target = arg if arg.startswith(("http://", "https://")) else f"https://{arg}"
                    opened = webbrowser.open(target)
                    step_result.update({"status": "ok", "opened_url": target, "opened": bool(opened)})

                elif action == "search_youtube":
                    if not arg:
                        raise ValueError("search_youtube requires a query")
                    url = f"https://www.youtube.com/results?search_query={quote_plus(arg)}"
                    opened = webbrowser.open(url)
                    step_result.update({"status": "ok", "opened_url": url, "opened": bool(opened)})

                elif action == "launch_app":
                    if not arg:
                        raise ValueError("launch_app requires a command")
                    first_token = arg.split()[0].lower().replace(".exe", "")
                    if first_token not in _allowed_apps():
                        raise PermissionError(
                            f"App not allowed: {first_token}. Allowed: {', '.join(sorted(_allowed_apps()))}"
                        )
                    subprocess.Popen(arg, shell=True)
                    step_result.update({"status": "ok", "launched": arg})

                elif action == "run_powershell":
                    if not _shell_enabled():
                        raise PermissionError("Shell execution disabled. Set FRIDAY_ENABLE_SHELL=1.")
                    if not arg:
                        raise ValueError("run_powershell requires a command")
                    denied = _command_denied(arg)
                    if denied:
                        raise PermissionError(f"Command blocked by safety rule: {denied}")
                    completed = subprocess.run(
                        ["powershell", "-NoProfile", "-Command", arg],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    step_result.update(
                        {
                            "status": "ok" if completed.returncode == 0 else "error",
                            "returncode": completed.returncode,
                            "stdout": completed.stdout[:2000],
                            "stderr": completed.stderr[:1000],
                        }
                    )

                elif action == "save_instruction":
                    if not arg:
                        raise ValueError("save_instruction requires text")
                    memory_dir = _ensure_path_allowed(str(Path.cwd() / ".friday"))
                    memory_dir.mkdir(parents=True, exist_ok=True)
                    file_path = memory_dir / "instructions.txt"
                    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
                    with file_path.open("a", encoding="utf-8") as f:
                        f.write(f"[{timestamp}] {arg}\n")
                    step_result.update({"status": "ok", "saved_to": str(file_path)})

                elif action == "wait":
                    seconds = max(0.0, min(float(arg or "1"), 20.0))
                    time.sleep(seconds)
                    step_result.update({"status": "ok", "waited_seconds": seconds})

                else:
                    raise ValueError(f"Unsupported action: {action}")

                if step_result.get("status") == "ok":
                    ok_count += 1

            except Exception as exc:
                step_result.update({"status": "error", "error": str(exc)})
                if stop_on_error:
                    results.append(step_result)
                    return {
                        "ok": False,
                        "completed_steps": ok_count,
                        "total_steps": len(lines),
                        "results": results,
                    }

            results.append(step_result)

        return {
            "ok": ok_count == len(lines),
            "completed_steps": ok_count,
            "total_steps": len(lines),
            "results": results,
        }
