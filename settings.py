"""
Configuration & Settings
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Settings:
    # Required
    BOT_TOKEN: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    DATABASE_URL: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///quiz_bot.db"
    ))

    # Quiz defaults
    QUESTION_TIMER: int = int(os.getenv("QUESTION_TIMER", "20"))      # seconds
    BETWEEN_QUESTIONS_DELAY: float = float(os.getenv("BETWEEN_DELAY", "3"))  # seconds
    MAX_OPTIONS: int = 4
    TOP_LEADERBOARD: int = 15

    # Anti-spam
    ANSWER_COOLDOWN: float = 0.5   # min seconds between button presses per user

    # Admin (optional: comma-separated user IDs)
    SUPER_ADMINS: list = field(default_factory=lambda: [
        int(x) for x in os.getenv("SUPER_ADMINS", "").split(",") if x.strip().isdigit()
    ])


settings = Settings()

if not settings.BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")
