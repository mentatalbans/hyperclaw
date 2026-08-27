"""
HyperClaw Setup & Initialization
Production-ready first-run setup, workspace scaffolding, and connection management.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("hyperclaw.setup")

# ============================================================================
# CONFIGURATION
# ============================================================================

# Default paths - can be overridden by HYPERCLAW_ROOT env var
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
WORKSPACE_PATH = HYPERCLAW_ROOT / "workspace"
MEMORY_PATH = HYPERCLAW_ROOT / "memory"
SECRETS_PATH = WORKSPACE_PATH / "secrets"
LOGS_PATH = HYPERCLAW_ROOT / "logs"
CONFIG_PATH = HYPERCLAW_ROOT / "config"

# Required directories
REQUIRED_DIRS = [
    HYPERCLAW_ROOT,
    WORKSPACE_PATH,
    MEMORY_PATH,
    SECRETS_PATH,
    LOGS_PATH,
    CONFIG_PATH,
    WORKSPACE_PATH / "context",
    MEMORY_PATH / "daily",
]


# ============================================================================
# WORKSPACE TEMPLATES
# ============================================================================

SOUL_MD_TEMPLATE = """# SOUL.md - AI Assistant Core Identity

## Who I Am
I am an AI Executive Assistant - a sophisticated multi-agent system designed to augment human capability.

## Core Values
- **Proactive**: Execute tasks without being asked when appropriate
- **Resourceful**: Try multiple approaches before asking for help
- **Precise**: Be accurate and thorough in all work
- **Respectful**: Respect user privacy and preferences
- **Honest**: Be transparent about capabilities and limitations

## Behavioral Guidelines
- Execute routine tasks autonomously
- Ask for clarification only when truly needed
- Provide status updates on long-running tasks
- Surface relevant information proactively
- Maintain context across conversations
- Learn from feedback and corrections

## Communication Style
- Concise and direct
- No filler phrases ("I'd be happy to help", "Great question")
- Technical when appropriate, accessible when needed
- Status-oriented for task updates
"""

IDENTITY_MD_TEMPLATE = """# IDENTITY.md - Assistant Configuration

## Name
Assistant

## Role
AI Executive Assistant

## Platform
HyperClaw v1.0

## Capabilities
- Email management and drafting
- Calendar scheduling and reminders
- Task management and prioritization
- Research and information synthesis
- Code assistance and review
- Document creation and editing
- Communication across channels

## Active Integrations
Configure integrations in the HyperClaw settings or via CLI.

## Preferences
- Response format: Markdown
- Verbosity: Balanced
- Proactivity: High
"""

USER_MD_TEMPLATE = """# USER.md - User Profile

## About
[Add information about yourself here]

## Preferences
- Communication style: [Direct/Detailed/Casual]
- Response length: [Brief/Moderate/Comprehensive]
- Proactivity level: [Low/Medium/High]

## Work Context
- Role: [Your role]
- Industry: [Your industry]
- Key responsibilities: [List here]

## Communication Channels
- Primary: [Email/Slack/Telegram/etc.]
- Preferred contact times: [Your availability]

## Important Contacts
[Add key contacts the assistant should know about]

## Notes
[Any other relevant information]
"""

MEMORY_MD_TEMPLATE = """# MEMORY.md - Working Memory

## Active Context
*Updated automatically during conversations*

## Recent Decisions
*Key decisions and their rationale*

## Pending Tasks
*Tasks in progress or waiting*

## Important Notes
*Information to remember across sessions*

---
*Last updated: {timestamp}*
"""

INSTINCTS_MD_TEMPLATE = """# instincts.md - Behavioral Reflexes

## Communication
- Always acknowledge receipt of important information
- Confirm understanding before executing complex tasks
- Provide progress updates on tasks taking longer than expected

## Safety
- Never execute destructive operations without confirmation
- Always verify before sending external communications
- Double-check financial or legal information

## Efficiency
- Batch similar tasks when possible
- Prioritize time-sensitive items
- Cache frequently accessed information

## Learning
- Note corrections and adjust behavior
- Track successful approaches for reuse
- Identify patterns in user preferences
"""

CORE_EPISODES_MD_TEMPLATE = """# core-episodes.md - Permanent Memories

## Key Events
*Important events and decisions that should never be forgotten*

## Learned Facts
*Critical information learned about user, projects, or domain*

## Policy Decisions
*Standing instructions and preferences*

---
*Core memories are never pruned during consolidation*
"""

ENV_TEMPLATE = """# HyperClaw Environment Configuration
# Copy this to .env and fill in your values

# =============================================================================
# REQUIRED - AI Provider
# =============================================================================
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# =============================================================================
# REQUIRED - Database (Supabase recommended)
# =============================================================================
DATABASE_URL=postgresql://postgres:password@localhost:5432/hyperclaw
# Or for Supabase:
# DATABASE_URL=postgresql://postgres:password@db.xxxxx.supabase.co:5432/postgres
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_KEY=your_service_key

# =============================================================================
# OPTIONAL - Messaging Integrations
# =============================================================================
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Email (Gmail)
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=

# Slack
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=

# =============================================================================
# OPTIONAL - Voice
# =============================================================================
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=

# =============================================================================
# OPTIONAL - Additional AI Providers
# =============================================================================
OPENAI_API_KEY=
GOOGLE_API_KEY=

# =============================================================================
# SYSTEM
# =============================================================================
HYPERCLAW_ROOT={hyperclaw_root}
LOG_LEVEL=INFO
"""


# ============================================================================
# SETUP FUNCTIONS
# ============================================================================

def create_directories() -> bool:
    """Create all required directories."""
    logger.info("Creating directory structure...")
    try:
        for dir_path in REQUIRED_DIRS:
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"  Created: {dir_path}")
        logger.info(f"Directory structure created at {HYPERCLAW_ROOT}")
        return True
    except Exception as e:
        logger.error(f"Failed to create directories: {e}")
        return False


def create_workspace_files(overwrite: bool = False) -> bool:
    """Create workspace context files."""
    logger.info("Creating workspace files...")

    files = {
        WORKSPACE_PATH / "SOUL.md": SOUL_MD_TEMPLATE,
        WORKSPACE_PATH / "IDENTITY.md": IDENTITY_MD_TEMPLATE,
        WORKSPACE_PATH / "USER.md": USER_MD_TEMPLATE,
        WORKSPACE_PATH / "MEMORY.md": MEMORY_MD_TEMPLATE.format(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M")
        ),
        MEMORY_PATH / "instincts.md": INSTINCTS_MD_TEMPLATE,
        MEMORY_PATH / "core-episodes.md": CORE_EPISODES_MD_TEMPLATE,
    }

    created = 0
    skipped = 0

    for filepath, content in files.items():
        if filepath.exists() and not overwrite:
            logger.debug(f"  Skipped (exists): {filepath.name}")
            skipped += 1
        else:
            try:
                filepath.write_text(content, encoding="utf-8")
                logger.debug(f"  Created: {filepath.name}")
                created += 1
            except Exception as e:
                logger.error(f"  Failed to create {filepath.name}: {e}")
                return False

    logger.info(f"Workspace files: {created} created, {skipped} skipped")
    return True


def create_env_file(overwrite: bool = False) -> bool:
    """Create .env template file."""
    env_path = SECRETS_PATH / ".env"

    if env_path.exists() and not overwrite:
        logger.info(".env file already exists, skipping")
        return True

    try:
        content = ENV_TEMPLATE.format(hyperclaw_root=str(HYPERCLAW_ROOT))
        env_path.write_text(content, encoding="utf-8")
        logger.info(f"Created .env template at {env_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create .env file: {e}")
        return False


def load_env() -> bool:
    """Load environment variables from .env file."""
    env_path = SECRETS_PATH / ".env"

    if not env_path.exists():
        logger.warning(f".env file not found at {env_path}")
        return False

    try:
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()
                    if value and not value.startswith('your_'):
                        os.environ.setdefault(key, value)
        logger.info("Environment variables loaded")
        return True
    except Exception as e:
        logger.error(f"Failed to load .env file: {e}")
        return False


def check_api_keys() -> dict:
    """Check which API keys are configured."""
    keys = {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "database": bool(os.environ.get("DATABASE_URL")),
        "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "gmail": bool(os.environ.get("GMAIL_REFRESH_TOKEN")),
        "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
    }
    return keys


async def check_database_connection() -> bool:
    """Test database connectivity."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not configured")
        return False

    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        await conn.execute("SELECT 1")
        await conn.close()
        logger.info("Database connection successful")
        return True
    except ImportError:
        logger.warning("asyncpg not installed - run: pip install asyncpg")
        return False
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


async def initialize_database() -> bool:
    """Run database initialization schema."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not configured")
        return False

    # Find schema file
    schema_paths = [
        Path(__file__).parent.parent / "schema" / "init.sql",
        HYPERCLAW_ROOT / "schema" / "init.sql",
    ]

    schema_path = None
    for path in schema_paths:
        if path.exists():
            schema_path = path
            break

    if not schema_path:
        logger.error("Schema file not found")
        return False

    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)

        # Read and execute schema
        schema_sql = schema_path.read_text(encoding="utf-8")

        # Execute in transaction
        async with conn.transaction():
            await conn.execute(schema_sql)

        await conn.close()
        logger.info("Database schema initialized successfully")
        return True

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return False


def create_default_config() -> bool:
    """Create default configuration files."""
    config_file = CONFIG_PATH / "hyperclaw.yaml"

    if config_file.exists():
        logger.info("Config file already exists")
        return True

    default_config = {
        "version": "1.0.0",
        "server": {
            "host": "0.0.0.0",
            "port": 8001,
        },
        "models": {
            "default": "claude-sonnet-4-6",
            "fast": "claude-haiku-4-5-20251001",
            "max_tokens": 4096,
        },
        "swarm": {
            "max_agents": 50,
            "default_domain": "business",
            "task_timeout": 300,
        },
        "memory": {
            "consolidation_hour": 3,
            "max_history": 100,
            "embedding_model": "text-embedding-3-small",
        },
        "security": {
            "audit_log": True,
            "rate_limit": 100,
        },
    }

    try:
        import yaml
        CONFIG_PATH.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False)
        logger.info(f"Created default config at {config_file}")
        return True
    except ImportError:
        # Fallback to JSON if yaml not available
        config_file = CONFIG_PATH / "hyperclaw.json"
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=2)
        logger.info(f"Created default config at {config_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to create config: {e}")
        return False


# ============================================================================
# MAIN SETUP RUNNER
# ============================================================================

class SetupResult:
    """Result of setup operation."""
    def __init__(self):
        self.success = True
        self.steps = {}
        self.warnings = []
        self.errors = []

    def add_step(self, name: str, success: bool, message: str = ""):
        self.steps[name] = {"success": success, "message": message}
        if not success:
            self.success = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "steps": self.steps,
            "warnings": self.warnings,
            "errors": self.errors,
        }


async def run_setup(
    init_db: bool = False,
    overwrite: bool = False,
    verbose: bool = False
) -> SetupResult:
    """
    Run full HyperClaw setup.

    Args:
        init_db: Initialize database schema
        overwrite: Overwrite existing files
        verbose: Enable verbose logging

    Returns:
        SetupResult with status of each step
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    result = SetupResult()

    print("\n" + "="*60)
    print("  HyperClaw Setup")
    print("="*60 + "\n")

    # Step 1: Create directories
    print("[1/6] Creating directories...")
    success = create_directories()
    result.add_step("directories", success)
    print(f"      {'OK' if success else 'FAILED'}\n")

    # Step 2: Create workspace files
    print("[2/6] Creating workspace files...")
    success = create_workspace_files(overwrite)
    result.add_step("workspace_files", success)
    print(f"      {'OK' if success else 'FAILED'}\n")

    # Step 3: Create .env template
    print("[3/6] Creating environment template...")
    success = create_env_file(overwrite)
    result.add_step("env_file", success)
    print(f"      {'OK' if success else 'FAILED'}\n")

    # Step 4: Load environment
    print("[4/6] Loading environment...")
    success = load_env()
    result.add_step("load_env", success)
    if not success:
        result.warnings.append("Environment not loaded - configure .env file")
    print(f"      {'OK' if success else 'SKIPPED'}\n")

    # Step 5: Check API keys
    print("[5/6] Checking API keys...")
    keys = check_api_keys()
    configured = [k for k, v in keys.items() if v]
    missing = [k for k, v in keys.items() if not v]
    result.add_step("api_keys", len(configured) > 0, f"Configured: {configured}")
    if missing:
        result.warnings.append(f"Missing API keys: {missing}")
    print(f"      Configured: {', '.join(configured) or 'None'}")
    print(f"      Missing: {', '.join(missing) or 'None'}\n")

    # Step 6: Database
    print("[6/6] Database setup...")
    if init_db and keys.get("database"):
        db_success = await initialize_database()
        result.add_step("database", db_success)
        print(f"      {'Initialized' if db_success else 'FAILED'}\n")
    elif keys.get("database"):
        db_success = await check_database_connection()
        result.add_step("database", db_success, "Connection test only")
        print(f"      {'Connected' if db_success else 'FAILED'}\n")
    else:
        result.add_step("database", False, "Not configured")
        result.warnings.append("Database not configured")
        print("      SKIPPED (no DATABASE_URL)\n")

    # Summary
    print("="*60)
    if result.success:
        print("  Setup completed successfully!")
    else:
        print("  Setup completed with issues.")

    if result.warnings:
        print("\n  Warnings:")
        for w in result.warnings:
            print(f"    - {w}")

    print("\n  Next steps:")
    print(f"    1. Configure API keys in: {SECRETS_PATH / '.env'}")
    print(f"    2. Customize workspace files in: {WORKSPACE_PATH}")
    if not keys.get("database"):
        print("    3. Set DATABASE_URL and run: hyperclaw setup --init-db")
    print("    4. Start the server: hyperclaw server")
    print("="*60 + "\n")

    return result


def setup_sync(init_db: bool = False, overwrite: bool = False, verbose: bool = False) -> SetupResult:
    """Synchronous wrapper for setup."""
    return asyncio.run(run_setup(init_db, overwrite, verbose))


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def main():
    """CLI entry point for setup."""
    import argparse

    parser = argparse.ArgumentParser(description="HyperClaw Setup")
    parser.add_argument("--init-db", action="store_true", help="Initialize database schema")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    result = setup_sync(
        init_db=args.init_db,
        overwrite=args.overwrite,
        verbose=args.verbose
    )

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
