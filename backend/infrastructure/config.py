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
    database_url: str = "postgresql+asyncpg://placeholder"
    redis_url: str = "redis://placeholder"
    sentry_dsn: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str | None = None
    langbot_webhook_secret: str = "placeholder-langbot-webhook-secret"
    langbot_api_base_url: str = "https://langbot.invalid"
    langbot_api_key: str = "placeholder-langbot-api-key"
    langbot_send_timeout_seconds: float = 10.0
    deepseek_api_key: str = "placeholder-deepseek-api-key"
    deepseek_base_url: str = "https://deepseek.invalid/v1"
    deepseek_light_model: str = "deepseek-light-placeholder"
    deepseek_standard_model: str = "deepseek-standard-placeholder"
    models_timeout_seconds: float = 10.0
    models_retry_attempts: int = 2
    models_nodes_json: str = ""
    tavily_base_url: str = "https://tavily.invalid"
    tavily_api_key: str = "placeholder-tavily-api-key"
    tavily_timeout_seconds: float = 10.0
    tavily_max_results: int = 5
    search_provider_order: str = "tavily,brave,duckduckgo"
    brave_search_api_key: str = ""
    brave_search_base_url: str = "https://api.search.brave.com/res/v1/web/search"
    duckduckgo_search_enabled: bool = False
    duckduckgo_search_base_url: str = "https://api.duckduckgo.com/"
    search_fallback_on_empty: bool = True
    search_provider_timeout_seconds: float | None = None
    running_task_timeout_seconds: float = 300.0
    pending_task_compensation_delay_seconds: float = 120.0
    scheduler_maintenance_interval_seconds: float = 300.0
    managed_skills_root: Path = Path("var/skills")
    managed_prompts_root: Path = Path("var/prompts")
    skill_packages_root: Path = Path("var/skill-packages")
    artifacts_root: Path = Path("var/artifacts")
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
    sandbox_enabled: bool = False
    sandbox_image: str = ""
    sandbox_allowed_images: str = ""
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
        if self.sandbox_provider != "none":
            return self.sandbox_provider
        if self.sandbox_enabled:
            return "docker"
        return "none"

    @property
    def effective_shell_exec_enabled(self) -> bool:
        """处理 effective shell exec enabled。"""
        return self.shell_exec_enabled or self.sandbox_enabled

    @property
    def effective_sandbox_docker_image(self) -> str:
        """处理 effective sandbox docker image。"""
        return self.sandbox_docker_image or self.sandbox_image

    @property
    def effective_sandbox_docker_allowed_images(self) -> str:
        """处理 effective sandbox docker allowed images。"""
        return self.sandbox_docker_allowed_images or self.sandbox_allowed_images

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
