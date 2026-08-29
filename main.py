#!/usr/bin/env python3
"""
Advanced Telegram Quiz Bot - Complete Edition
Features:
  - Quiz card after creation (like QuizBot screenshot: title, Qs, timer, shuffle + action buttons)
  - "Start quiz in group" → instructions + deep-link
  - Forum/topic group support — run /startquiz INSIDE a topic → quiz starts in that section only
  - Timer choices: 15s, 30s, 1 min, 2 min, 5 min
  - Shuffle toggle (questions + stored per quiz)
  - Multiple quiz sets — no "which set?" prompt confusion in group
  - Photo questions — image shown before poll
  - Forward Telegram quiz polls (auto-parsed)
  - Manual question creation
  - Live "recording answers" indicator
  - First-answer-wins, duplicate-tap protection
  - Per-question stats after each question closes
  - Top-15 final leaderboard with medals

RAILWAY FIX:
  - DB_PATH now always resolves to a plain SQLite file path.
    Set DATABASE_URL=sqlite:///quiz_bot.db  (or just leave it blank).
    For persistence on Railway, mount a volume at /data and set:
    DATABASE_URL=sqlite:////data/quiz_bot.db
  - Never tries to parse a Postgres URL as a file path.
"""

import asyncio, json, logging, os, sys, time, random
from typing import Optional, Dict, List, Tuple

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, PollAnswer,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", stream=sys.stdout)
logging.getLogger("aiogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
DELAY        = float(os.getenv("BETWEEN_DELAY", "3"))
SUPER_ADMINS = [int(x) for x in os.getenv("SUPER_ADMINS", "").split(",") if x.strip().isdigit()]

# ── RAILWAY FIX: resolve DB path safely ──────────────────────────────────────
def _resolve_db_path() -> str:
    explicit = os.getenv("SQLITE_PATH", "").strip()
    if explicit:
        return explicit

    db_url = os.getenv("DATABASE_URL", "").strip()

    if db_url.startswith("sqlite:////"):
        return db_url[len("sqlite:////") - 1:]
    if db_url.startswith("sqlite+aiosqlite:////"):
        return db_url[len("sqlite+aiosqlite:////") - 1:]
    if db_url.startswith("sqlite:///"):
        return db_url[len("sqlite:///"):]
    if db_url.startswith("sqlite+aiosqlite:///"):
        return db_url[len("sqlite+aiosqlite:///"):]

    if db_url and not db_url.startswith("sqlite"):
        logger.warning(
            "DATABASE_URL looks like a non-SQLite URL (%s…). "
            "Ignoring it and using ./quiz_bot.db instead. "
            "Set SQLITE_PATH=/data/quiz_bot.db for a persistent volume on Railway.",
            db_url[:30]
        )

    return "quiz_bot.db"

DB_PATH = _resolve_db_path()
logger.info(f"SQLite path: {DB_PATH}")
# ─────────────────────────────────────────────────────────────────────────────

# ── Timer options (displayed label, seconds) ──
TIMER_OPTIONS = [
    ("⚡ 15s",    15),
    ("30s",       30),
    ("⏱ 1 min",  60),
    ("2 min",    120),
    ("🐢 5 min", 300),
]
DEFAULT_TIMER = 30

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")

BOT_USERNAME = ""   # filled at startup

# ── Spam guard ──
_spam: Dict[int, float] = {}
def is_spam(uid):
    now = time.monotonic()
    if now - _spam.get(uid, 0) < 0.5: return True
    _spam[uid] = now; return False

# ── Thread/topic helpers ──
def thread_id(msg: Message):
    return msg.message_thread_id if msg.is_topic_message else None

def thread_id_cb(cb: CallbackQuery):
    return cb.message.message_thread_id if cb.message and cb.message.is_topic_message else None

# ── Formatting ──
def acc(c, t): return round(c * 100 / t, 1) if t else 0.0
RANK = {1:"🥇", 2:"🥈", 3:"🥉"}
def remoji(r): return RANK.get(r, f"{r}.")

def timer_label(s: int) -> str:
    if s < 60: return f"{s}s"
    return f"{s//60} min" if s % 60 == 0 else f"{s//60}m {s%60}s"

def leaderboard_text(rows):
    if not rows: return "📊 <b>No participants scored.</b>"
    lines = ["🏆 <b>FINAL LEADERBOARD — TOP 15</b>\n"]
    for i, r in enumerate(rows, 1):
        name = f"@{r['username']}" if r['username'] else r['first_name'] or "User"
        t = r['correct'] + r['wrong']
        lines.append(
            f"{remoji(i)} <b>{name}</b>\n"
            f"   🎯 Score: <b>{r['score']}</b> pts  │  ✅ {r['correct']}  ❌ {r['wrong']}  │  📈 {acc(r['correct'],t)}%"
        )
    return "\n".join(lines)

def make_quiz_card_text(quiz: dict, cnt: int) -> str:
    tl   = timer_label(quiz.get("timer", DEFAULT_TIMER))
    shuf = "shuffle" if quiz.get("shuffle") else "no shuffle"
    desc = quiz.get("description") or "Nobody answered"
    return (
        f"👍 <b>Quiz created.</b>\n\n"
        f"<b>{quiz['title']}</b>   <i>{desc}</i>\n"
        f"✏️ {cnt} question{'s' if cnt != 1 else ''}  ·  "
        f"🕐 {tl}  ·  "
        f"{'🔀' if quiz.get('shuffle') else '↕️'} {shuf}"
    )

def make_quiz_card(quiz: dict, cnt: int) -> InlineKeyboardMarkup:
    qid = quiz["id"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Start this quiz",     callback_data=f"card:start:{qid}")],
        [InlineKeyboardButton(text="🏘 Start quiz in group",  callback_data=f"card:ingroup:{qid}")],
        [InlineKeyboardButton(text="🔗 Get share link",       callback_data=f"card:share:{qid}")],
        [InlineKeyboardButton(text="✏️ Edit quiz",            callback_data=f"card:edit:{qid}")],
        [InlineKeyboardButton(text="📊 Quiz stats",           callback_data=f"card:stats:{qid}")],
    ])

def _timer_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for label, secs in TIMER_OPTIONS:
        row.append(InlineKeyboardButton(text=label, callback_data=f"{prefix}:{secs}"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════
class DB:
    def __init__(self): self.c = None

    async def init(self):
        d = os.path.dirname(os.path.abspath(DB_PATH))
        if d and not os.path.exists(d):
            try:
                os.makedirs(d, exist_ok=True)
                logger.info(f"Created DB directory: {d}")
            except OSError as e:
                logger.error(f"Cannot create DB directory {d}: {e}")
                raise

        self.c = await aiosqlite.connect(DB_PATH)
        self.c.row_factory = aiosqlite.Row
        await self.c.execute("PRAGMA journal_mode=WAL")
        await self.c.executescript("""
        CREATE TABLE IF NOT EXISTS quizzes(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id    INTEGER NOT NULL,
            title       TEXT    NOT NULL,
            description TEXT,
            timer       INTEGER DEFAULT 30,
            shuffle     INTEGER DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS questions(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id     INTEGER NOT NULL,
            question    TEXT    NOT NULL,
            options     TEXT    NOT NULL,
            answer_idx  INTEGER NOT NULL,
            explanation TEXT,
            photo_id    TEXT,
            position    INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions(
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id      INTEGER NOT NULL,
            chat_id      INTEGER NOT NULL,
            thread_id    INTEGER,
            started_by   INTEGER NOT NULL,
            status       TEXT    DEFAULT 'running',
            question_ids TEXT    NOT NULL,
            current_idx  INTEGER DEFAULT 0,
            timer        INTEGER DEFAULT 30,
            started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            ended_at     DATETIME
        );
        CREATE TABLE IF NOT EXISTS poll_map(
            poll_id     TEXT PRIMARY KEY,
            session_id  INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            chat_id     INTEGER NOT NULL,
            thread_id   INTEGER
        );
        CREATE TABLE IF NOT EXISTS answers(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT,
            first_name  TEXT,
            chosen_idx  INTEGER NOT NULL,
            is_correct  INTEGER NOT NULL,
            UNIQUE(session_id, question_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS stats(
            session_id  INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT,
            first_name  TEXT,
            correct     INTEGER DEFAULT 0,
            wrong       INTEGER DEFAULT 0,
            score       INTEGER DEFAULT 0,
            PRIMARY KEY(session_id, user_id)
        );
        """)
        await self.c.commit()
        logger.info(f"DB ready: {DB_PATH}")

    async def close(self):
        if self.c: await self.c.close()

    # ── Quizzes ──
    async def create_quiz(self, owner_id, title, desc=None, timer=DEFAULT_TIMER, shuffle=0):
        cur = await self.c.execute(
            "INSERT INTO quizzes(owner_id,title,description,timer,shuffle) VALUES(?,?,?,?,?)",
            (owner_id, title, desc, timer, shuffle)
        )
        await self.c.commit(); return cur.lastrowid

    async def get_quiz(self, qid):
        cur = await self.c.execute("SELECT * FROM quizzes WHERE id=?", (qid,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_quizzes(self, uid):
        cur = await self.c.execute("SELECT * FROM quizzes WHERE owner_id=? ORDER BY id DESC", (uid,))
        return [dict(r) for r in await cur.fetchall()]

    async def delete_quiz(self, qid, uid):
        cur = await self.c.execute("DELETE FROM quizzes WHERE id=? AND owner_id=?", (qid, uid))
        await self.c.commit(); return cur.rowcount > 0

    async def update_quiz_timer(self, qid, timer):
        await self.c.execute("UPDATE quizzes SET timer=? WHERE id=?", (timer, qid))
        await self.c.commit()

    async def update_quiz_shuffle(self, qid, shuffle):
        await self.c.execute("UPDATE quizzes SET shuffle=? WHERE id=?", (shuffle, qid))
        await self.c.commit()

    # ── Questions ──
    async def add_question(self, quiz_id, question, options, answer_idx, explanation=None, photo_id=None):
        cur = await self.c.execute("SELECT COUNT(*) FROM questions WHERE quiz_id=?", (quiz_id,))
        pos = (await cur.fetchone())[0]
        cur = await self.c.execute(
            "INSERT INTO questions(quiz_id,question,options,answer_idx,explanation,photo_id,position)"
            " VALUES(?,?,?,?,?,?,?)",
            (quiz_id, question, json.dumps(options), answer_idx, explanation, photo_id, pos)
        )
        await self.c.commit(); return cur.lastrowid

    async def get_questions(self, quiz_id):
        cur = await self.c.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY position", (quiz_id,))
        rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r); d["options"] = json.loads(d["options"]); result.append(d)
        return result

    async def get_question(self, qid):
        cur = await self.c.execute("SELECT * FROM questions WHERE id=?", (qid,))
        row = await cur.fetchone()
        if not row: return None
        d = dict(row); d["options"] = json.loads(d["options"]); return d

    async def count_questions(self, quiz_id):
        cur = await self.c.execute("SELECT COUNT(*) FROM questions WHERE quiz_id=?", (quiz_id,))
        return (await cur.fetchone())[0]

    # ── Sessions ──
    async def create_session(self, quiz_id, chat_id, tid, started_by, qids, timer):
        await self.c.execute(
            "UPDATE sessions SET status='stopped' WHERE chat_id=? AND thread_id IS ? AND status='running'",
            (chat_id, tid)
        )
        cur = await self.c.execute(
            "INSERT INTO sessions(quiz_id,chat_id,thread_id,started_by,question_ids,timer)"
            " VALUES(?,?,?,?,?,?)",
            (quiz_id, chat_id, tid, started_by, json.dumps(qids), timer)
        )
        await self.c.commit(); return cur.lastrowid

    async def get_active_session(self, chat_id, tid):
        cur = await self.c.execute(
            "SELECT * FROM sessions WHERE chat_id=? AND thread_id IS ? AND status='running'",
            (chat_id, tid)
        )
        row = await cur.fetchone()
        if not row: return None
        d = dict(row); d["question_ids"] = json.loads(d["question_ids"]); return d

    async def get_session(self, sid):
        cur = await self.c.execute("SELECT * FROM sessions WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row: return None
        d = dict(row); d["question_ids"] = json.loads(d["question_ids"]); return d

    async def end_session(self, sid, status="finished"):
        await self.c.execute(
            "UPDATE sessions SET status=?,ended_at=CURRENT_TIMESTAMP WHERE id=?", (status, sid)
        )
        await self.c.commit()

    # ── Poll map ──
    async def save_poll(self, poll_id, session_id, question_id, chat_id, tid):
        await self.c.execute(
            "INSERT OR REPLACE INTO poll_map(poll_id,session_id,question_id,chat_id,thread_id)"
            " VALUES(?,?,?,?,?)",
            (poll_id, session_id, question_id, chat_id, tid)
        )
        await self.c.commit()

    async def get_poll(self, poll_id):
        cur = await self.c.execute("SELECT * FROM poll_map WHERE poll_id=?", (poll_id,))
        row = await cur.fetchone(); return dict(row) if row else None

    # ── Answers & Stats ──
    async def record_answer(self, session_id, question_id, user_id, username, first_name, chosen_idx, is_correct):
        try:
            await self.c.execute(
                "INSERT INTO answers(session_id,question_id,user_id,username,first_name,chosen_idx,is_correct)"
                " VALUES(?,?,?,?,?,?,?)",
                (session_id, question_id, user_id, username, first_name, chosen_idx, int(is_correct))
            )
            if is_correct:
                await self.c.execute(
                    "INSERT INTO stats(session_id,user_id,username,first_name,correct,score)"
                    " VALUES(?,?,?,?,1,10)"
                    " ON CONFLICT(session_id,user_id) DO UPDATE SET"
                    " correct=correct+1, score=score+10,"
                    " username=excluded.username, first_name=excluded.first_name",
                    (session_id, user_id, username, first_name)
                )
            else:
                await self.c.execute(
                    "INSERT INTO stats(session_id,user_id,username,first_name,wrong)"
                    " VALUES(?,?,?,?,1)"
                    " ON CONFLICT(session_id,user_id) DO UPDATE SET"
                    " wrong=wrong+1, username=excluded.username, first_name=excluded.first_name",
                    (session_id, user_id, username, first_name)
                )
            await self.c.commit(); return True
        except aiosqlite.IntegrityError:
            return False

    async def get_q_stats(self, sid, qid):
        cur = await self.c.execute(
            "SELECT SUM(is_correct) as correct, SUM(1-is_correct) as wrong"
            " FROM answers WHERE session_id=? AND question_id=?", (sid, qid)
        )
        row = await cur.fetchone()
        return {"correct": row[0] or 0, "wrong": row[1] or 0}

    async def get_leaderboard(self, sid, limit=15):
        cur = await self.c.execute(
            "SELECT user_id,username,first_name,correct,wrong,score,"
            " CASE WHEN (correct+wrong)>0 THEN ROUND(correct*100.0/(correct+wrong),1) ELSE 0 END as accuracy"
            " FROM stats WHERE session_id=? ORDER BY score DESC, accuracy DESC LIMIT ?",
            (sid, limit)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_summary(self, sid):
        cur = await self.c.execute(
            "SELECT COUNT(DISTINCT user_id) as p, SUM(correct) as tc, SUM(wrong) as tw"
            " FROM stats WHERE session_id=?", (sid,)
        )
        row = await cur.fetchone()
        return {"participants": row[0] or 0, "total_correct": row[1] or 0, "total_wrong": row[2] or 0}


# ══════════════════════════════════════════════════════════════
#  QUIZ MANAGER
# ══════════════════════════════════════════════════════════════
SessionKey = Tuple[int, Optional[int]]

class QuizManager:
    def __init__(self, bot, db):
        self.bot = bot; self.db = db
        self._tasks: Dict[SessionKey, asyncio.Task] = {}

    def key(self, c, t): return (c, t)
    def is_active(self, c, t):
        k = self.key(c, t); t_ = self._tasks.get(k)
        return t_ is not None and not t_.done()

    async def start(self, quiz_id, chat_id, tid, started_by, qids, timer):
        k = self.key(chat_id, tid)
        if k in self._tasks and not self._tasks[k].done(): self._tasks[k].cancel()
        sid = await self.db.create_session(quiz_id, chat_id, tid, started_by, qids, timer)
        self._tasks[k] = asyncio.create_task(self._run(k, sid, qids, timer))

    async def stop(self, chat_id, tid):
        k = self.key(chat_id, tid)
        session = await self.db.get_active_session(chat_id, tid)
        if not session: return False
        if k in self._tasks and not self._tasks[k].done(): self._tasks[k].cancel()
        await self.db.end_session(session["id"], "stopped")
        await self._send_final(chat_id, tid, session["id"], session["question_ids"])
        self._cleanup(k); return True

    async def handle_poll_answer(self, pa: PollAnswer):
        info = await self.db.get_poll(pa.poll_id)
        if not info or not pa.option_ids: return
        session = await self.db.get_session(info["session_id"])
        if not session or session["status"] != "running": return
        q = await self.db.get_question(info["question_id"])
        if not q: return
        u = pa.user; chosen = pa.option_ids[0]
        await self.db.record_answer(
            info["session_id"], info["question_id"],
            u.id, u.username or "", u.first_name or "User",
            chosen, chosen == q["answer_idx"]
        )

    async def _send(self, chat_id, tid, **kw):
        if tid: kw["message_thread_id"] = tid
        return await self.bot.send_message(chat_id=chat_id, **kw)

    async def _run(self, k: SessionKey, sid: int, qids: List[int], timer: int):
        chat_id, tid = k; total = len(qids)
        try:
            for idx, qid in enumerate(qids, 1):
                s = await self.db.get_session(sid)
                if not s or s["status"] != "running": break
                q = await self.db.get_question(qid)
                if not q: continue

                # 1. Header
                try:
                    await self._send(chat_id, tid,
                        text=f"❓ <b>Question {idx}/{total}</b>  │  ⏱ <b>{timer_label(timer)}</b>")
                except Exception as e: logger.error(f"Header err: {e}")

                # 2. Photo
                if q.get("photo_id"):
                    try:
                        pkw = dict(chat_id=chat_id, photo=q["photo_id"],
                                   caption="🖼 <b>Look at this carefully before answering!</b>")
                        if tid: pkw["message_thread_id"] = tid
                        await self.bot.send_photo(**pkw)
                        await asyncio.sleep(1.5)
                    except Exception as e: logger.error(f"Photo err: {e}")

                # 3. Poll
                pkw = dict(
                    chat_id=chat_id, question=q["question"][:300],
                    options=q["options"], type="quiz",
                    correct_option_id=q["answer_idx"], is_anonymous=False,
                    open_period=min(timer, 600), protect_content=False
                )
                if q.get("explanation"): pkw["explanation"] = q["explanation"][:200]
                if tid: pkw["message_thread_id"] = tid
                try:
                    pm = await self.bot.send_poll(**pkw)
                    await self.db.save_poll(pm.poll.id, sid, qid, chat_id, tid)
                except Exception as e:
                    logger.error(f"Poll err: {e}"); continue

                # 4. Recording indicator
                dot_mid = None
                try:
                    dm = await self._send(chat_id, tid,
                        text=f"🔴 <b>Recording answers…</b>  ⏳ <b>{timer_label(timer)}</b>\n"
                             f"<i>Only your first tap counts!</i>")
                    dot_mid = dm.message_id
                except Exception as e: logger.error(f"Dot err: {e}")

                # 5. Wait
                await asyncio.sleep(timer + 1)

                # 6. Close dot
                if dot_mid:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id, message_id=dot_mid,
                            text="⚫ <b>Poll closed.</b> Tallying…")
                    except Exception: pass

                # 7. Per-question stats
                try:
                    st  = await self.db.get_q_stats(sid, qid)
                    tot = st["correct"] + st["wrong"]
                    await self._send(chat_id, tid,
                        text=(f"📊 <b>Q{idx} Results</b>\n\n"
                              f"✅ Correct : <b>{st['correct']}</b>\n"
                              f"❌ Wrong   : <b>{st['wrong']}</b>\n"
                              f"👥 Total   : <b>{tot}</b>\n"
                              f"🎯 Accuracy: <b>{acc(st['correct'],tot)}%</b>"))
                except Exception as e: logger.error(f"Stats err: {e}")

                if idx < total: await asyncio.sleep(DELAY)

            s = await self.db.get_session(sid)
            if s and s["status"] == "running":
                await self.db.end_session(sid, "finished")
                await self._send_final(chat_id, tid, sid, qids)
        except asyncio.CancelledError:
            logger.info(f"Quiz cancelled: {k}")
        except Exception as e:
            logger.exception(f"Quiz loop err: {e}")
            try: await self._send(chat_id, tid, text="⚠️ An error occurred. Quiz stopped.")
            except: pass
        finally:
            self._cleanup(k)

    async def _send_final(self, chat_id, tid, sid, qids):
        try:
            sm = await self.db.get_summary(sid)
            lb = await self.db.get_leaderboard(sid, 15)
            tc, tw = sm["total_correct"], sm["total_wrong"]
            await self._send(chat_id, tid,
                text=(f"🎉 <b>QUIZ FINISHED!</b>\n\n"
                      f"📋 Questions    : <b>{len(qids)}</b>\n"
                      f"👥 Participants : <b>{sm['participants']}</b>\n"
                      f"✅ Correct      : <b>{tc}</b>   ❌ Wrong: <b>{tw}</b>\n"
                      f"🎯 Overall Acc  : <b>{acc(tc, tc+tw)}%</b>"))
            await asyncio.sleep(1)
            await self._send(chat_id, tid, text=leaderboard_text(lb))
        except Exception as e: logger.error(f"Final err: {e}")

    def _cleanup(self, k): self._tasks.pop(k, None)


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════
async def is_admin(bot, chat_id, user_id):
    if user_id in SUPER_ADMINS: return True
    try:
        from aiogram.types import ChatMemberAdministrator, ChatMemberOwner
        m = await bot.get_chat_member(chat_id, user_id)
        return isinstance(m, (ChatMemberAdministrator, ChatMemberOwner))
    except: return False

def dm_allowed(uid): return not SUPER_ADMINS or uid in SUPER_ADMINS


# ══════════════════════════════════════════════════════════════
#  FSM STATES
# ══════════════════════════════════════════════════════════════
class CQ(StatesGroup):
    title = State(); description = State(); timer = State(); shuffle = State()

class AQ(StatesGroup):
    method = State(); photo = State(); question = State()
    options = State(); answer = State(); explanation = State()
    awaiting_poll = State()

class EditQ(StatesGroup):
    new_timer = State()


# ══════════════════════════════════════════════════════════════
#  ROUTER & GLOBALS
# ══════════════════════════════════════════════════════════════
router = Router()
qm: Optional[QuizManager] = None
db: Optional[DB]           = None

HELP = """🤖 <b>Quiz Bot — Guide</b>

<b>📝 Create &amp; Manage (DM only):</b>
/newquiz          — Create a new quiz set
/myquizzes        — List all your quiz sets
/addq             — Add questions to a quiz set
/deletequiz &lt;id&gt; — Delete a quiz set

<b>🎮 Run Quiz (Group — admins only):</b>
/startquiz  — Start a quiz in <b>this section/topic</b>
/stopquiz   — Stop running quiz &amp; show leaderboard

<b>📌 Forum/topic groups:</b>
Go into the specific <b>section/topic</b> and type /startquiz
→ quiz runs only in that section!

<b>⏱ Timers:</b> 15s · 30s · 1 min · 2 min · 5 min
<b>🔀 Shuffle</b> questions toggle per quiz set
<b>🔴 First tap</b> counts — no changing answers
<b>🏆 Top 15</b> leaderboard at the end

/cancel — Cancel current action"""


# ══════════════════════════════════════════════════════════════
#  /start  — handles both plain start and deep-links
# ══════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    # ── Handle deep-link: ?start=doq_<quiz_id>  (solo quiz in DM via share link) ──
    text = msg.text or ""
    parts = text.split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""

    if payload.startswith("doq_") and payload[4:].isdigit():
        qid  = int(payload[4:])
        quiz = await db.get_quiz(qid)
        if not quiz:
            await msg.reply("❌ Quiz not found."); return
        cnt = await db.count_questions(qid)
        if cnt == 0:
            await msg.reply("❌ This quiz has no questions yet."); return
        qs   = await db.get_questions(qid)
        qids = [q["id"] for q in qs]
        if quiz.get("shuffle"): random.shuffle(qids)
        timer = quiz.get("timer", DEFAULT_TIMER)
        await msg.reply(
            f"🎉 <b>Starting «{quiz['title']}»</b> just for you!\n\n"
            f"❓ {cnt} question(s)  │  ⏱ {timer_label(timer)}\n\n"
            f"Get ready… 🎯"
        )
        await qm.start(qid, msg.chat.id, None, msg.from_user.id, qids, timer)
        return

    # ── Normal /start ──
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Create New Quiz Set", callback_data="m:new")],
        [InlineKeyboardButton(text="📋 My Quiz Sets",        callback_data="m:list")],
        [InlineKeyboardButton(text="➕ Add Questions",        callback_data="m:addq")],
        [InlineKeyboardButton(text="❓ Help",                  callback_data="m:help")],
    ])
    await msg.reply(
        f"👋 Hello <b>{msg.from_user.first_name}</b>!\n\n"
        "I run interactive quiz polls in Telegram groups.\n\n"
        "✅ Create multiple quiz sets\n"
        "✅ Start in any group <b>section/topic</b>\n"
        "✅ Top-15 leaderboard after every quiz",
        reply_markup=kb
    )

@router.callback_query(F.data == "m:new")
async def m_new(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.message.chat.type != "private":
        await cb.message.reply("📩 Please use /newquiz in my DM."); return
    await _newquiz(cb.message, state, cb.from_user.id)

@router.callback_query(F.data == "m:list")
async def m_list(cb: CallbackQuery):
    await cb.answer(); await _myquizzes(cb.message, cb.from_user.id)

@router.callback_query(F.data == "m:addq")
async def m_addq_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    if cb.message.chat.type != "private":
        await cb.message.reply("📩 Please use /addq in my DM."); return
    await _start_addq(cb.message, state, cb.from_user.id)

@router.callback_query(F.data == "m:help")
async def m_help(cb: CallbackQuery):
    await cb.answer(); await cb.message.reply(HELP)

@router.message(Command("help"))
async def cmd_help(msg: Message): await msg.reply(HELP)

@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
        await msg.reply("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    else:
        await msg.reply("Nothing to cancel.")


# ══════════════════════════════════════════════════════════════
#  QUIZ CARD BUTTONS
# ══════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("card:start:"))
async def card_start(cb: CallbackQuery):
    await cb.answer()
    qid  = int(cb.data.split(":")[2])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    cnt = await db.count_questions(qid)
    if cnt == 0:
        await cb.message.reply("❌ No questions yet. Add some with /addq"); return
    qs   = await db.get_questions(qid)
    qids = [q["id"] for q in qs]
    if quiz.get("shuffle"): random.shuffle(qids)
    timer = quiz.get("timer", DEFAULT_TIMER)
    await cb.message.reply(
        f"🚀 <b>Starting quiz here (solo mode)!</b>\n"
        f"📋 <b>{quiz['title']}</b>  │  ❓ {cnt} Qs  │  ⏱ {timer_label(timer)}"
    )
    await qm.start(qid, cb.message.chat.id, None, cb.from_user.id, qids, timer)


# ── FIX 1: "Start in group" now gives a direct deep-link that opens
#    a group-picker in Telegram, then the admin runs /startquiz there ──
@router.callback_query(F.data.startswith("card:ingroup:"))
async def card_ingroup(cb: CallbackQuery):
    await cb.answer()
    qid  = int(cb.data.split(":")[2])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    cnt = await db.count_questions(qid)
    if cnt == 0:
        await cb.message.reply("❌ No questions yet. Add some with /addq"); return

    # startgroup= payload is passed to /start in the group context.
    # We encode the quiz id so when the bot is added it can show a hint,
    # but the actual quiz launch still requires /startquiz from the admin.
    start_in_group_link = f"https://t.me/{BOT_USERNAME}?startgroup=sq_{qid}"
    add_link            = f"https://t.me/{BOT_USERNAME}?startgroup=start"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Choose group & start this quiz", url=start_in_group_link)],
        [InlineKeyboardButton(text="➕ Add bot to a new group",          url=add_link)],
    ])
    await cb.message.reply(
        f"🏘 <b>Start «{quiz['title']}» in a group</b>\n\n"
        f"1️⃣ Tap <b>«Choose group & start this quiz»</b> below\n"
        f"   → Telegram opens a group-picker\n"
        f"2️⃣ Select the group you want\n"
        f"3️⃣ As a group admin, type <code>/startquiz</code>\n"
        f"   ↳ <b>«{quiz['title']}»</b> will be at the top of the list\n\n"
        f"<b>Forum/topic groups:</b> go <b>inside the specific section</b> first,\n"
        f"then /startquiz — quiz runs <b>only in that section!</b>",
        reply_markup=kb,
        disable_web_page_preview=True
    )


@router.callback_query(F.data.startswith("card:stats:"))
async def card_stats(cb: CallbackQuery):
    await cb.answer()
    qid  = int(cb.data.split(":")[2])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    cnt = await db.count_questions(qid)
    await cb.message.reply(
        f"📊 <b>Stats — «{quiz['title']}»</b>\n\n"
        f"❓ Questions : <b>{cnt}</b>\n"
        f"⏱ Timer     : <b>{timer_label(quiz.get('timer', DEFAULT_TIMER))}</b>\n"
        f"🔀 Shuffle   : <b>{'Yes' if quiz.get('shuffle') else 'No'}</b>\n"
        f"🆔 ID        : <b>#{qid}</b>"
    )


@router.callback_query(F.data.startswith("card:share:"))
async def card_share(cb: CallbackQuery):
    await cb.answer()
    qid  = int(cb.data.split(":")[2])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    cnt = await db.count_questions(qid)
    if cnt == 0:
        await cb.message.reply("❌ No questions yet. Add some with /addq"); return
    share_link = f"https://t.me/{BOT_USERNAME}?start=doq_{qid}"
    title = quiz["title"]
    await cb.message.reply(
        "🔗 <b>Share link for «" + title + "»</b>\n\n"
        "<code>" + share_link + "</code>\n\n"
        "Anyone who taps this link will start the quiz solo in their own DM.\n"
        "✅ Copy the link above and share it anywhere!"
    )


@router.callback_query(F.data.startswith("card:edit:"))
async def card_edit(cb: CallbackQuery):
    await cb.answer()
    qid  = int(cb.data.split(":")[2])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    shuf_label = "🔀 Turn Shuffle OFF" if quiz.get("shuffle") else "↕️ Turn Shuffle ON"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Change Timer",  callback_data=f"edit:timer:{qid}")],
        [InlineKeyboardButton(text=shuf_label,         callback_data=f"edit:shuffle:{qid}")],
        [InlineKeyboardButton(text="➕ Add Questions",  callback_data=f"addq:{qid}")],
    ])
    await cb.message.reply(f"✏️ <b>Edit «{quiz['title']}»</b>\n\nWhat do you want to change?", reply_markup=kb)


@router.callback_query(F.data.startswith("edit:timer:"))
async def edit_timer(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    qid = int(cb.data.split(":")[2])
    await state.set_state(EditQ.new_timer)
    await state.update_data(edit_quiz_id=qid)
    await cb.message.reply("⏱ <b>Pick new timer:</b>", reply_markup=_timer_keyboard("edittimer"))

@router.callback_query(F.data.startswith("edittimer:"), EditQ.new_timer)
async def edit_timer_pick(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    t = int(cb.data.split(":")[1])
    data = await state.get_data(); qid = data.get("edit_quiz_id")
    await state.clear()
    if not qid: await cb.message.reply("❌ Session expired."); return
    await db.update_quiz_timer(qid, t)
    quiz = await db.get_quiz(qid); cnt = await db.count_questions(qid)
    await cb.message.reply(
        f"✅ Timer updated to <b>{timer_label(t)}</b>!\n\n" + make_quiz_card_text(quiz, cnt),
        reply_markup=make_quiz_card(quiz, cnt)
    )

@router.callback_query(F.data.startswith("edit:shuffle:"))
async def edit_shuffle(cb: CallbackQuery):
    await cb.answer()
    qid = int(cb.data.split(":")[2])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    new_shuf = 0 if quiz.get("shuffle") else 1
    await db.update_quiz_shuffle(qid, new_shuf)
    quiz = await db.get_quiz(qid); cnt = await db.count_questions(qid)
    label = "ON 🔀" if new_shuf else "OFF ↕️"
    await cb.message.reply(
        f"✅ Shuffle <b>{label}</b>!\n\n" + make_quiz_card_text(quiz, cnt),
        reply_markup=make_quiz_card(quiz, cnt)
    )

@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery): await cb.answer()


# ══════════════════════════════════════════════════════════════
#  CREATE QUIZ
# ══════════════════════════════════════════════════════════════
async def _newquiz(msg, state, uid):
    if not dm_allowed(uid): await msg.reply("⛔ Not authorized."); return
    await state.clear(); await state.set_state(CQ.title)
    await msg.reply(
        "🆕 <b>Create New Quiz Set</b>\n\n"
        "<b>Step 1/4</b> — Send the <b>title</b>:\n"
        "<i>e.g. Science Quiz, GK Round 1…</i>\n\n/cancel to abort."
    )

@router.message(Command("newquiz"))
async def cmd_newquiz(msg: Message, state: FSMContext):
    if msg.chat.type != "private": await msg.reply("📩 Use /newquiz in my DM."); return
    await _newquiz(msg, state, msg.from_user.id)

@router.message(CQ.title)
async def cq_title(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    t = msg.text.strip()
    if len(t) < 2: await msg.reply("❌ Title too short."); return
    await state.update_data(title=t); await state.set_state(CQ.description)
    await msg.reply(f"✅ Title: <b>{t}</b>\n\n<b>Step 2/4</b> — Send a description, or /skip:")

@router.message(Command("skip"), CQ.description)
async def cq_skip_desc(msg: Message, state: FSMContext):
    await state.update_data(description=None); await _ask_timer(msg, state)

@router.message(CQ.description)
async def cq_desc(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    await state.update_data(description=msg.text.strip()); await _ask_timer(msg, state)

async def _ask_timer(msg, state):
    await state.set_state(CQ.timer)
    await msg.reply("<b>Step 3/4</b> — Choose <b>timer per question</b>:", reply_markup=_timer_keyboard("cqtimer"))

@router.callback_query(F.data.startswith("cqtimer:"), CQ.timer)
async def cq_timer_btn(cb: CallbackQuery, state: FSMContext):
    await cb.answer(); t = int(cb.data.split(":")[1])
    await state.update_data(timer=t); await state.set_state(CQ.shuffle)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔀 Yes, shuffle",  callback_data="cqshuf:1")],
        [InlineKeyboardButton(text="↕️ No shuffle",    callback_data="cqshuf:0")],
    ])
    await cb.message.reply(
        f"✅ Timer: <b>{timer_label(t)}</b>\n\n<b>Step 4/4</b> — Shuffle questions &amp; options?",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("cqshuf:"), CQ.shuffle)
async def cq_shuffle_btn(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(shuffle=int(cb.data.split(":")[1]))
    await _finish_create_quiz(cb.message, state, cb.from_user.id)

async def _finish_create_quiz(msg, state, uid):
    data = await state.get_data(); await state.clear()
    qid  = await db.create_quiz(uid, data["title"], data.get("description"),
                                data.get("timer", DEFAULT_TIMER), data.get("shuffle", 0))
    quiz = await db.get_quiz(qid); cnt = 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Questions Now", callback_data=f"addq:{qid}")],
        [InlineKeyboardButton(text="📋 My Quiz Sets",       callback_data="m:list")],
    ])
    await msg.reply(make_quiz_card_text(quiz, cnt) + "\n\n<i>Add questions to get started!</i>", reply_markup=kb)


# ══════════════════════════════════════════════════════════════
#  LIST / DELETE
# ══════════════════════════════════════════════════════════════
async def _myquizzes(msg, uid):
    qs = await db.get_user_quizzes(uid)
    if not qs:
        await msg.reply("📭 No quiz sets yet.\nCreate one with /newquiz"); return
    lines = [f"📚 <b>Your Quiz Sets ({len(qs)} total):</b>\n"]
    for q in qs:
        cnt = await db.count_questions(q["id"])
        lines.append(
            f"<b>#{q['id']}</b>  {q['title']}  ⏱ {timer_label(q.get('timer', DEFAULT_TIMER))}\n"
            f"   📝 {cnt} question(s)" + (f"  │  <i>{q['description']}</i>" if q.get("description") else "")
        )
    rows = [[InlineKeyboardButton(text=f"📋 #{q['id']} {q['title']}", callback_data=f"showcard:{q['id']}")] for q in qs]
    rows.append([InlineKeyboardButton(text="🆕 New Quiz Set", callback_data="m:new")])
    await msg.reply("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.message(Command("myquizzes"))
async def cmd_myquizzes(msg: Message): await _myquizzes(msg, msg.from_user.id)

@router.message(Command("deletequiz"))
async def cmd_delquiz(msg: Message):
    if msg.chat.type != "private": await msg.reply("📩 Use in DM."); return
    parts = msg.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.reply("Usage: /deletequiz <id>\nGet IDs with /myquizzes"); return
    ok = await db.delete_quiz(int(parts[1]), msg.from_user.id)
    await msg.reply(f"🗑 Quiz #{parts[1]} deleted." if ok else f"❌ Quiz #{parts[1]} not found or not yours.")

@router.callback_query(F.data.startswith("showcard:"))
async def cb_showcard(cb: CallbackQuery):
    await cb.answer()
    qid  = int(cb.data.split(":")[1])
    quiz = await db.get_quiz(qid)
    if not quiz or quiz["owner_id"] != cb.from_user.id:
        await cb.message.reply("❌ Not found."); return
    cnt = await db.count_questions(qid)
    await cb.message.reply(make_quiz_card_text(quiz, cnt), reply_markup=make_quiz_card(quiz, cnt))


# ══════════════════════════════════════════════════════════════
#  ADD QUESTIONS
# ══════════════════════════════════════════════════════════════
async def _start_addq(msg, state, uid):
    if not dm_allowed(uid): await msg.reply("⛔ Not authorized."); return
    qs = await db.get_user_quizzes(uid)
    if not qs: await msg.reply("❌ No quiz sets yet. Create one with /newquiz"); return
    if len(qs) == 1:
        await _ask_method(msg, state, qs[0]["id"], qs[0]["title"]); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📋 #{q['id']}  {q['title']}  ⏱ {timer_label(q.get('timer', DEFAULT_TIMER))}",
            callback_data=f"addq:{q['id']}"
        )] for q in qs
    ])
    await msg.reply("📚 <b>Which quiz set?</b>", reply_markup=kb)

@router.message(Command("addq"))
async def cmd_addq(msg: Message, state: FSMContext):
    if msg.chat.type != "private": await msg.reply("📩 Use /addq in my DM."); return
    await _start_addq(msg, state, msg.from_user.id)

@router.callback_query(F.data.startswith("addq:"))
async def cb_addq(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    qid = int(cb.data.split(":")[1]); q = await db.get_quiz(qid)
    if not q: await cb.message.reply("❌ Not found."); return
    if q["owner_id"] != cb.from_user.id: await cb.message.reply("⛔ Not yours."); return
    await _ask_method(cb.message, state, qid, q["title"])

async def _ask_method(msg, state, quiz_id, quiz_title):
    await state.set_state(AQ.method)
    await state.update_data(quiz_id=quiz_id, quiz_title=quiz_title)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Manual",                    callback_data="method:manual")],
        [InlineKeyboardButton(text="📨 Forward a Telegram Quiz Poll", callback_data="method:forward")],
    ])
    await msg.reply(f"➕ <b>Adding to: {quiz_title}</b>\n\nHow?", reply_markup=kb)

@router.callback_query(F.data == "method:forward", AQ.method)
async def method_forward(cb: CallbackQuery, state: FSMContext):
    await cb.answer(); await state.set_state(AQ.awaiting_poll)
    await cb.message.reply(
        "📨 <b>Forward a Telegram Quiz Poll here.</b>\n"
        "Bot will auto-read everything.\n\n/cancel to abort."
    )

@router.message(AQ.awaiting_poll)
async def recv_forwarded_poll(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    poll = msg.poll
    if not poll:
        await msg.reply("❌ No poll found. Forward a Telegram quiz poll.\n/cancel to abort."); return
    if poll.type != "quiz":
        await msg.reply("❌ Regular poll — need a quiz-type poll.\n/cancel to abort."); return
    data = await state.get_data()
    options = [o.text for o in poll.options]
    qid = await db.add_question(data["quiz_id"], poll.question, options,
                                poll.correct_option_id, poll.explanation or None)
    cnt = await db.count_questions(data["quiz_id"]); await state.clear()
    opts_text = "\n".join(
        f"{'✅' if i == poll.correct_option_id else '▪️'} {chr(65+i)}. {o}"
        for i, o in enumerate(options)
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Forward Another", callback_data=f"addq:{data['quiz_id']}")],
        [InlineKeyboardButton(text="✅ Done",             callback_data=f"showcard:{data['quiz_id']}")],
    ])
    await msg.reply(
        f"🎉 <b>Imported #{qid}!</b>\n\n❓ {poll.question}\n\n{opts_text}\n\n"
        f"📊 Quiz now has <b>{cnt}</b> question(s).", reply_markup=kb
    )

@router.callback_query(F.data == "method:manual", AQ.method)
async def method_manual(cb: CallbackQuery, state: FSMContext):
    await cb.answer(); await state.set_state(AQ.photo)
    await cb.message.reply("🖼 <b>Step 1/5 — Photo (optional)</b>\n\nSend a photo or /skip:")

@router.message(Command("skip"), AQ.photo)
async def aq_skip_photo(msg: Message, state: FSMContext):
    await state.update_data(photo_id=None); await state.set_state(AQ.question)
    await msg.reply("✅ No photo.\n\n📝 <b>Step 2/5 — Question text:</b>")

@router.message(AQ.photo, F.photo)
async def aq_photo(msg: Message, state: FSMContext):
    await state.update_data(photo_id=msg.photo[-1].file_id); await state.set_state(AQ.question)
    await msg.reply("✅ Photo saved!\n\n📝 <b>Step 2/5 — Question text:</b>")

@router.message(AQ.photo)
async def aq_photo_wrong(msg: Message):
    if msg.text and msg.text.startswith("/"): return
    await msg.reply("❌ Send a photo or /skip.")

@router.message(AQ.question)
async def aq_q(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    t = msg.text.strip()
    if len(t) < 3: await msg.reply("❌ Too short."); return
    if len(t) > 300: await msg.reply("❌ Max 300 chars."); return
    await state.update_data(question=t); await state.set_state(AQ.options)
    await msg.reply("✅ Saved!\n\n📝 <b>Step 3/5 — Options</b>, one per line (2–4):\n\n<i>Paris\nLondon\nBerlin</i>")

@router.message(AQ.options)
async def aq_opts(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    opts = [l.strip() for l in msg.text.strip().splitlines() if l.strip()]
    if len(opts) < 2: await msg.reply("❌ Need at least 2 options."); return
    opts = opts[:4]
    for o in opts:
        if len(o) > 100: await msg.reply(f"❌ Option too long:\n{o[:60]}..."); return
    await state.update_data(options=opts); await state.set_state(AQ.answer)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=f"{chr(65+i)}. {o}")] for i, o in enumerate(opts)],
        resize_keyboard=True, one_time_keyboard=True
    )
    await msg.reply("✅ Options saved!\n\n🎯 <b>Step 4/5 — Correct answer:</b>", reply_markup=kb)

@router.message(AQ.answer)
async def aq_ans(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    data = await state.get_data(); opts = data["options"]
    t = msg.text.strip().upper(); ai = None
    for i, o in enumerate(opts):
        l = chr(65+i)
        if t == l or t.startswith(f"{l}.") or t == f"{l}. {o}".upper(): ai = i; break
    if ai is None and msg.text.strip().isdigit():
        idx = int(msg.text.strip()) - 1
        if 0 <= idx < len(opts): ai = idx
    if ai is None:
        await msg.reply(f"❌ Invalid. Choose A–{chr(64+len(opts))}.", reply_markup=ReplyKeyboardRemove()); return
    await state.update_data(answer_idx=ai); await state.set_state(AQ.explanation)
    await msg.reply(
        f"✅ Correct: <b>{chr(65+ai)}. {opts[ai]}</b>\n\n"
        "📖 <b>Step 5/5 — Explanation</b> (shown after poll), or /skip:",
        reply_markup=ReplyKeyboardRemove()
    )

@router.message(Command("skip"), AQ.explanation)
async def aq_skip_expl(msg: Message, state: FSMContext): await _save_q(msg, state, None)

@router.message(AQ.explanation)
async def aq_expl(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith("/"): return
    await _save_q(msg, state, msg.text.strip()[:200])

async def _save_q(msg, state, expl):
    data = await state.get_data(); await state.clear()
    photo_id = data.get("photo_id")
    try:
        qid = await db.add_question(data["quiz_id"], data["question"], data["options"],
                                    data["answer_idx"], expl, photo_id)
        cnt  = await db.count_questions(data["quiz_id"])
        quiz = await db.get_quiz(data["quiz_id"])
        opts_text = "\n".join(
            f"{'✅' if i == data['answer_idx'] else '▪️'} {chr(65+i)}. {o}"
            for i, o in enumerate(data["options"])
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Another",            callback_data=f"addq:{data['quiz_id']}")],
            [InlineKeyboardButton(text="✅ Done — Show Quiz Card",  callback_data=f"showcard:{data['quiz_id']}")],
        ])
        photo_line = "🖼 Has photo\n" if photo_id else ""
        expl_line  = "📖 " + expl if expl else ""
        await msg.reply(
            f"🎉 <b>Question #{qid} Saved!</b>\n\n"
            f"{photo_line}"
            f"❓ {data['question']}\n\n{opts_text}\n\n"
            f"{expl_line}\n\n"
            f"📊 Quiz now has <b>{cnt}</b> question(s).",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(e); await msg.reply("❌ Error saving. Try again.")

@router.callback_query(F.data == "done")
async def cb_done(cb: CallbackQuery):
    await cb.answer("✅ Done!")
    await cb.message.reply("✅ Done!\n\nRun your quiz in any group with /startquiz")


# ══════════════════════════════════════════════════════════════
#  START QUIZ IN GROUP
# ══════════════════════════════════════════════════════════════
@router.message(Command("startquiz"))
async def cmd_startquiz(msg: Message, bot: Bot):
    if msg.chat.type == "private":
        await msg.reply(
            "❌ Use /startquiz in a group.\n\n"
            "📌 <b>Forum/topic groups:</b> Go into the specific <b>section/topic</b> "
            "and run /startquiz there — the quiz will start <b>only in that section</b>!"
        ); return

    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("⛔ Only group admins can start a quiz."); return

    tid = thread_id(msg)

    if qm.is_active(msg.chat.id, tid):
        scope = f"this section (topic #{tid})" if tid else "this group"
        await msg.reply(f"⚠️ A quiz is already running in {scope}!\nUse /stopquiz to end it first."); return

    # Only show THIS admin's quizzes — no cross-user leakage
    qs    = await db.get_user_quizzes(msg.from_user.id)
    valid = [(q, await db.count_questions(q["id"])) for q in qs]
    valid = [(q, c) for q, c in valid if c > 0]

    if not valid:
        # DM the admin with instructions, keep group message clean
        try:
            await bot.send_message(
                msg.from_user.id,
                "❌ You have no quiz sets with questions!\n\n"
                "1. /newquiz — create a set\n"
                "2. /addq — add questions\n\n"
                "Then come back and run /startquiz in your group."
            )
        except Exception:
            pass
        await msg.reply(
            f"📩 @{msg.from_user.username or msg.from_user.first_name}, "
            "check your DM — you have no quiz sets with questions yet."
        )
        return

    section_label = f"topic #{tid}" if tid else "this group (main)"

    # If only one quiz → launch directly, no pick list needed
    if len(valid) == 1:
        await _launch(msg, bot, valid[0][0], valid[0][1], tid); return

    # Multiple quizzes → send pick list PRIVATELY to admin via DM
    # encode chat_id and tid into callback so the quiz launches in the right place
    # tid can be None so we encode as 0
    tid_enc = tid if tid else 0
    rows = []
    for q, c in valid:
        label = f"⭐ {q['title']}  ({c} Qs │ ⏱ {timer_label(q.get('timer', DEFAULT_TIMER))})"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"sq:{q['id']}:{msg.chat.id}:{tid_enc}:{msg.from_user.id}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    try:
        await bot.send_message(
            msg.from_user.id,
            f"📚 <b>Pick a quiz to start in «{msg.chat.title}»</b>"
            + (f" — topic #{tid}" if tid else "") + ":",
            reply_markup=kb
        )
        # Public notice in group — no quiz names exposed
        name = f"@{msg.from_user.username}" if msg.from_user.username else f"<b>{msg.from_user.first_name}</b>"
        await msg.reply(
            f"📩 {name}, check your DM to pick which quiz to start here!"
        )
    except Exception:
        # Bot is blocked in DM — fall back to in-group (less ideal but won't crash)
        rows2 = []
        for q, c in valid:
            label = f"⭐ {q['title']}  ({c} Qs │ ⏱ {timer_label(q.get('timer', DEFAULT_TIMER))})"
            rows2.append([InlineKeyboardButton(
                text=label,
                callback_data=f"sq:{q['id']}:{msg.chat.id}:{tid_enc}:{msg.from_user.id}"
            )])
        await msg.reply(
            f"📚 <b>Pick a quiz</b> (open my DM first for privacy):\n"
            f"<i>💡 Start a DM with me so I can send the list privately next time.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows2)
        )


@router.callback_query(F.data.startswith("sq:"))
async def cb_sq(cb: CallbackQuery, bot: Bot):
    parts  = cb.data.split(":")
    qid    = int(parts[1])
    # New format: sq:<qid>:<chat_id>:<tid_enc>:<uid>
    # Old format (fallback): sq:<qid>:<uid>  — tid unknown, skip
    if len(parts) == 5:
        chat_id = int(parts[2])
        tid_enc = int(parts[3])
        uid     = int(parts[4])
        tid     = tid_enc if tid_enc != 0 else None
    else:
        # Old in-group format — derive from message context
        uid     = int(parts[2])
        chat_id = cb.message.chat.id
        tid     = thread_id_cb(cb)

    if cb.from_user.id != uid:
        await cb.answer("❌ Only the admin who typed /startquiz can pick.", show_alert=True); return
    await cb.answer("✅ Starting quiz…")

    if qm.is_active(chat_id, tid):
        await cb.message.reply("⚠️ A quiz is already running there! Use /stopquiz first."); return

    q = await db.get_quiz(qid)
    if not q:
        await cb.message.reply("❌ Quiz not found."); return
    cnt = await db.count_questions(qid)
    if cnt == 0:
        await cb.message.reply("❌ No questions in that quiz!"); return

    # Confirm to admin in DM
    await cb.message.reply(
        f"✅ <b>«{q['title']}»</b> is starting in your group now!"
    )

    # Launch in the actual group/topic
    await _launch_in(bot, chat_id, tid, q, cnt, cb.from_user.id)

async def _launch(msg, bot, quiz, cnt, tid):
    """Launch from a Message object (single-quiz fast path)."""
    await _launch_in(bot, msg.chat.id, tid, quiz, cnt, msg.from_user.id)

async def _launch_in(bot, chat_id, tid, quiz, cnt, started_by):
    """Launch into an explicit chat_id/tid (used when pick happened in DM)."""
    qs   = await db.get_questions(quiz["id"])
    qids = [q["id"] for q in qs]
    if quiz.get("shuffle"): random.shuffle(qids)
    timer   = quiz.get("timer", DEFAULT_TIMER)
    section = f"section: topic #{tid}" if tid else "main group"
    skw = dict(
        chat_id=chat_id,
        text=(
            f"🚀 <b>QUIZ STARTING!</b>\n\n"
            f"📋 <b>{quiz['title']}</b>\n"
            + (f"📝 {quiz['description']}\n" if quiz.get("description") else "") +
            f"\n❓ Questions : <b>{cnt}</b>\n"
            f"⏱ Timer     : <b>{timer_label(timer)}</b> per question\n"
            f"📍 Section   : <b>{section}</b>\n"
            f"🔀 Shuffle   : <b>{'Yes' if quiz.get('shuffle') else 'No'}</b>\n"
            f"🔴 First tap counts — no changing answers!\n"
            f"🏆 Top 15 shown at the end\n\nGet ready… 🎯"
        )
    )
    if tid: skw["message_thread_id"] = tid
    await bot.send_message(**skw)
    await qm.start(quiz["id"], chat_id, tid, started_by, qids, timer)


# ══════════════════════════════════════════════════════════════
#  STOP QUIZ
# ══════════════════════════════════════════════════════════════
@router.message(Command("stopquiz"))
async def cmd_stopquiz(msg: Message, bot: Bot):
    if msg.chat.type == "private": await msg.reply("❌ Use in a group."); return
    if not await is_admin(bot, msg.chat.id, msg.from_user.id):
        await msg.reply("⛔ Only admins can stop a quiz."); return
    tid = thread_id(msg)
    ok  = await qm.stop(msg.chat.id, tid)
    section = f"section #{tid}" if tid else "this group"
    await msg.reply(f"🛑 Quiz stopped in {section}. Results shown above." if ok
                    else f"ℹ️ No active quiz in {section}.")


# ══════════════════════════════════════════════════════════════
#  POLL ANSWER HANDLER
# ══════════════════════════════════════════════════════════════
@router.poll_answer()
async def on_poll_answer(pa: PollAnswer):
    try: await qm.handle_poll_answer(pa)
    except Exception as e: logger.error(f"Poll answer err: {e}")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    global qm, db, BOT_USERNAME
    logger.info("Starting Quiz Bot…")
    db  = DB(); await db.init()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me  = await bot.get_me(); BOT_USERNAME = me.username or ""
    logger.info(f"Bot: @{BOT_USERNAME}")
    qm  = QuizManager(bot, db)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot is polling…")
    try:
        await dp.start_polling(bot, allowed_updates=["message","callback_query","poll_answer"])
    finally:
        await db.close(); await bot.session.close(); logger.info("Bot stopped.")

if __name__ == "__main__":
    asyncio.run(main())
