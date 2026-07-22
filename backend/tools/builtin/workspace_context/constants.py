WORKSPACE_TOOL_VERSION = "workspace-context-v1"
READONLY_SHELL_VERSION = "readonly-shell-v1"

DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    ".git/**",
    "**/.git/**",
    "node_modules/**",
    "**/node_modules/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pem",
    "**/*.pem",
    "*.key",
    "**/*.key",
    "*.p12",
    "**/*.p12",
    "*.sqlite",
    "**/*.sqlite",
    "*.db",
    "**/*.db",
)

_TEXT_DOC_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst", ".adoc"})
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".avif",
        ".bin",
        ".bmp",
        ".class",
        ".dmg",
        ".doc",
        ".docx",
        ".DS_Store",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".lockb",
        ".mp3",
        ".mp4",
        ".otf",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".pyc",
        ".so",
        ".tar",
        ".ttf",
        ".webp",
        ".woff",
        ".woff2",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
_ALLOWED_READONLY_COMMANDS = frozenset(
    {"ls", "find", "grep", "rg", "cat", "head", "tail", "wc"}
)
_SHELL_META_CHARS = frozenset("|&;><`$(){}[]\n\r")
_DANGEROUS_ARGS = frozenset(
    {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fprint",
        "-fprintf",
        "-fls",
        "-i",
        "--in-place",
        "--replace",
        "--files-from",
        "--pre",
        "--pre-glob",
    }
)
