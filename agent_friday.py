"""
FRIDAY – Voice Agent (MCP-powered)
===================================
Iron Man-style voice assistant that controls RGB lighting, runs diagnostics,
scans the network, and triggers dramatic boot sequences via an MCP server
running on the Windows host.

MCP Server URL is auto-resolved from WSL → Windows host IP.

Run:
  uv run agent_friday.py dev      – LiveKit Cloud mode
  uv run agent_friday.py console  – text-only console mode
"""

import os
import logging
import subprocess
import re
import time
import httpx

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli, llm as lk_llm, tts as lk_tts, utils as lk_utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.llm import mcp

# Plugins
from livekit.plugins import openai as lk_openai, sarvam, silero

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

STT_PROVIDER       = "sarvam"
LLM_PROVIDER       = "openrouter"
TTS_PROVIDER       = "sarvam"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_LLM_MODEL = os.getenv("OPENROUTER_LLM_MODEL", "openai/gpt-4o-mini")
OPENROUTER_TEMPERATURE = os.getenv("OPENROUTER_TEMPERATURE", "0.2")
OPENROUTER_MAX_TOKENS  = os.getenv("OPENROUTER_MAX_TOKENS", "100")
OPENROUTER_TOP_P       = os.getenv("OPENROUTER_TOP_P", "0.85")
OPENROUTER_QUOTA_COOLDOWN_SECONDS = os.getenv("OPENROUTER_QUOTA_COOLDOWN_SECONDS", "60")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_X_TITLE = os.getenv("OPENROUTER_X_TITLE", "Friday Tony Stark Demo")
OPENROUTER_TIMEOUT_SECONDS = os.getenv("OPENROUTER_TIMEOUT_SECONDS", "4")
OPENROUTER_FALLBACK_ON_ANY_ERROR = os.getenv("OPENROUTER_FALLBACK_ON_ANY_ERROR", "1")
ENDPOINTING_DELAY_SECONDS = os.getenv("ENDPOINTING_DELAY_SECONDS", "0.025")

TTS_SPEED           = 1.2

SARVAM_TTS_LANGUAGE = "en-IN"
SARVAM_TTS_SPEAKER  = "shreya"
SARVAM_STT_LANGUAGE = os.getenv("SARVAM_STT_LANGUAGE", "en-IN")

# MCP server running on Windows host
MCP_SERVER_PORT = 8000

# ---------------------------------------------------------------------------
# System prompt – F.R.I.D.A.Y.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are F.R.I.D.A.Y., Tony Stark style: calm, sharp, warm, and concise.
Speak naturally and briefly in 1-3 sentences by default for speed.
Use a two-pass style: deliver an instant concise answer first, then expand only if the user asks "continue", "details", or "explain more".
Only switch to long, step-by-step explanations when the user explicitly asks for detail (for example: explain in detail, deep dive, or step by step).
In explanation mode, be proactive: define terms, explain why, and include practical examples when helpful.
Never mention technical tool names or internal mechanics.
Call available tools immediately when needed.
For world/news requests: fetch news first, deliver a short spoken brief, then open the world monitor.
If a tool fails, report it simply and offer to retry.
Stay in character and use terms like boss, on it, and standing by.
""".strip()
# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger("friday-agent")
logger.setLevel(logging.INFO)

REQUIRED_LIVEKIT_ENV = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
)


# ---------------------------------------------------------------------------
# Resolve Windows host IP from WSL
# ---------------------------------------------------------------------------

def _get_windows_host_ip() -> str:
    """Get the Windows host IP by looking at the default network route."""
    try:
        # 'ip route' is the most reliable way to find the 'default' gateway
        # which is always the Windows host in WSL.
        cmd = "ip route show default | awk '{print $3}'"
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=2
        )
        ip = result.stdout.strip()
        if ip:
            logger.info("Resolved Windows host IP via gateway: %s", ip)
            return ip
    except Exception as exc:
        logger.warning("Gateway resolution failed: %s. Trying fallback...", exc)

    # Fallback to your original resolv.conf logic if 'ip route' fails
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                if "nameserver" in line:
                    ip = line.split()[1]
                    logger.info("Resolved Windows host IP via nameserver: %s", ip)
                    return ip
    except Exception:
        pass

    return "127.0.0.1"

def _mcp_server_url() -> str:
    # host_ip = _get_windows_host_ip()
    # url = f"http://{host_ip}:{MCP_SERVER_PORT}/sse"
    # url = f"https://ongoing-colleague-samba-pioneer.trycloudflare.com/sse"
    url = f"http://127.0.0.1:{MCP_SERVER_PORT}/sse"
    logger.info("MCP Server URL: %s", url)
    return url


# ---------------------------------------------------------------------------
# Build provider instances
# ---------------------------------------------------------------------------

def _build_stt():
    logger.info("STT → Sarvam Saaras v3")
    return sarvam.STT(
        language=SARVAM_STT_LANGUAGE,
        model="saaras:v3",
        mode="transcribe",
        flush_signal=True,
        sample_rate=16000,
    )


def _build_llm():
    if LLM_PROVIDER == "openrouter":
        logger.info("LLM → OpenRouter (%s) with local fallback", OPENROUTER_LLM_MODEL)
        return OpenRouterWithFallbackLLM(
            primary=lk_openai.LLM(
                model=OPENROUTER_LLM_MODEL,
                api_key=OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                temperature=_safe_float(OPENROUTER_TEMPERATURE, 0.2),
                max_completion_tokens=_safe_int(OPENROUTER_MAX_TOKENS, 160),
                top_p=_safe_float(OPENROUTER_TOP_P, 0.85),
                timeout=httpx.Timeout(
                    connect=8.0,
                    read=max(1.0, _safe_float(OPENROUTER_TIMEOUT_SECONDS, 4.0)),
                    write=5.0,
                    pool=5.0,
                ),
                extra_headers=_openrouter_extra_headers(),
            ),
            fallback=OfflineFridayLLM(),
            cooldown_seconds=_safe_float(OPENROUTER_QUOTA_COOLDOWN_SECONDS, 60.0),
        )
    if LLM_PROVIDER == "local":
        logger.info("LLM → local Friday brain")
        return OfflineFridayLLM()
    raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")


def _safe_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _openrouter_extra_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if OPENROUTER_HTTP_REFERER.strip():
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER.strip()
    if OPENROUTER_X_TITLE.strip():
        headers["X-Title"] = OPENROUTER_X_TITLE.strip()
    return headers


def _is_provider_quota_or_rate_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    message = str(exc).lower()

    if status_code in {429, 402, 403}:
        return True

    if isinstance(body, dict):
        body_text = str(body).lower()
        if "resource_exhausted" in body_text or "quota" in body_text or "rate" in body_text:
            return True

    return (
        "resource_exhausted" in message
        or "insufficient_quota" in message
        or "quota exceeded" in message
        or "too many requests" in message
        or "rate limit" in message
    )


def _extract_retry_after_seconds(exc: Exception) -> float | None:
    message = str(exc)
    body = getattr(exc, "body", None)
    text = f"{message} {body}" if body is not None else message

    match = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", text, flags=re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None

    match = re.search(r'"retryDelay"\s*:\s*"([0-9]+)s"', text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None

    return None


class OpenRouterWithFallbackLLMStream(lk_llm.LLMStream):
    def __init__(
        self,
        *,
        llm: lk_llm.LLM,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool],
        conn_options,
        parent: "OpenRouterWithFallbackLLM",
    ) -> None:
        super().__init__(
            llm=llm,
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
        )
        self._parent = parent

    async def _forward_stream(self, stream: lk_llm.LLMStream) -> None:
        async for chunk in stream:
            self._event_ch.send_nowait(chunk)

    async def _run(self) -> None:
        if self._parent.is_in_cooldown():
            fallback_stream = self._parent.fallback.chat(
                chat_ctx=self._chat_ctx,
                tools=self._tools,
                conn_options=self._conn_options,
            )
            await self._forward_stream(fallback_stream)
            return

        try:
            primary_stream = self._parent.primary.chat(
                chat_ctx=self._chat_ctx,
                tools=self._tools,
                conn_options=self._conn_options,
            )
            await self._forward_stream(primary_stream)
            return
        except Exception as exc:
            fallback_any_error = os.getenv("OPENROUTER_FALLBACK_ON_ANY_ERROR", "1").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if not _is_provider_quota_or_rate_error(exc) and not fallback_any_error:
                raise

            retry_after = _extract_retry_after_seconds(exc)
            self._parent.enter_cooldown(retry_after)
            logger.warning(
                "OpenRouter unavailable due to quota/rate limits (%s). Using local fallback.",
                exc,
            )

        fallback_stream = self._parent.fallback.chat(
            chat_ctx=self._chat_ctx,
            tools=self._tools,
            conn_options=self._conn_options,
        )
        await self._forward_stream(fallback_stream)


class OpenRouterWithFallbackLLM(lk_llm.LLM):
    def __init__(self, *, primary: lk_llm.LLM, fallback: lk_llm.LLM, cooldown_seconds: float) -> None:
        super().__init__()
        self.primary = primary
        self.fallback = fallback
        self._cooldown_seconds = max(1.0, cooldown_seconds)
        self._cooldown_until = 0.0

    def is_in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def enter_cooldown(self, retry_after_seconds: float | None) -> None:
        duration = retry_after_seconds if retry_after_seconds and retry_after_seconds > 0 else self._cooldown_seconds
        self._cooldown_until = time.monotonic() + duration

    def chat(
        self,
        *,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool] | None = None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> lk_llm.LLMStream:
        return OpenRouterWithFallbackLLMStream(
            llm=self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            parent=self,
        )

    @property
    def model(self) -> str:
        return f"{self.primary.model}+fallback"

    @property
    def provider(self) -> str:
        return "openrouter-with-fallback"


class OfflineFridayLLMStream(lk_llm.LLMStream):
    async def _run(self) -> None:
        tool_call = self._build_tool_call()
        if tool_call is not None:
            self._event_ch.send_nowait(
                lk_llm.ChatChunk(
                    id=lk_utils.shortuuid("chunk_"),
                    delta=lk_llm.ChoiceDelta(role="assistant", tool_calls=[tool_call]),
                )
            )
            return

        response_text = self._build_response_text()
        self._event_ch.send_nowait(
            lk_llm.ChatChunk(
                id=lk_utils.shortuuid("chunk_"),
                delta=lk_llm.ChoiceDelta(role="assistant", content=response_text),
            )
        )

    def _latest_user_text(self) -> str:
        for message in reversed(self._chat_ctx.messages()):
            if message.role == "user":
                return (message.text_content or "").strip()
        return ""

    def _latest_tool_output(self) -> lk_llm.FunctionCallOutput | None:
        for item in reversed(self._chat_ctx.items):
            if isinstance(item, lk_llm.FunctionCallOutput):
                return item
        return None

    def _build_tool_call(self) -> lk_llm.FunctionToolCall | None:
        user_text = self._latest_user_text().lower()
        available_tools = {getattr(tool, "id", "") for tool in self._tools}

        if any(keyword in user_text for keyword in ["news", "brief me", "what's happening", "what happened", "world update"]):
            if "get_world_news" in available_tools:
                return lk_llm.FunctionToolCall(
                    name="get_world_news",
                    arguments="{}",
                    call_id=lk_utils.shortuuid("call_"),
                )

        if any(keyword in user_text for keyword in ["world monitor", "open monitor", "open world monitor"]):
            if "open_world_monitor" in available_tools:
                return lk_llm.FunctionToolCall(
                    name="open_world_monitor",
                    arguments="{}",
                    call_id=lk_utils.shortuuid("call_"),
                )

        return None

    def _build_response_text(self) -> str:
        tool_output = self._latest_tool_output()
        if tool_output is not None:
            if tool_output.is_error:
                return "I hit a snag on that, boss. Try again in a moment."

            text = (tool_output.output or "").strip()
            if tool_output.name == "get_world_news":
                summary = " ".join(text.split())
                if len(summary) > 360:
                    summary = summary[:360].rsplit(" ", 1)[0] + "..."
                return f"Here's the brief, boss. {summary}"

            if tool_output.name == "open_world_monitor":
                return "World monitor is open, boss."

            return f"I've got the result, boss. {text[:360]}"

        lowered_text = self._latest_user_text().lower()

        if any(keyword in lowered_text for keyword in ["hello", "hi", "hey"]):
            return "I'm here, boss. Standing by."
        if any(keyword in lowered_text for keyword in ["what are you up to", "what's up", "status", "how are you"]):
            return "All systems are nominal, boss. Standing by."
        if any(keyword in lowered_text for keyword in ["news", "brief me", "what's happening", "what happened", "world update"]):
            return "Give me a sec, boss. I’m pulling that now."

        return "I'm here, boss. What do you need?"


class OfflineFridayLLM(lk_llm.LLM):
    def __init__(self) -> None:
        super().__init__()

    def chat(
        self,
        *,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool] | None = None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> lk_llm.LLMStream:
        return OfflineFridayLLMStream(
            llm=self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )

    @property
    def model(self) -> str:
        return "offline-friday"

    @property
    def provider(self) -> str:
        return "local"


def _build_tts():
    logger.info("TTS → Sarvam Bulbul v3 (HTTP synth via StreamAdapter)")
    # Sarvam's streaming websocket path is returning non-WAV frames here, so
    # we route synthesis through the HTTP chunked path instead.
    return lk_tts.StreamAdapter(
        tts=sarvam.TTS(
            target_language_code=SARVAM_TTS_LANGUAGE,
            model="bulbul:v3",
            speaker=SARVAM_TTS_SPEAKER,
            pace=TTS_SPEED,
        ),
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class FridayAgent(Agent):
    """
    F.R.I.D.A.Y. – Iron Man-style voice assistant.
    All tools are provided via the MCP server on the Windows host.
    """

    def __init__(self, stt, llm, tts) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
            stt=stt,
            llm=llm,
            tts=tts,
            vad=silero.VAD.load(),
            mcp_servers=[
                mcp.MCPServerHTTP(
                    url=_mcp_server_url(),
                    transport_type="sse",
                    client_session_timeout_seconds=10,
                ),
            ],
        )

    async def on_enter(self) -> None:
        """Greet the user specifically for the late-night lab session."""
        await self.session.generate_reply(
            instructions=(
                "Greet the user exactly with: 'Greetings boss, you're awake late at night today. What you up to?' "
                "Maintain a helpful but dry tone."
            )
        )


# ---------------------------------------------------------------------------
# LiveKit entry point
# ---------------------------------------------------------------------------

def _turn_detection() -> str:
    return "stt"


def _endpointing_delay() -> float:
    raw = _safe_float(ENDPOINTING_DELAY_SECONDS, 0.025)
    # Keep endpointing low for speed, but clamp to a stable range.
    return max(0.02, min(0.08, raw))


def _validate_livekit_environment() -> None:
    required = [*REQUIRED_LIVEKIT_ENV, "SARVAM_API_KEY"]
    if LLM_PROVIDER == "openrouter":
        required.append("OPENROUTER_API_KEY")
    missing = [name for name in required if not os.getenv(name)]
    if not missing:
        return

    missing_vars = ", ".join(missing)
    raise SystemExit(
        "Missing LiveKit configuration: "
        f"{missing_vars}. Copy .env.example to .env and fill in your LiveKit Cloud credentials, "
        "or export the variables before running uv run friday_voice."
    )


async def entrypoint(ctx: JobContext) -> None:
    logger.info(
        "FRIDAY online – room: %s | STT=%s | LLM=%s | TTS=%s",
        ctx.room.name,
        STT_PROVIDER,
        LLM_PROVIDER,
        TTS_PROVIDER,
    )

    stt = _build_stt()
    llm = _build_llm()
    tts = _build_tts()

    session = AgentSession(
        turn_detection=_turn_detection(),
        min_endpointing_delay=_endpointing_delay(),
    )

    await session.start(
        agent=FridayAgent(stt=stt, llm=llm, tts=tts),
        room=ctx.room,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _validate_livekit_environment()
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

def dev():
    """Wrapper to run the agent in dev mode automatically."""
    import sys
    # If no command was provided, inject 'dev'
    if len(sys.argv) == 1:
        sys.argv.append("dev")
    main()

if __name__ == "__main__":
    main()