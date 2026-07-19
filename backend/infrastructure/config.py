from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    model_gateway_timeout_seconds: float = 10.0
    model_gateway_retry_attempts: int = 2
    model_gateway_nodes_json: str = ""
    tavily_base_url: str = "https://tavily.invalid"
    tavily_api_key: str = "placeholder-tavily-api-key"
    tavily_timeout_seconds: float = 10.0
    tavily_max_results: int = 5
    running_task_timeout_seconds: float = 300.0
    pending_task_compensation_delay_seconds: float = 120.0
    scheduler_maintenance_interval_seconds: float = 300.0
    managed_skills_root: Path = Path("var/skills")
    managed_prompts_root: Path = Path("var/prompts")
    skill_packages_root: Path = Path("var/skill-packages")
    artifacts_root: Path = Path("var/artifacts")
    knowledge_root: Path = Path("var/knowledge")
    browser_state_root: Path = Path("var/browser")
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

    @property
    def effective_sandbox_provider(self) -> Literal["none", "docker"]:
        if self.sandbox_provider != "none":
            return self.sandbox_provider
        if self.sandbox_enabled:
            return "docker"
        return "none"

    @property
    def effective_shell_exec_enabled(self) -> bool:
        return self.shell_exec_enabled or self.sandbox_enabled

    @property
    def effective_sandbox_docker_image(self) -> str:
        return self.sandbox_docker_image or self.sandbox_image

    @property
    def effective_sandbox_docker_allowed_images(self) -> str:
        return self.sandbox_docker_allowed_images or self.sandbox_allowed_images

    @property
    def effective_sandbox_docker_allowed_images_tuple(self) -> tuple[str, ...]:
        return tuple(
            item.strip()
            for item in self.effective_sandbox_docker_allowed_images.split(",")
            if item.strip()
        )


def load_settings() -> Settings:
    return Settings()
