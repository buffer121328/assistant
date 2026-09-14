from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """表示 处理 settings 的后端数据结构或服务对象。"""

    app_env: str = "local"
    log_level: str = "INFO"
    service_name: str = "assistant-api"
    local_api_auth_required: bool = False
    local_api_token: SecretStr = SecretStr("")
    credential_master_key: SecretStr = SecretStr("")
    admin_recovery_enabled: bool = False
    admin_recovery_question: str = ""
    admin_recovery_answer_verifier: SecretStr = SecretStr("")
    admin_recovery_max_attempts: int = 5
    admin_recovery_cooldown_minutes: int = 15
    database_url: str = "postgresql+asyncpg://placeholder"
    redis_url: str = "redis://placeholder"
    sentry_dsn: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str | None = None
    langfuse_prompt_management_enabled: bool = False
    langfuse_prompt_label: str = "production"
    langfuse_prompt_module_map_json: str = ""
    langbot_webhook_secret: str = "placeholder-langbot-webhook-secret"
    langbot_api_base_url: str = "https://langbot.invalid"
    langbot_api_key: str = "placeholder-langbot-api-key"
    langbot_send_timeout_seconds: float = 10.0
    deepseek_api_key: str = "placeholder-deepseek-api-key"
    deepseek_base_url: str = "https://deepseek.invalid/v1"
    deepseek_light_model: str = "deepseek-light-placeholder"
    deepseek_standard_model: str = "deepseek-standard-placeholder"
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_rag_model: str = "qwen-plus"
    models_timeout_seconds: float = 10.0
    models_retry_attempts: int = 2
    models_nodes_json: str = ""
    tavily_api_key: str = "placeholder-tavily-api-key"
    tavily_timeout_seconds: float = 10.0
    tavily_max_results: int = 5
    search_provider_order: str = "tavily,serper,exa"
    serper_api_key: str = ""
    serper_base_url: str = "https://google.serper.dev/search"
    exa_api_key: str = ""
    exa_base_url: str = "https://api.exa.ai/search"
    search_fallback_on_empty: bool = True
    search_provider_timeout_seconds: float | None = None
    running_task_timeout_seconds: float = 300.0
    pending_task_compensation_delay_seconds: float = 120.0
    scheduler_maintenance_interval_seconds: float = 300.0
    managed_skills_root: Path = Path("var/skills")
    managed_prompts_root: Path = Path("var/prompts")
    skill_packages_root: Path = Path("var/skill-packages")
    artifacts_root: Path = Path("var/artifacts")
    resource_uploads_root: Path = Path("var/resource-inputs")
    desktop_capture_enabled: bool = False
    desktop_capture_mode: Literal["local_process", "controller_http"] = "local_process"
    desktop_capture_controller_url: str = "http://127.0.0.1:8765/screenshot"
    desktop_capture_default_target: Literal["desktop", "frontend"] = "frontend"
    desktop_capture_require_confirmation_for_desktop: bool = True
    desktop_capture_artifact_ttl_seconds: int = 3600
    session_workspace_root: Path = Path("var/workspace/sessions")
    workspace_context_root: Path = Path(".")
    workspace_context_enabled: bool = True
    workspace_context_deny_globs: str = ".env,.env.*,**/.env,**/.env.*,.git/**,**/.git/**,node_modules/**,**/node_modules/**,__pycache__/**,**/__pycache__/**,*.pem,**/*.pem,*.key,**/*.key,*.p12,**/*.p12,*.sqlite,**/*.sqlite,*.db,**/*.db"
    workspace_context_max_file_bytes: int = 200_000
    workspace_context_max_results: int = 50
    readonly_shell_enabled: bool = False
    readonly_shell_timeout_seconds: float = 10.0
    readonly_shell_max_output_chars: int = 50_000
    knowledge_root: Path = Path("var/knowledge")
    browser_enabled: bool = False
    browser_timeout_seconds: float = 20.0
    browser_max_text_chars: int = 50_000
    sandbox_provider: Literal["none", "docker"] = "none"
    shell_exec_enabled: bool = False
    sandbox_workspace_root: Path = Path("var/sandbox")
    sandbox_docker_image: str = ""
    sandbox_docker_allowed_images: str = ""
    sandbox_timeout_seconds: float = 30.0
    subagent_enabled: bool = True
    subagent_max_count: int = 3
    subagent_concurrency: int = 2
    subagent_timeout_seconds: float = 30.0
    mem0_config_path: Path | None = None
    mem0_search_limit: int = 5
    quality_judge_sample_rate: float = 0.0
    quality_judge_policy_version: str = "judge-v1"
    quality_judge_threshold: float = 0.6
    content_governance_enabled: bool = True
    content_governance_provider: Literal["local", "guardrails_ai"] = "local"
    production_provider_enabled: bool = False
    production_provider_timeout_seconds: float = 20.0
    shared_semantic_provider_enabled: bool = False
    postgresql_rls_required: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("search_provider_timeout_seconds", mode="before")
    @classmethod
    def blank_search_provider_timeout_is_none(cls, value: object) -> object:
        """处理 blank search provider timeout is none。

        Args:
            value: value 参数。
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def effective_sandbox_provider(self) -> Literal["none", "docker"]:
        """处理 effective sandbox provider。"""
        return self.sandbox_provider

    @property
    def effective_shell_exec_enabled(self) -> bool:
        """处理 effective shell exec enabled。"""
        return self.shell_exec_enabled

    @property
    def effective_sandbox_docker_image(self) -> str:
        """处理 effective sandbox docker image。"""
        return self.sandbox_docker_image

    @property
    def effective_sandbox_docker_allowed_images(self) -> str:
        """处理 effective sandbox docker allowed images。"""
        return self.sandbox_docker_allowed_images

    @property
    def effective_sandbox_docker_allowed_images_tuple(self) -> tuple[str, ...]:
        """处理 effective sandbox docker allowed images tuple。"""
        return tuple(
            item.strip()
            for item in self.effective_sandbox_docker_allowed_images.split(",")
            if item.strip()
        )


def load_settings() -> Settings:
    """加载 settings。"""
    return Settings()
