# 🤖 Advanced Telegram Quiz Bot

A production-ready, section-aware Telegram Quiz Bot supporting normal groups, supergroups, and forum/topic groups — with leaderboards, hints, explanations, and per-section parallel quizzes.

---

## ✨ Features

| Feature | Details |
|---|---|
| 🗂 Section/Topic Quizzes | Independent quizzes per forum topic |
| ⏱ Per-Question Timer | Configurable (default 20s), auto-advances |
| 🧠 Hints & Explanations | Shown after each question result |
| 📊 Live Stats | Correct/wrong count after each question |
| 🏆 Leaderboard | Top 15 by score + accuracy tiebreaker |
| 🔐 Admin-Only Controls | startquiz/stopquiz require group admin |
| 🔁 Duplicate Prevention | One answer per user per question |
| 🛡 Anti-Spam | Button cooldown per user |
| 💾 Persistent DB | SQLite (swap to PostgreSQL easily) |
| ♻️ Restart-Safe | Sessions survive bot restarts via DB |
| 📦 Parallel Quizzes | Multiple topics run independently |

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/yourname/quiz-bot.git
cd quiz-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and set your BOT_TOKEN
```

### 3. Run

```bash
python main.py
```

---

## ⚙️ Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | ✅ Yes | — | Your BotFather token |
| `SUPER_ADMINS` | No | `""` | Comma-separated user IDs for DM access |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///quiz_bot.db` | DB connection string |
| `QUESTION_TIMER` | No | `20` | Seconds per question |
| `BETWEEN_DELAY` | No | `3` | Seconds between questions |

---

## 📋 Commands

### Group Commands (Admins Only)
| Command | Description |
|---|---|
| `/startquiz` | Start quiz in current section/topic |
| `/stopquiz` | Stop active quiz and show results |
| `/quizstats` | Show total question count |

### Private DM Commands
| Command | Description |
|---|---|
| `/addquestion` | Add a question (6-step wizard) |
| `/listquestions` | List all questions with IDs |
| `/deletequestion <id>` | Delete a question by ID |
| `/cancel` | Cancel current operation |

---

## 🗂 Project Structure

```
quiz_bot/
├── main.py                  # Entry point
├── requirements.txt
├── Procfile                 # Railway deployment
├── railway.toml
├── .env.example
├── config/
│   ├── __init__.py
│   └── settings.py          # All config via env vars
├── database/
│   ├── __init__.py
│   └── db.py                # Async SQLite (aiosqlite)
├── handlers/
│   ├── __init__.py
│   ├── admin.py             # /startquiz /stopquiz
│   ├── quiz.py              # Answer callbacks
│   ├── questions.py         # Add/list/delete questions (FSM)
│   └── user.py              # /start /help
└── utils/
    ├── __init__.py
    ├── helpers.py           # Formatting, anti-spam, helpers
    ├── logger.py            # Logging config
    ├── permissions.py       # Admin checks
    └── quiz_manager.py      # Core quiz orchestration engine
```

---

## 🚂 Deploy to Railway

1. Push to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variables in Railway dashboard:
   - `BOT_TOKEN`
   - `SUPER_ADMINS` (optional)
4. Railway auto-detects `Procfile` and deploys

> **Note:** SQLite DB is ephemeral on Railway's free tier. For persistence, set `DATABASE_URL` to a PostgreSQL connection string (Railway provides free Postgres add-ons).

---

## 🐘 Using PostgreSQL (Optional)

Install the async PostgreSQL driver:
```bash
pip install asyncpg sqlalchemy[asyncio]
```

Set `DATABASE_URL`:
```
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname
```

Then update `database/db.py` to use SQLAlchemy async engine instead of aiosqlite — the interface is the same.

---

## 🎮 How It Works

### Adding Questions (Admin DM)
1. Open private chat with bot
2. Send `/addquestion`
3. Follow the 6-step wizard:
   - Question text
   - Options (one per line)
   - Correct answer (A/B/C/D)
   - Hint (optional)
   - Explanation (optional)
   - Category (optional)

### Running a Quiz (Group)
1. Admin sends `/startquiz` in a group or forum topic
2. Bot shuffles all questions and sends them one by one
3. Members tap inline buttons to answer
4. After timer: correct answer + stats shown
5. After all questions: summary + leaderboard

### Forum Topic Support
- Each topic runs its own independent quiz
- Use `/startquiz` inside a topic — it runs only there
- Multiple topics can run quizzes simultaneously

---

## 📊 Scoring System

| Event | Points |
|---|---|
| Correct answer | +10 pts |
| Wrong answer | 0 pts |

Leaderboard tiebreaker: accuracy percentage.

---

## 🛡 Security

- Admin commands require Telegram group admin role (or `SUPER_ADMINS` env var)
- Duplicate answers blocked at DB level (UNIQUE constraint)
- Anti-spam button cooldown (0.5s per user)
- All errors caught and logged — no crashes

---

## 📄 License

MIT License — free to use, modify, and deploy.
