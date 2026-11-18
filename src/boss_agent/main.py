"""
boss_agent.main
===============
The single composition root.

This is the only module that knows every concrete adapter class. CLI scripts under
``scripts/`` keep their paths (process identity is part of their contract) and shrink
to argparse, calls into the builders below, and process supervision; they do not
assemble broker, stores, device sessions, prompts, or LLM clients themselves.

Dependencies point inward: this module imports core and adapters, never the reverse,
and no core or adapter module imports it.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from boss_agent.adapters.inbound.verification.feed_verification import (
    build_verification_pipeline as _build_verification_pipeline,
)
from boss_agent.adapters.inbound.worker.config import WorkerConfig
from boss_agent.adapters.inbound.worker.context import WorkerContext
from boss_agent.adapters.inbound.worker.daemon import AutomationWorker
from boss_agent.adapters.inbound.worker.handlers import (
    AutoApplyHandler,
    CheckChatHandler,
    CheckLoginHandler,
    ScrapeJobsHandler,
)
from boss_agent.adapters.inbound.worker.scheduler import AutomationScheduler
from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore
from boss_agent.adapters.outbound.config import settings as _config_settings
from boss_agent.adapters.outbound.config.config_realm import (
    PLACEHOLDER_API_KEY,
    is_mask_placeholder,
)
from boss_agent.adapters.outbound.config.greeting_prompt import load_greeting_prompt
from boss_agent.adapters.outbound.config.screening_config import (
    ScreeningConfigBlacklistSink,
    load_screening_policy,
)
from boss_agent.adapters.outbound.config.screening_prompt import load_screening_prompt
from boss_agent.adapters.outbound.config.settings import (
    load_feed_config_defaults,
    resolve_chat_acknowledgment_settings,
    resolve_enable_scheduler,
    resolve_pocketbase_url,
    resolve_server_url,
)
from boss_agent.adapters.outbound.device.pages.chat_bundle import build_triage_world
from boss_agent.adapters.outbound.device.pages.feed_bundle import build_feed_device_world
from boss_agent.adapters.outbound.device.takeover import TakeoverHandler
from boss_agent.adapters.outbound.llm.llm_config import create_llm_client, load_llm_config
from boss_agent.adapters.outbound.llm.tracing import (
    TracedCandidateScreener,
    TracedJobMatchGreetingService,
    build_traced_screener,
)
from boss_agent.adapters.outbound.pocketbase.async_bridge import run_sync
from boss_agent.adapters.outbound.pocketbase.candidate_memory_store import (
    resolve_candidate_memory_store,
)
from boss_agent.adapters.outbound.pocketbase.pocketbase_adapter import PocketBaseTaskBroker
from boss_agent.adapters.outbound.pocketbase.saved_search_store import (
    missing_saved_search_message,
    resolve_saved_search_store,
)
from boss_agent.adapters.outbound.resume.graph import run_resume_lifecycle_graph
from boss_agent.adapters.outbound.resume.memory import ResumeMemoryManager, ResumeTextExtractor
from boss_agent.adapters.shared.prompts import load_prompt_or_default
from boss_agent.core.candidate.domain.candidate_profile import (
    CandidateProfile,
    stored_profile_is_substantive,
)
from boss_agent.core.candidate.interfaces.candidate_memory_store import CandidateMemoryStore
from boss_agent.core.chat.use_cases.rejection_classifier import RejectionClassifier
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.job.interfaces.device import FeedDeviceWorld
from boss_agent.core.job.interfaces.store import JobRecordStore
from boss_agent.core.job.use_cases.matching import JobMatchGreetingService
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.search.interfaces.store import SavedSearchStore
from boss_agent.core.task.interfaces.broker import BaseTaskBroker
from boss_agent.core.task.use_cases.startup_cleanup import StartupCleanupGate
from boss_agent.core.task.use_cases.sweeper import TaskLeaseSweeper
from droid_agent_core.driver import AppiumSession, DriverConfig
from droid_agent_core.llm import LLMConfig, LLMDecisionClient

_T = TypeVar("_T")

#: Device capabilities shared by the worker's lazy session and the live verification runner.
BOSS_APP_PACKAGE = "com.hpbr.bosszhipin"

#: Pre-refactor LLM budget defaults preserved for ``scripts/parse_resume.py``.
RESUME_PARSE_DEFAULT_TIMEOUT_SEC = 300.0
RESUME_PARSE_DEFAULT_MAX_TOKENS = 16384


# --------------------------------------------------------------------------- LLM


def _is_masked_key(value: str | None) -> bool:
    """Whether a configured key is unset or still a masked/placeholder display value."""
    if not value:
        return True
    stripped = str(value).strip()
    return not stripped or stripped == PLACEHOLDER_API_KEY or is_mask_placeholder(stripped)


def _is_missing_key(value: str | None) -> bool:
    """Whether a configured key is absent or blank, without judging a masked value."""
    return not value or not str(value).strip()


def build_llm_client(
    llm_config_arg: str | None = None,
    *,
    allow_config_path: bool = False,
    timeout_sec: float | None = None,
    max_tokens: int | None = None,
    treat_masked_key_as_missing: bool = True,
) -> LLMDecisionClient:
    """Build the LLM decision client from an optional inline JSON config.

    Every evaluation/refinement script routes through here, so which wire protocol is
    spoken is decided once by the configuration. By default a masked configured key falls
    back to the realm's real key rather than being sent on the wire; scripts that always
    passed a masked key through opt out with ``treat_masked_key_as_missing=False``.
    """
    if llm_config_arg:
        try:
            if llm_config_arg.strip().startswith("{"):
                config_data = json.loads(llm_config_arg)
                base_url = config_data.get("base_url") or "https://api.minimaxi.com/v1"
                api_key = config_data.get("api_key") or None
                model = config_data.get("model") or "MiniMax-M3"
                temperature = float(config_data.get("temperature") or 0.2)

                default_cfg = load_llm_config()
                configured_key = config_data.get("api_key")
                key_is_unusable = (
                    _is_masked_key(configured_key)
                    if treat_masked_key_as_missing
                    else _is_missing_key(configured_key)
                )
                if key_is_unusable:
                    api_key = default_cfg.api_key
                if (not base_url or base_url == "https://api.openai.com/v1") and key_is_unusable:
                    base_url = default_cfg.base_url
                    model = default_cfg.model

                overrides: dict[str, Any] = {}
                # Explicit caller overrides win, then the config's own budget, then the
                # pre-refactor defaults. parse_resume (`allow_config_path`) always ran the
                # resume lifecycle with 300s/16384 regardless of the framework defaults.
                if timeout_sec is not None:
                    overrides["timeout_sec"] = timeout_sec
                elif config_data.get("timeout_sec") is not None:
                    overrides["timeout_sec"] = float(config_data["timeout_sec"])
                elif allow_config_path:
                    overrides["timeout_sec"] = RESUME_PARSE_DEFAULT_TIMEOUT_SEC
                if max_tokens is not None:
                    overrides["max_tokens"] = max_tokens
                elif config_data.get("max_tokens") is not None:
                    overrides["max_tokens"] = int(config_data["max_tokens"])
                elif allow_config_path:
                    overrides["max_tokens"] = RESUME_PARSE_DEFAULT_MAX_TOKENS
                llm_cfg = LLMConfig(
                    provider=config_data.get("provider", default_cfg.provider),
                    base_url=base_url.rstrip("/"),
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    **overrides,
                )
                return create_llm_client(config=llm_cfg)
            if allow_config_path:
                return create_llm_client(config=load_llm_config(llm_config_arg))
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse custom LLM config ({e}), falling back.\n")

    return create_llm_client()


def build_matching_service(
    *,
    llm_client: LLMDecisionClient | None = None,
    candidate_profile: CandidateProfile | None = None,
    greeting_prompt: str | None = None,
) -> TracedJobMatchGreetingService:
    """The traced greeting service, with the settled Greeting Prompt injected verbatim."""
    resolved_greeting = (
        greeting_prompt
        if greeting_prompt is not None
        else load_prompt_or_default(load_greeting_prompt)
    )
    return TracedJobMatchGreetingService(
        llm_client=llm_client or create_llm_client(),
        candidate_profile=candidate_profile,
        greeting_prompt=resolved_greeting,
    )


def build_screener(
    *,
    llm_client: LLMDecisionClient | None = None,
    matching_service: JobMatchGreetingService | None = None,
    greeting_prompt: str | None = None,
    screening_prompt: str | None = None,
) -> TracedCandidateScreener:
    """The traced screener; prompt documents are injected, never read by core."""
    return build_traced_screener(
        llm_client=llm_client or create_llm_client(),
        matching_service=matching_service,
        greeting_prompt=greeting_prompt,
        screening_prompt=screening_prompt,
    )


def build_default_screening_policy() -> ScreeningPolicy:
    """Load the active ScreeningPolicy from resolved settings, degrading to defaults."""
    try:
        return ScreeningPolicy.from_dict(_config_settings.load_settings())
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to load default screening policy ({e})\n")
        return ScreeningPolicy()


def resolve_screening_prompt(explicit_prompt: str | None = None) -> str | None:
    """Return an explicit screening prompt or load the realm document, degrading to None."""
    if explicit_prompt is not None:
        return explicit_prompt
    return load_prompt_or_default(load_screening_prompt, default=None)


# ------------------------------------------------------------------- worker daemon


def _driver_factory(config: WorkerConfig) -> Any:
    def create() -> Any:
        logging.getLogger("worker_main").info(
            "Initializing Appium driver session for device %s", config.device_id
        )
        driver_cfg = DriverConfig(
            server_url=config.appium_url,
            app_package=BOSS_APP_PACKAGE,
            extra_capabilities={"udid": config.device_id},
        )
        return AppiumSession(driver_cfg).start()

    return create


def build_worker(
    *,
    worker_id: str | None = None,
    device_id: str = "emulator-5554",
    appium_url: str | None = None,
    pocketbase_url: str | None = None,
    poll_interval_sec: float = 2.0,
    enable_scheduler: bool | None = None,
) -> AutomationWorker:
    """Compose one worker process: broker, session factory, handlers, and startup gate."""
    resolved_pb_url = resolve_pocketbase_url(explicit_url=pocketbase_url)
    resolved_appium_url = resolve_server_url(explicit_url=appium_url)

    config = WorkerConfig(
        worker_id=worker_id or f"worker-{device_id}",
        device_id=device_id,
        appium_url=resolved_appium_url,
        pocketbase_url=resolved_pb_url,
        poll_interval_sec=poll_interval_sec,
        enable_scheduler=resolve_enable_scheduler(explicit=enable_scheduler),
    )
    broker = PocketBaseTaskBroker(base_url=resolved_pb_url)
    context = WorkerContext(config=config, driver_factory=_driver_factory(config))

    chat_acknowledgment = resolve_chat_acknowledgment_settings()
    # One gate shared by both services in this process, so the integrated scheduler
    # queues the startup 拒信清扫 exactly once (#230).
    startup_gate = StartupCleanupGate(
        broker,
        enabled=config.run_cleanup_on_startup,
        acknowledgment=chat_acknowledgment,
    )
    llm_client = create_llm_client()
    screener = build_screener(
        llm_client=llm_client,
        greeting_prompt=load_prompt_or_default(load_greeting_prompt),
        screening_prompt=load_prompt_or_default(load_screening_prompt) or None,
    )
    return AutomationWorker(
        config=config,
        broker=broker,
        context=context,
        handlers=[
            CheckLoginHandler(),
            ScrapeJobsHandler(
                screener=screener,
                pages=build_feed_device_world,
                job_store=broker.job_store,
                feed_config_defaults_loader=load_feed_config_defaults,
            ),
            AutoApplyHandler(
                screener=screener,
                pages=build_feed_device_world,
                job_store=broker.job_store,
                candidate_memory=broker.candidate_memory,
                feed_config_defaults_loader=load_feed_config_defaults,
            ),
            CheckChatHandler(
                classifier=RejectionClassifier(llm_client=llm_client),
                blacklist_sink=ScreeningConfigBlacklistSink(),
                settings=chat_acknowledgment,
                pages=build_triage_world,
                policy_loader=load_screening_policy,
            ),
        ],
        startup_gate=startup_gate,
    )


def build_scheduler(
    *,
    pocketbase_url: str | None = None,
    poll_interval_sec: float = 30.0,
    broker: BaseTaskBroker | None = None,
    saved_searches: SavedSearchStore | None = None,
) -> AutomationScheduler:
    """Compose the standalone Cron scheduler daemon."""
    resolved_pb_url = resolve_pocketbase_url(explicit_url=pocketbase_url)
    resolved_broker = broker or PocketBaseTaskBroker(base_url=resolved_pb_url)
    resolved_searches = saved_searches or resolve_saved_search_store(base_url=resolved_pb_url)
    return AutomationScheduler(
        broker=resolved_broker,
        saved_searches=resolved_searches,
        poll_interval_sec=poll_interval_sec,
    )


def build_supervised_services(
    worker: AutomationWorker,
    *,
    scheduler_poll_interval_sec: float = 30.0,
    saved_searches: SavedSearchStore | None = None,
) -> list[Coroutine[Any, Any, None]]:
    """The auxiliary loops supervised beside the worker's claim loop."""
    config = worker.config
    services: list[Coroutine[Any, Any, None]] = [
        TaskLeaseSweeper(
            broker=worker.broker,
            lease_timeout_sec=config.lease_timeout_sec,
            retry_limit=config.max_task_retries,
        ).start(interval_sec=config.lease_sweep_interval_sec)
    ]
    if config.enable_scheduler:
        resolved_searches = saved_searches or resolve_saved_search_store(
            base_url=config.pocketbase_url
        )
        services.append(
            AutomationScheduler(
                broker=worker.broker,
                saved_searches=resolved_searches,
                poll_interval_sec=scheduler_poll_interval_sec,
                startup_gate=worker.startup_gate,
            ).run_forever()
        )
    return services


# --------------------------------------------------------------------- device/session


def build_device_session(*, device_udid: str, server_url: str) -> AppiumSession:
    """A not-yet-started Appium session configured for the Boss 直聘 app."""
    return AppiumSession(
        DriverConfig(
            server_url=server_url,
            platform_name="Android",
            automation_name="UiAutomator2",
            device_name=device_udid,
            app_package=BOSS_APP_PACKAGE,
            app_activity="com.hpbr.bosszhipin.module.launcher.WelcomeActivity",
            no_reset=True,
            auto_grant_permissions=True,
            new_command_timeout=300,
            extra_capabilities={
                "appium:udid": device_udid,
                "appium:uiautomator2ServerInstallTimeout": 60000,
                "appium:adbExecTimeout": 60000,
                "appium:ensureWebviewsHavePages": True,
                "appium:nativeWebScreenshot": True,
            },
        )
    )


def build_takeover_handler(driver: Any, *, auto_confirm_for_test: bool = False) -> TakeoverHandler:
    return TakeoverHandler(driver, auto_confirm_for_test=auto_confirm_for_test)


def build_resume_memory_manager(
    *,
    llm_client: LLMDecisionClient | None = None,
    candidate_memory: CandidateMemoryStore | None = None,
    memory_file_path: str | Path | None = None,
) -> ResumeMemoryManager:
    client = llm_client or create_llm_client()
    store = candidate_memory or resolve_candidate_memory_store()
    return ResumeMemoryManager(
        llm_client=client,
        candidate_memory=store,
        memory_file_path=memory_file_path,
    )


def load_cached_candidate_profile(
    *,
    candidate_memory: CandidateMemoryStore | None = None,
) -> CandidateProfile | None:
    """Read the persisted CandidateProfile without constructing an LLM client or task broker."""
    store = candidate_memory or resolve_candidate_memory_store()
    data = run_sync(store.get_candidate_profile(), timeout=5.0)
    if stored_profile_is_substantive(data):
        return CandidateProfile.from_dict(data)
    return None


def build_saved_search_store(*, prefer_database: bool = True) -> Any:
    return resolve_saved_search_store(prefer_database=prefer_database)


def load_runner_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load merged Configuration Realm settings for CLI runners."""
    return _config_settings.load_settings(config_path=config_path)


def format_resolved_runner_settings(
    keys: list[str], *, settings: dict[str, Any] | None = None
) -> str:
    """Format Configuration Realm values as ``key=value`` lines for shell runners."""
    from boss_agent.adapters.outbound.config.config_realm import format_resolved_values

    return format_resolved_values(keys, settings=settings)


def run_async(coro: Coroutine[Any, Any, _T], *, timeout: float | None = None) -> _T:
    """Bridge an async port call for synchronous CLI callers."""
    return run_sync(coro, timeout=timeout)


def format_missing_saved_search_message(search_id: str, available: list[str]) -> str:
    """Format the diagnostic message when a named SavedSearch preset is absent."""
    return missing_saved_search_message(search_id, available)


# ------------------------------------------------------------------- verification


def build_verification_pipeline(
    driver: Any,
    *,
    matching_service: JobMatchGreetingService | None = None,
    screener: CandidateScreener | None = None,
    world: FeedDeviceWorld | None = None,
    store: JobRecordStore | None = None,
    **kwargs: Any,
) -> Any:
    """One verification feed run, composing the traced screener and prompt at this edge."""
    resolved_matching = matching_service or build_matching_service()
    resolved_screener = screener or build_screener(
        llm_client=resolved_matching.llm_client,
        matching_service=resolved_matching,
        screening_prompt=load_prompt_or_default(load_screening_prompt) or "",
    )
    return _build_verification_pipeline(
        driver,
        screener=resolved_screener,
        world=world or build_feed_device_world(driver),
        store=store or InMemoryJobRecordStore(),
        **kwargs,
    )


# ----------------------------------------------------------------------- resume


@dataclass(frozen=True)
class ResumePipeline:
    """A resume text extractor plus the wired LangGraph lifecycle runner."""

    extractor: ResumeTextExtractor
    memory_manager: ResumeMemoryManager
    candidate_memory: CandidateMemoryStore

    def run(self, **kwargs: Any) -> Any:
        return run_resume_lifecycle_graph(
            llm_client=self.memory_manager.llm_client,
            candidate_memory=self.candidate_memory,
            **kwargs,
        )


def build_resume_text_source() -> ResumeTextExtractor:
    """The text extractor alone, before an LLM client is worth building."""
    return ResumeTextExtractor()


def build_resume_pipeline(
    *,
    llm_client: LLMDecisionClient | None = None,
    memory_file_path: str | Path | None = None,
    candidate_memory: CandidateMemoryStore | None = None,
) -> ResumePipeline:
    resolved_memory = candidate_memory or resolve_candidate_memory_store()
    manager = build_resume_memory_manager(
        llm_client=llm_client,
        candidate_memory=resolved_memory,
        memory_file_path=memory_file_path,
    )
    return ResumePipeline(
        extractor=manager.extractor,
        memory_manager=manager,
        candidate_memory=resolved_memory,
    )


__all__ = [
    "RESUME_PARSE_DEFAULT_MAX_TOKENS",
    "RESUME_PARSE_DEFAULT_TIMEOUT_SEC",
    "ResumePipeline",
    "build_default_screening_policy",
    "build_device_session",
    "build_llm_client",
    "build_matching_service",
    "build_resume_memory_manager",
    "build_resume_pipeline",
    "build_resume_text_source",
    "build_saved_search_store",
    "build_scheduler",
    "build_screener",
    "build_supervised_services",
    "build_takeover_handler",
    "build_verification_pipeline",
    "build_worker",
    "format_missing_saved_search_message",
    "format_resolved_runner_settings",
    "load_cached_candidate_profile",
    "load_runner_settings",
    "resolve_screening_prompt",
    "run_async",
]
