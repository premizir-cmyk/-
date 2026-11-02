import asyncio
import json
import logging
import os
import random
import time
from collections import defaultdict
from html import escape

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = os.getenv("BOT_TOKEN", "8660698360:AAEGVQen4KR8MXC3bJXZqDkAijZRZdttMfs")
OWNER_ID = 7605961809
CHANNEL_ID = -1003932211788

DATA_DIR = "/app/data" if os.path.isdir("/app/data") else os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DATA_DIR, "database.db")

bot = Bot(token=TOKEN)
dp = Dispatcher()
user_locks = defaultdict(asyncio.Lock)

# ======================================================================
#                              БАЗА ДАННЫХ
# ======================================================================
async def db_start():
    os.makedirs(DATA_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 500,
                jobs_done INTEGER DEFAULT 0,
                last_work INTEGER DEFAULT 0,
                last_bonus INTEGER DEFAULT 0,
                story_status TEXT DEFAULT 'not_started',
                last_quest_time INTEGER DEFAULT 0,
                referred_by INTEGER,
                earned INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quest (
                user_id INTEGER PRIMARY KEY,
                node TEXT,
                hp INTEGER,
                inv TEXT,
                flags TEXT,
                checkpoint TEXT,
                endings TEXT,
                claimed TEXT,
                started INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quest2 (
                user_id INTEGER PRIMARY KEY,
                node TEXT,
                hp INTEGER,
                inv TEXT,
                flags TEXT,
                checkpoint TEXT,
                endings TEXT,
                claimed TEXT,
                started INTEGER
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS work_cd (user_id INTEGER, job TEXT, last_ts INTEGER, PRIMARY KEY (user_id, job))")
        
        for col in [("users", "earned", "INTEGER DEFAULT 0"), ("users", "last_quest_time", "INTEGER DEFAULT 0"), ("quest", "started", "INTEGER"), ("quest2", "started", "INTEGER")]:
            try:
                await db.execute(f"ALTER TABLE {col[0]} ADD COLUMN {col[1]} {col[2]}")
            except Exception:
                pass
        await db.commit()


async def register_user(user_id: int, username: str, full_name: str):
    safe_name = "".join(c for c in full_name if c.isalnum() or c in " _-").strip() or "Без имени"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, balance, jobs_done, last_work, last_bonus, story_status, earned, last_quest_time) "
            "VALUES (?, ?, 500, 0, 0, 0, 'not_started', 0, 0)",
            (user_id, safe_name),
        )
        await db.commit()


async def add_coins(user_id: int, amount: int, earn: bool = False):
    async with aiosqlite.connect(DB_FILE) as db:
        if earn:
            await db.execute("UPDATE users SET balance = balance + ?, earned = earned + ? WHERE user_id = ?", (amount, amount, user_id))
        else:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def spend(user_id: int, amount: int) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount > 0


async def show(cb: types.CallbackQuery, text: str, kb=None):
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            logging.warning("edit_text error: %s", e)


# ======================================================================
#                      ПРОВЕРКА ПОДПИСКИ НА КАНАЛ
# ======================================================================
_sub_cache = {}
_known_users = set()
_channel_url = None

async def get_channel_url():
    global _channel_url
    if _channel_url:
        return _channel_url
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        if chat.username:
            _channel_url = f"https://t.me/{chat.username}"
        else:
            _channel_url = chat.invite_link or await bot.export_chat_invite_link(CHANNEL_ID)
    except Exception as e:
        logging.warning("Не удалось получить ссылку на канал: %s", e)
    return _channel_url


async def is_subscribed(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    ts = _sub_cache.get(uid)
    if ts and time.time() - ts < 120:
        return True
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
    except Exception as e:
        logging.error("Проверка подписки не удалась: %s", e)
        return True
    ok = m.status in ("member", "administrator", "creator") or (m.status == "restricted" and getattr(m, "is_member", False))
    if ok:
        _sub_cache[uid] = time.time()
    return ok


async def ensure_user(user):
    if user.id in _known_users:
        return
    await register_user(user.id, user.username or "NoName", user.first_name or "Игрок")
    _known_users.add(user.id)


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        if isinstance(event, types.CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)
        if await is_subscribed(user.id):
            await ensure_user(user)
            return await handler(event, data)

        url = await get_channel_url()
        rows = []
        if url:
            rows.append([InlineKeyboardButton(text="📢 Подписаться на канал", url=url)])
        rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")])
        text = "🔒 Чтобы пользоваться ботом, подпишись на наш канал, а потом нажми «Я подписался»."
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        if isinstance(event, types.CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(text, reply_markup=kb)
        else:
            await event.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _sub_cache.pop(uid, None)
    if await is_subscribed(uid):
        await ensure_user(callback.from_user)
        await callback.answer("✅ Отлично!")
        await show(callback, "Добро пожаловать! Главное меню:", get_main_menu())
    else:
        await callback.answer("❌ Подписка не найдена. Подпишись и нажми ещё раз.", show_alert=True)


# ======================================================================
#                              МЕНЮ
# ======================================================================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💼 Работа", callback_data="work_menu"),
            InlineKeyboardButton(text="📖 Выбор квеста", callback_data="quests_hub"),
        ],
        [
            InlineKeyboardButton(text="🎰 Казино (Кости)", callback_data="casino"),
            InlineKeyboardButton(text="🎡 Колесо Фортуны", callback_data="fortune_wheel"),
        ],
        [
            InlineKeyboardButton(text="🎯 Тир по медузам", callback_data="game_jelly"),
            InlineKeyboardButton(text="✂️ КНБ с Патриком", callback_data="game_rps"),
        ],
        [
            InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily_bonus"),
            InlineKeyboardButton(text="🏆 Розыгрыш", callback_data="contest"),
        ],
        [
            InlineKeyboardButton(text="💰 Профиль", callback_data="balance"),
            InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top"),
        ],
    ])


def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    name = message.from_user.first_name or "Игрок"
    await ensure_user(message.from_user)
    await message.answer(
        f"Здорово, <b>{escape(name)}</b>! 👋\n\nДобро пожаловать в Бикини Боттом. Выбирай занятие:",
        reply_markup=get_main_menu(),
        parse_mode="HTML",
    )


@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    await message.answer("Главное меню:", reply_markup=get_main_menu())


@dp.callback_query(F.data == "back_menu")
async def back_menu(callback: types.CallbackQuery):
    await callback.answer()
    await show(callback, "Главное меню:", get_main_menu())


@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done, earned FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
    balance, jobs, earned = row if row else (500, 0, 0)
    q1 = await get_q(uid, "quest")
    q2 = await get_q(uid, "quest2")
    ends1 = len(q1["endings"]) if q1 else 0
    ends2 = len(q2["endings"]) if q2 else 0
    text = (
        f"👤 <b>Твой профиль:</b>\n\n💰 Баланс: <b>{balance} коинов</b>\n🛠 Смен: <b>{jobs}</b>\n"
        f"🏆 Заработано для розыгрыша: <b>{earned}</b>\n"
        f"📖 Концовок Квеста 1: <b>{ends1}/{len(ENDINGS)}</b>\n"
        f"📖 Концовок Квеста 2: <b>{ends2}/{len(ENDINGS_Q2)}</b>"
    )
    await show(callback, text, get_back_keyboard())


# ======================================================================
#                        ХАБ КВЕСТОВ (ЛИМИТ 1 В ДЕНЬ)
# ======================================================================
@dp.callback_query(F.data == "quests_hub")
async def quests_hub(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_quest_time FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
    last_t = row[0] if row else 0
    now = int(time.time())
    cd_active = (uid != OWNER_ID) and (now - last_t < 86400)
    left = 86400 - (now - last_t)

    text = "📖 <b>Выбор сюжетного квеста</b>\n\n📜 Правило: <b>1 квест в 1 день</b> (пройти можно любой из двух на выбор, следующий — через 24 часа)."
    if cd_active:
        h, m = left // 3600, (left % 3600) // 60
        text += f"\n\n⏳ Следующий запуск квеста будет доступен через <b>{h} ч {m} мин</b>."

    kb_rows = [
        [InlineKeyboardButton(text="🕵️ Квест 1: Тайна формулы", callback_data="story_start")],
        [InlineKeyboardButton(text="🕵️‍♂️ Квест 2: Похищение Спанч Боба", callback_data="story2_start")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ]
    await show(callback, text, InlineKeyboardMarkup(inline_keyboard=kb_rows))


async def check_quest_cooldown(uid):
    if uid == OWNER_ID:
        return True, 0
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_quest_time FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
    last_t = row[0] if row else 0
    now = int(time.time())
    if now - last_t < 86400:
        left = 86400 - (now - last_t)
        return False, left
    return True, 0


async def update_quest_cooldown(uid):
    if uid == OWNER_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET last_quest_time = ? WHERE user_id = ?", (int(time.time()), uid))
        await db.commit()


# ======================================================================
#                 РАБОТЫ И РОЗЫГРЫШ
# ======================================================================
WORK_CD = 4 * 3600
JOBS = {
    "chef": ("🍔 Повар", (250, 350)),
    "courier": ("🛵 Курьер", (200, 300)),
    "dishes": ("🧽 Мойщик посуды", (300, 450)),
    "jelly": ("🪼 Ловец медуз", (350, 500)),
    "cashier": ("💵 Кассир", (400, 600)),
    "guard": ("🕵️ Охранник", (500, 700)),
    "acc": ("🧮 Бухгалтер Крабса", (700, 1000)),
}
LAYERS = [
    ("bun_top", "🍞 Верхняя булочка"),
    ("meat", "🥩 Котлета"),
    ("cheese", "🧀 Сыр"),
    ("salad", "🥬 Салат"),
    ("tomato", "🍅 Помидор"),
    ("bun_bot", "🍞 Нижняя булочка"),
]
sessions = {}

def fmt_time(sec):
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h} ч {m} мин" if h else f"{max(m, 1)} мин"

def kb_rows(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)

def make_opts(correct, deltas, n):
    opts = {correct}
    for d in random.sample(deltas, len(deltas)):
        if len(opts) >= n:
            break
        if correct + d > 0:
            opts.add(correct + d)
    opts = list(opts)
    random.shuffle(opts)
    return opts

async def cd_left(uid, job):
    if uid == OWNER_ID:
        return 0
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_ts FROM work_cd WHERE user_id = ? AND job = ?", (uid, job)) as cur:
            row = await cur.fetchone()
    last = row[0] if row else 0
    return max(0, WORK_CD - (int(time.time()) - last))

async def set_cd(uid, job):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO work_cd (user_id, job, last_ts) VALUES (?, ?, ?)", (uid, job, int(time.time())))
        await db.commit()

async def finish_job(cb, uid, s, ok, ok_text="", fail_text=""):
    if sessions.get(uid) is not s:
        return
    sessions.pop(uid, None)
    kb = kb_rows([[btn("💼 К списку работ", "work_menu")], [btn("🏠 Меню", "back_menu")]])
    if ok:
        lo, hi = JOBS[s["job"]][1]
        reward = random.randint(lo, hi)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE users SET balance = balance + ?, earned = earned + ?, jobs_done = jobs_done + 1 WHERE user_id = ?",
                (reward, reward, uid),
            )
            await db.commit()
        await show(cb, f"✅ {ok_text}\n\n💰 Зарплата: <b>+{reward} коинов</b>\n⏳ Эта работа снова будет доступна через 4 часа.", kb)
    else:
        await show(cb, f"❌ {fail_text}\n\nСмена потеряна. ⏳ Эта работа снова будет доступна через 4 часа.", kb)

@dp.callback_query(F.data == "work_menu")
async def work_menu(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    sessions.pop(uid, None)
    lines, rows = [], []
    for key, (title, (lo, hi)) in JOBS.items():
        left = await cd_left(uid, key)
        status = "✅ доступна" if not left else f"⏳ {fmt_time(left)}"
        lines.append(f"{title}: {lo}–{hi} 🪙 • {status}")
        rows.append([btn(title if not left else f"{title} ⏳ {fmt_time(left)}", f"j:start:{key}")])
    rows.append([btn("◀️ Назад в меню", "back_menu")])
    text = "💼 <b>Работа</b>\n\nУ каждой работы свой таймер (4 часа).\n\n" + "\n".join(lines)
    await show(callback, text, kb_rows(rows))

@dp.callback_query(F.data.startswith("j:start:"))
async def job_start(callback: types.CallbackQuery):
    uid = callback.from_user.id
    key = callback.data.split(":")[2]
    if key not in JOBS:
        await callback.answer()
        return
    left = await cd_left(uid, key)
    if left:
        await callback.answer(f"⏳ Эта работа будет доступна через {fmt_time(left)}", show_alert=True)
        return
    await callback.answer()
    await set_cd(uid, key)
    await STARTERS[key](callback, uid)

@dp.callback_query(F.data.startswith("j:a:"))
async def job_answer(callback: types.CallbackQuery):
    uid = callback.from_user.id
    s = sessions.get(uid)
    if not s:
        await callback.answer("Смена уже закончилась.", show_alert=True)
        return
    await callback.answer()
    await ANSWERS[s["job"]](callback, uid, s, callback.data[4:])

async def start_chef(cb, uid):
    picked = random.sample(LAYERS, 4)
    s = {"job": "chef", "recipe": [k for k, _ in picked], "step": 0}
    sessions[uid] = s
    names = " ➡️ ".join(n for _, n in picked)
    await show(cb, f"🍔 <b>Повар</b>\n\n📜 Запомни рецепт:\n<code>{names}</code>\n\n⏱ Показываю <b>4 секунды</b>...")
    await asyncio.sleep(4)
    if sessions.get(uid) is not s:
        return
    await chef_ask(cb, s)

async def chef_ask(cb, s):
    opts = LAYERS[:]
    random.shuffle(opts)
    rows = [[btn(n, f"j:a:{k}")] for k, n in opts]
    await show(cb, f"🍳 <b>Сборка: слой {s['step'] + 1} из {len(s['recipe'])}</b>\nКакой слой дальше?", kb_rows(rows))

async def chef_answer(cb, uid, s, arg):
    if arg != s["recipe"][s["step"]]:
        await finish_job(cb, uid, s, False, fail_text="Ошибка в слоях, заказ испорчен.")
        return
    s["step"] += 1
    if s["step"] >= len(s["recipe"]):
        await finish_job(cb, uid, s, True, ok_text="Идеальный крабсбургер!")
    else:
        await chef_ask(cb, s)

async def start_courier(cb, uid):
    s = {"job": "courier", "state": "wait"}
    sessions[uid] = s
    await show(cb, "🛵 <b>Курьер</b>\n\nЖди заказ. Нажми кнопку, когда появится 🍔.", kb_rows([[btn("⏳ Жди...", "j:a:early")]]))
    await asyncio.sleep(random.uniform(2.5, 7))
    if sessions.get(uid) is not s:
        return
    s["state"] = "go"
    await show(cb, "🍔 <b>ЗАКАЗ ГОТОВ!</b>", kb_rows([[btn("🍔 ЗАБРАТЬ!", "j:a:go")]]))
    s["t"] = time.time()

async def courier_answer(cb, uid, s, arg):
    if arg == "early" or s.get("state") != "go":
        await finish_job(cb, uid, s, False, fail_text="Слишком рано.")
    elif time.time() - s["t"] > 3.0:
        await finish_job(cb, uid, s, False, fail_text="Слишком медленно.")
    else:
        await finish_job(cb, uid, s, True, ok_text="Доставлено вовремя!")

DISH_POOL = ["🍽", "🥄", "🍴", "🧂", "🥢", "🫖"]
async def start_dishes(cb, uid):
    seq = [random.randrange(6) for _ in range(5)]
    s = {"job": "dishes", "seq": seq, "pos": 0}
    sessions[uid] = s
    shown = " ".join(DISH_POOL[i] for i in seq)
    await show(cb, f"🧽 <b>Мойщик посуды</b>\n\nЗапомни порядок:\n\n{shown}\n\n⏱ Показываю <b>4 секунды</b>...")
    await asyncio.sleep(4)
    if sessions.get(uid) is not s:
        return
    await dishes_ask(cb, s)

async def dishes_ask(cb, s):
    done = " ".join(DISH_POOL[i] for i in s["seq"][:s["pos"]]) or "—"
    rows = [[btn(DISH_POOL[i], f"j:a:{i}") for i in (r * 2, r * 2 + 1)] for r in range(3)]
    await show(cb, f"🧽 Повтори порядок ({s['pos']} из {len(s['seq'])}):\n\n{done}", kb_rows(rows))

async def dishes_answer(cb, uid, s, arg):
    if int(arg) != s["seq"][s["pos"]]:
        await finish_job(cb, uid, s, False, fail_text="Посуда разбита.")
        return
    s["pos"] += 1
    if s["pos"] >= len(s["seq"]):
        await finish_job(cb, uid, s, True, ok_text="Посуда сверкает!")
    else:
        await dishes_ask(cb, s)

JELLY_CELLS = 8
def jelly_render(s):
    zone = (s["target"], s["target"] + 1)
    cells = ["🪼" if i == s["pos"] else "🥅" if i in zone else "🌊" for i in range(JELLY_CELLS)]
    return " ".join(cells)

async def start_jelly(cb, uid):
    s = {"job": "jelly", "pos": 0, "target": random.randint(2, 5)}
    sessions[uid] = s
    kb = kb_rows([[btn("🥅 ЛОВИ!", "j:a:catch")]])
    for pos in range(JELLY_CELLS):
        s["pos"] = pos
        await show(cb, f"🪼 <b>Ловец медуз</b>\n\n{jelly_render(s)}", kb)
        await asyncio.sleep(1.5)
        if sessions.get(uid) is not s:
            return
    await finish_job(cb, uid, s, False, fail_text="Уплыла.")

async def jelly_answer(cb, uid, s, arg):
    if s["target"] <= s["pos"] <= s["target"] + 1:
        await finish_job(cb, uid, s, True, ok_text="Медуза поймана!")
    else:
        await finish_job(cb, uid, s, False, fail_text="Мимо сачка.")

MENU_ITEMS = [("🍔 Крабсбургер", 35), ("🍟 Картошка", 20), ("🥤 Кола", 15), ("🥗 Салат", 25), ("🍦 Мороженое", 18), ("🍕 Кельп-пицца", 40)]
async def cashier_show(cb, s):
    items = random.sample(MENU_ITEMS, 3)
    total = sum(p for _, p in items)
    paid = (total // 50 + 1) * 50
    change = paid - total
    s["change"] = change
    s["opts"] = make_opts(change, [-10, -5, -3, 3, 5, 10], 3)
    s["deadline"] = time.time() + 12
    order = "\n".join(f"{n} — {p}" for n, p in items)
    rows = [[btn(f"{v} коинов", f"j:a:{i}")] for i, v in enumerate(s["opts"])]
    await show(cb, f"💵 <b>Кассир — клиент {s['round'] + 1} из 2</b>\n\n{order}\n\nДаёт <b>{paid}</b>. Сдача?", kb_rows(rows))

async def start_cashier(cb, uid):
    s = {"job": "cashier", "round": 0}
    sessions[uid] = s
    await cashier_show(cb, s)

async def cashier_answer(cb, uid, s, arg):
    if time.time() > s["deadline"] + 1.5 or s["opts"][int(arg)] != s["change"]:
        await finish_job(cb, uid, s, False, fail_text="Ошибка сдачи.")
    else:
        s["round"] += 1
        if s["round"] >= 2:
            await finish_job(cb, uid, s, True, ok_text="Клиенты довольны!")
        else:
            await cashier_show(cb, s)

GUARD_PAIRS = [("🦀", "🦞"), ("😀", "😃"), ("🐟", "🐠"), ("🔴", "🟠"), ("🐙", "🦑"), ("🌕", "🌝")]
async def guard_show(cb, s):
    a, b = random.choice(GUARD_PAIRS)
    if random.random() < 0.5:
        a, b = b, a
    odd = random.randrange(16)
    s["odd"] = odd
    s["deadline"] = time.time() + 8
    rows = [[btn(b if r * 4 + c == odd else a, f"j:a:{r * 4 + c}") for c in range(4)] for r in range(4)]
    await show(cb, f"🕵️ <b>Охранник — обход {s['round'] + 1} из 3</b>\n\nНайди лишнего!", kb_rows(rows))

async def start_guard(cb, uid):
    s = {"job": "guard", "round": 0}
    sessions[uid] = s
    await guard_show(cb, s)

async def guard_answer(cb, uid, s, arg):
    if time.time() > s["deadline"] + 1.5 or int(arg) != s["odd"]:
        await finish_job(cb, uid, s, False, fail_text="Тревога!")
    else:
        s["round"] += 1
        if s["round"] >= 3:
            await finish_job(cb, uid, s, True, ok_text="Нарушители пойманы!")
        else:
            await guard_show(cb, s)

async def start_acc(cb, uid):
    nums = [random.randint(12, 59) for _ in range(4)]
    s = {"job": "acc", "total": sum(nums)}
    sessions[uid] = s
    shown = "  •  ".join(str(n) for n in nums)
    await show(cb, f"🧮 <b>Бухгалтер</b>\n\nЧисла:\n<b>{shown}</b>\n\n⏱ Запомни на 5 секунд.")
    await asyncio.sleep(5)
    if sessions.get(uid) is not s:
        return
    s["opts"] = make_opts(s["total"], [-1, 1, -2, 2, -10, 10, -11, 11], 4)
    s["deadline"] = time.time() + 20
    rows = [[btn(str(s["opts"][r * 2 + c]), f"j:a:{r * 2 + c}") for c in range(2)] for r in range(2)]
    await show(cb, "🧮 Сумма четырёх чисел?", kb_rows(rows))

async def acc_answer(cb, uid, s, arg):
    if time.time() > s["deadline"] + 1.5 or s["opts"][int(arg)] != s["total"]:
        await finish_job(cb, uid, s, False, fail_text="Ошибка в отчёте.")
    else:
        await finish_job(cb, uid, s, True, ok_text="Отчёт сошёлся!")

STARTERS = {"chef": start_chef, "courier": start_courier, "dishes": start_dishes, "jelly": start_jelly, "cashier": start_cashier, "guard": start_guard, "acc": start_acc}
ANSWERS = {"chef": chef_answer, "courier": courier_answer, "dishes": dishes_answer, "jelly": jelly_answer, "cashier": cashier_answer, "guard": guard_answer, "acc": acc_answer}


# Розыгрыш
async def contest_text(uid):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT username, earned FROM users WHERE user_id != ? AND earned > 0 ORDER BY earned DESC LIMIT 10", (OWNER_ID,)) as cur:
            rows = await cur.fetchall()
        async with db.execute("SELECT earned FROM users WHERE user_id = ?", (uid,)) as cur:
            me = await cur.fetchone()
        mine = me[0] if me else 0
        async with db.execute("SELECT COUNT(*) FROM users WHERE user_id != ? AND earned > ?", (OWNER_ID, mine)) as cur:
            ahead = (await cur.fetchone())[0]
    lines = [f"{['🥇', '🥈', '🥉'][i] if i < 3 else f'{i+1}.'} {escape(str(name))} — <b>{earned}</b>" for i, (name, earned) in enumerate(rows)]
    top_text = "\n".join(lines) or "Пока никто не заработал."
    return f"🏆 <b>Розыгрыш</b>\n\nПобеждают 3 игрока с наибольшим заработком (зарплаты и квесты).\n\n<b>Топ-10:</b>\n{top_text}"

@dp.callback_query(F.data == "contest")
async def contest_cb(callback: types.CallbackQuery):
    await callback.answer()
    await show(callback, await contest_text(callback.from_user.id), kb_rows([[btn("💼 К работам", "work_menu")], [btn("◀️ Назад", "back_menu")]]))


# ======================================================================
#                      КВЕСТ 1: ТАЙНА ФОРМУЛЫ (ОГРОМНЫЙ)
# ======================================================================
MAX_HP = 5
ITEMS = {"lupa": "🔎 Лупа", "drone": "🚁 Дрон", "note": "🗒 Записка", "blueprint": "📐 Чертёж", "napkin": "🧻 Салфетка"}
CLUES = {
    "c_lock": "🔐 Царапины", "c_fuel": "🧪 Топливо Сэнди", "c_inside": "🪟 Окно изнутри", "c_policy": "📄 Страховка",
    "c_print": "🖐 Отпечаток", "c_witness": "🗣 Показания Боба", "c_snore": "😴 Храп", "c_pink": "🩷 Розовые следы",
    "c_patrick": "🪨 Патрик", "c_tracks": "🛞 Следы гусениц", "c_note": "🗒 Записка 3:00", "c_cam": "📹 Камера",
    "c_squid_clear": "🦑 Сквидвард чист", "c_fuelmatch": "⛽ Топливо совпало", "c_plankton_alibi": "🧪 Алиби Планктона", "c_plankton_saw": "👀 Видел фигуру"
}
ENDINGS = {"hasty": "Поспешный арест", "squid": "Невинная жертва", "plankton": "По Планктону", "patrick": "Сон Патрика", "merger": "Красти-Чам", "truth_mercy": "Прощённый Крабс", "truth_justice": "Крабс в тюрьме"}
NODES = {}

def add(nid, text, choices=(), **kw):
    NODES[nid] = {"t": text, "c": list(choices), **kw}

def C(label, to, **kw):
    return {"l": label, "to": to, **kw}

def calc_reward(elapsed):
    mins = elapsed // 60
    if mins <= 15:
        return 15000
    reward = 15000 - (mins - 15) * 1000
    return max(4000, reward)

add("intro", "🌊 <b>Пролог. Дело о пропавшей формуле</b>\n\nУтро в Бикини Боттом. У «Красти Краб» — полицейская лента и вопли мистера Крабса: ночью из сейфа исчезла <b>секретная формула крабсбургера</b>.", [C("🚪 Приступить к расследованию", "hall")])

add("hall", "🏠 <b>Главный зал «Красти Краб»</b>\n\nКуда отправимся искать улики?", [
    C("🔐 Кабинет Крабса", "office"),
    C("🍳 Кухня", "kitchen"),
    C("🌃 Задний переулок", "alley"),
    C("🦀 Поговорить с Крабсом", "krabs_talk"),
    C("🎷 Навестить Сквидварда", "squid_house"),
    C("🧪 Вернуться в Чам Баккет", "chum_gate", all=["c_tracks"], hide=True),
    C("📋 Назвать виновного (нужно 4 улики)", "theory", clues=4),
], cp=True)

add("office", "🔐 <b>Кабинет Крабса</b>\n\nСейф нараспашку, окно приоткрыто.", [
    C("🔐 Изучить замок сейфа", "lock_check"),
    C("🪟 Осмотреть окно", "window"),
    C("🗄 Порыться в столе", "desk"),
    C("◀️ В зал", "hall"),
])

add("lock_check", "На замке глубокие царапины с капельками водорослевого топлива.", [C("🌳 К Сэнди", "sandy_lab"), C("◀️ Назад", "office")], flags=["c_lock"])
add("sandy_lab", "🌳 <b>Купол Сэнди</b>\n\nСэнди исследует образец и дает лупу.", [C("◀️ В зал", "hall")], flags=["c_fuel"], give="lupa")
add("window", "Окно открыто <b>изнутри</b>.", [C("◀️ Назад", "office")], flags=["c_inside"])
add("desk", "Свежий страховой полис на крупную сумму, оформленный вчера.", [C("◀️ Назад", "office")], flags=["c_policy"])

add("kitchen", "🍳 <b>Кухня</b>\n\nГубка Боб оттирает гриль и всхлипывает.", [
    C("🍟 Осмотреть фритюрницу", "fryer"),
    C("🧽 Поговорить с Бобом", "bob_talk"),
    C("◀️ В зал", "hall"),
])

add("fryer", "На фритюрнице жирный отпечаток.", [C("🔎 Снять отпечаток лупой", "print", item="lupa", hint="Нужна лупа"), C("◀️ Назад", "kitchen")])
add("print", "Отпечаток металлического сочленения робота!", [C("◀️ Назад", "kitchen")], flags=["c_print"])
add("bob_talk", "Боб слышал ночью храп с присвистом и бормотание про желе.", [C("◀️ Назад", "kitchen")], flags=["c_witness", "c_snore"])

add("alley", "🌃 <b>Задний переулок</b>", [
    C("🛞 Изучить следы гусениц", "tracks"),
    C("🗑 Осмотреть мусорный бак", "dumpster"),
    C("◀️ В зал", "hall"),
])
add("tracks", "Следы ведут к Чам Баккет.", [C("🧪 К Чам Баккет", "chum_gate"), C("◀️ Назад", "alley")], flags=["c_tracks"])
add("dumpster", "В баке записка: «Ровно в 3:00. Окно открою изнутри. — К.»", [C("◀️ Назад", "alley")], give="note", flags=["c_note"])

add("krabs_talk", "🦀 <b>Мистер Крабс</b>\n\nКрабс паникует и требует найти вора.", [C("🌙 Спросить про ночь", "krabs_night"), C("◀️ Назад", "hall")])
add("krabs_night", "— Дома спал! — слишком быстро отвечает он.", [C("◀️ Назад", "krabs_talk")], flags=["krabs_sweat"])

add("squid_house", "🎷 <b>Дом Сквидварда</b>\n\nСквидвард играет на кларнете.", [C("🦯 Сравнить трость", "squid_cane"), C("◀️ Уйти", "hall")])
add("squid_cane", "Диаметр трости не подходит. Сквидвард чист.", [C("◀️ Назад", "hall")], flags=["c_squid_clear"])

add("chum_gate", "🧪 <b>Чам Баккет</b>\n\nКак проникнем внутрь?", [C("🚪 Через парадный вход", "front_door"), C("◀️ В переулок", "alley")], cp=True)
add("front_door", "Планктон предлагает Чам-бургер.", [C("🧪 Допросить Планктона", "plankton_call")])
add("plankton_call", "🧪 <b>Шелдон Планктон</b>\n\n— Не брал я формулу! Ночью видел розовую фигуру и в окне Красти Краб мелькнула клешня!", [
    C("⛓ Арестовать", "end_hasty"),
    C("🤝 Объединить бизнесы", "end_merger"),
    C("◀️ К воротам", "chum_gate")
], flags=["c_plankton_alibi", "c_plankton_saw"])

add("theory", "📋 <b>Развязка</b>\n\nКого обвиняем?", [
    C("🧪 Планктона", "end_plankton"),
    C("🦀 Мистера Крабса", "end_truth_justice", all=["c_policy", "c_inside", "c_note"]),
    C("⭐ Патрика", "end_patrick", item="napkin"),
    C("◀️ Искать улики", "hall")
])

add("end_hasty", "🚔 Поспешный арест. Провал.", end="hasty")
add("end_plankton", "⚖️ Планктон осужден судом.", end="plankton")
add("end_merger", "🤝 Создана Корпорация Красти-Чам!", end="merger")
add("end_truth_mercy", "🕊 Крабс прощён и вернул страховку.", end="truth_mercy")
add("end_truth_justice", "🚔 Крабс арестован за мошенничество со страховкой!", end="truth_justice")
add("end_squid", "⚖️ Сквидвард выиграл суд за клевету.", end="squid")
add("end_patrick", "🤯 Патрик съел формулу во сне и записал на салфетке.", end="patrick")
add("dead", "💀 Силы кончились.", fail=True)


# ======================================================================
#         КВЕСТ 2: ПОХИЩЕНИЕ СПАНЧ БОБА (ОГРОМНЫЙ И МАСШТАБНЫЙ)
# ======================================================================
ITEMS_Q2 = {
    "net": "🥅 Элитный сачок", "map": "🗺 Пиратская карта", "jar": "🫙 Банка из-под солений",
    "hook": "⚓ Старый крюк", "glasses": "👓 Очки Планктона", "compass": "🧭 Компас Летучего Голландца"
}
CLUES_Q2 = {
    "s2_hook": "⚓ Металлический крюк на окне", "s2_net": "🥅 Рваный сачок Боба у обрыва", "s2_jelly": "🪼 Взвод безумных медуз",
    "s2_note": "🗒 Записка в банке: «Он в пещере у Потолка»", "s2_print": "🖐 Следы гигантских пальцев", "s2_bubble": "🫧 Мыльные пузыри на скале",
    "s2_map": "🗺 Карта глубин", "s2_patch": "🏴‍☠️ Пиратская метка", "s2_chum": "🥫 Запах чама в пещере", "s2_alibi": "🧪 Алиби Планктона подтверждено",
    "s2_ghost": "👻 Слизь Летучего Голландца", "s2_sandy_rocket": "🚀 Следы ракетного топлива Сэнди"
}
ENDINGS_Q2 = {
    "s2_surface": "Пленник суши", "s2_cave": "Пещерный отшельник", "s2_pirate": "Пиратский юнга",
    "s2_jelly_king": "Король медуз", "s2_chum_bot": "Робо-повар", "s2_true_hero": "Спаситель Бикини Боттом", "s2_bad_end": "Забытый на дне"
}
NODES_Q2 = {}

def add2(nid, text, choices=(), **kw):
    NODES_Q2[nid] = {"t": text, "c": list(choices), **kw}

def C2(label, to, **kw):
    return {"l": label, "to": to, **kw}

add2("intro2", "🧽 <b>Пролог. Где Спанч Боб?</b>\n\nУтро в Бикини Боттом омрачено: Губка Боб не вышел на смену! Дома пусто, на плите стынут оладушки. Нужно срочно начать масштабное расследование!", [C2("🔍 Начать поиски", "square_start")])

add2("square_start", "🏠 <b>Дом Спанч Боба (Ананас)</b>\n\nВнутри тихо. Гэри грустно мяукает у входной двери.", [
    C2("🔎 Осмотреть комнату Боба", "s2_room"),
    C2("🐌 Поговорить с Гэри", "s2_gary"),
    C2("🌿 Осмотреть сад и лужайку", "s2_yard"),
    C2("🪼 Отправиться в Медузьи поля", "s2_fields", all=["s2_net"], hint="Нужна улика из комнаты Боба"),
    C2("🏴‍☠️ Проверить бухту Летучего Голландца", "s2_bay"),
    C2("📋 Назвать точное место похищения", "s2_theory", clues=4)
], cp=True)

add2("s2_room", "В комнате идеальный порядок, но на окне висит зацепка — металлический крюк и клочок желтой пористой ткани.", [
    C2("◀️ Назад в прихожую", "square_start"),
    C2("🧲 Взять крюк с собой", "s2_take_hook")
], flags=["s2_hook"])

add2("s2_take_hook", "Ты аккуратно снимаешь крюк. Похоже на снаряжение пиратов с поверхности.", [C2("◀️ Назад", "square_start")], give="hook")

add2("s2_gary", "Гэри ползает по полу и выкладывает ракушками странный узор, похожий на карту глубин.", [
    C2("◀️ Назад", "square_start"),
    C2("🗺 Забрать карту", "s2_take_map")
], flags=["s2_map"])

add2("s2_take_map", "Ты забираешь карту глубин Бикини Боттом.", [C2("◀️ Назад", "square_start")], give="map")

add2("s2_yard", "На траве возле ананаса обнаружены гигантские следы и капли мыльных пузырей, уходящие на север.", [
    C2("◀️ Назад", "square_start"),
    C2("🫧 Исследовать пузыри", "s2_bubbles")
], flags=["s2_bubble"])

add2("s2_bubbles", "Пузыри пахнут соленым океаном и порохом.", [C2("◀️ Назад", "square_start")], hp=1)

add2("s2_fields", "🪼 <b>Медузьи поля</b>\n\nОгромные луга, наполненные жужжанием медуз.", [
    C2("🥅 Поймать редкую медузу", "s2_catch_jelly"),
    C2("🔍 Искать следы у обрыва", "s2_cliff"),
    C2("◀️ К дому Боба", "square_start")
])

add2("s2_catch_jelly", "Ты пытаешься поймать дикую электрическую медузу!", [C2("◀️ Убежать", "s2_fields")], hp=-1)

add2("s2_cliff", "У обрыва валяется рваный сачок Спанч Боба и банка из-под солений с запиской внутри.", [
    C2("🫙 Достать записку из банки", "s2_read_note"),
    C2("◀️ Назад", "s2_fields")
], flags=["s2_net"], give="jar")

add2("s2_read_note", "В записке каракулями написано: <i>«Он в пещере у Потолка... Спасите...»</i>", [C2("◀️ Назад", "s2_fields")], flags=["s2_note"])

add2("s2_bay", "🏴‍☠️ <b>Призрачная бухта</b>\n\nТуман и призрачный зеленый свет.", [
    C2("👻 Вызвать Летучего Голландца", "s2_ghost_talk"),
    C2("◀️ Назад", "square_start")
])

add2("s2_ghost_talk", "Летучий Голландец: «Твой друг? Ха! Он на моей палубе бардрайки моет!»", [C2("◀️ Назад", "s2_bay")], flags=["s2_ghost"], give="compass")

add2("s2_theory", "📋 <b>Куда отвели Спанч Боба? Сделай выбор!</b>", [
    C2("🏴‍☠️ На корабль к Летучему Голландцу", "s2_end_pirate", all=["s2_ghost"]),
    C2("🧪 В подземную лабораторию Чам Баккет", "s2_end_chum_bot"),
    C2("🪼 В улей Короля Медуз", "s2_end_jelly"),
    C2("🏔 В секретную пещеру на Потолке", "s2_end_hero", all=["s2_net", "s2_bubble", "s2_note"]),
    C2("⚓ На поверхность к людям", "s2_end_surface"),
    C2("🌿 Бросить поиски", "s2_end_bad")
])

add2("s2_end_surface", "🏖 Похитители подняли Боба на сушу в стеклянный аквариум.", end="s2_surface")
add2("s2_end_chum_bot", "🥫 Планктон перепрограммировал Боба в робота-повара.", end="s2_chum_bot")
add2("s2_end_hero", "🏆 Ты нашел пещеру и освободил Спанч Боба! Герой Бикини Боттом!", end="s2_true_hero")
add2("s2_end_pirate", "🏴‍☠️ Боб стал юнгой на корабле призраков на века.", end="s2_pirate")
add2("s2_end_jelly", "🪼 Медузы короновали Боба своим императором.", end="s2_jelly_king")
add2("s2_end_cave", "🦇 Боб ушел в глубокую пещеру и стал отшельником.", end="s2_cave")
add2("s2_end_bad", "💤 Ты забросил поиски, и Боб потерялся навсегда.", end="s2_bad_end")

add2("dead2", "💀 Силы кончились. Возврат к чекпоинту.", fail=True)


# Движок квестов
def new_q():
    return {"node": "intro", "hp": MAX_HP, "inv": [], "flags": [], "cp": "hall", "endings": [], "claimed": [], "started": int(time.time())}

def new_q2():
    return {"node": "intro2", "hp": MAX_HP, "inv": [], "flags": [], "cp": "square_start", "endings": [], "claimed": [], "started": int(time.time())}

async def get_q(uid, table):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(f"SELECT node, hp, inv, flags, checkpoint, endings, claimed, started FROM {table} WHERE user_id = ?", (uid,)) as cur:
            r = await cur.fetchone()
    if not r:
        return None
    return {
        "node": r[0], "hp": r[1], "inv": json.loads(r[2] or "[]"), "flags": json.loads(r[3] or "[]"),
        "cp": r[4] or "hall", "endings": json.loads(r[5] or "[]"), "claimed": json.loads(r[6] or "[]"),
        "started": int(r[7] or time.time()),
    }

async def save_q(uid, q, table):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO {table} (user_id, node, hp, inv, flags, checkpoint, endings, claimed, started) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, q["node"], q["hp"], json.dumps(q["inv"]), json.dumps(q["flags"]), q["cp"], json.dumps(q["endings"]), json.dumps(q["claimed"]), q["started"]),
        )
        await db.commit()

def clue_count(flags, clues_dict):
    return sum(1 for f in flags if f in clues_dict)

def hearts(hp):
    return "❤️" * hp + "🖤" * (MAX_HP - hp)

def choice_ok(ch, q, items_dict, clues_dict):
    if "item" in ch and ch["item"] not in q["inv"]:
        return False, ch.get("hint", f"Нужен предмет")
    for f in ch.get("all", []):
        if f not in q["flags"]:
            return False, ch.get("hint", "Не хватает улик")
    if "clues" in ch and clue_count(q["flags"], clues_dict) < ch["clues"]:
        return False, f"Нужно улик: {ch['clues']}"
    return True, ""

async def apply_node(uid, q, nid, nodes_dict, clues_dict, items_dict, table):
    n = nodes_dict[nid]
    q["node"] = nid
    notes = []
    for f in n.get("flags", []):
        if f not in q["flags"]:
            q["flags"].append(f)
            if f in clues_dict:
                notes.append(f"🧩 Улика: {clues_dict[f]}")
    g = n.get("give")
    if g and g not in q["inv"]:
        q["inv"].append(g)
        notes.append(f"🎒 Предмет: {items_dict.get(g, g)}")
    
    delta = n.get("hp", 0)
    if delta:
        if delta < 0:
            q["hp"] = max(0, q["hp"] + delta)
            notes.append(f"💔 Здоровье {delta}")
        else:
            q["hp"] = min(MAX_HP, q["hp"] + delta)
            notes.append(f"💚 Здоровье +{delta}")

    if n.get("cp"):
        q["cp"] = nid

    if n.get("end"):
        eid = n["end"]
        if eid not in q["endings"]:
            q["endings"].append(eid)
            notes.append(f"✨ Концовка открыта! ({len(q['endings'])})")
        if len(q["endings"]) >= 7 and "quest_final" not in q["claimed"]:
            q["claimed"].append("quest_final")
            elapsed = max(0, int(time.time()) - q["started"])
            reward = calc_reward(elapsed)
            await add_coins(uid, reward, earn=True)
            notes.append(f"🏆 Квест пройден за {fmt_time(elapsed)}! Награда: +{reward} коинов")

    if q["hp"] <= 0 and not n.get("end"):
        q["node"] = "dead2" if table == "quest2" else "dead"
    return notes

def node_text(q, notes, nodes_dict, clues_dict, items_dict):
    n = nodes_dict[q["node"]]
    head = f"{hearts(q['hp'])}   🧩 {clue_count(q['flags'], clues_dict)}   🎒 {len(q['inv'])}\n\n"
    body = n["t"]
    if n.get("end"):
        body += f"\n\n🏁 <b>Концовка открыта!</b>"
    if notes:
        body += "\n\n" + "\n".join(notes)
    return head + body

def node_kb(q, nodes_dict, clues_dict, items_dict, prefix):
    n = nodes_dict[q["node"]]
    rows = []
    for i, ch in enumerate(n["c"]):
        ok, _ = choice_ok(ch, q, items_dict, clues_dict)
        if ok:
            rows.append([InlineKeyboardButton(text=ch["l"], callback_data=f"{prefix}:{ch['to']}")])
        elif not ch.get("hide"):
            rows.append([InlineKeyboardButton(text="🔒 " + ch["l"], callback_data=f"{prefix}l:{i}")])
    if n.get("end") or n.get("fail"):
        rows.append([InlineKeyboardButton(text="↩️ К чекпоинту", callback_data=f"{prefix}cp")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_node(cb, q, nodes_dict, clues_dict, items_dict, prefix, table, notes=None):
    if q["node"] not in nodes_dict:
        q["node"] = q["cp"] if q["cp"] in nodes_dict else list(nodes_dict.keys())[0]
    await show(cb, node_text(q, notes or [], nodes_dict, clues_dict, items_dict), node_kb(q, nodes_dict, clues_dict, items_dict, prefix))


@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    ok, left = await check_quest_cooldown(uid)
    if not ok:
        await callback.answer(f"⏳ Следующий квест можно начать через {fmt_time(left)}", show_alert=True)
        return
    await update_quest_cooldown(uid)
    q = await get_q(uid, "quest")
    if not q:
        q = new_q()
        notes = await apply_node(uid, q, "intro", NODES, CLUES, ITEMS, "quest")
        await save_q(uid, q, "quest")
    await show_node(callback, q, NODES, CLUES, ITEMS, "q", "quest")

@dp.callback_query(F.data.startswith("q:"))
async def q_move(callback: types.CallbackQuery):
    uid = callback.from_user.id
    target = callback.data[2:]
    async with user_locks[uid]:
        q = await get_q(uid, "quest")
        notes = await apply_node(uid, q, target, NODES, CLUES, ITEMS, "quest")
        await save_q(uid, q, "quest")
        await show_node(callback, q, NODES, CLUES, ITEMS, "q", "quest")

@dp.callback_query(F.data == "qcp")
async def q_cp(callback: types.CallbackQuery):
    uid = callback.from_user.id
    q = await get_q(uid, "quest")
    q["hp"] = MAX_HP
    q["node"] = q["cp"]
    await save_q(uid, q, "quest")
    await show_node(callback, q, NODES, CLUES, ITEMS, "q", "quest")


@dp.callback_query(F.data == "story2_start")
async def story2_start(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    ok, left = await check_quest_cooldown(uid)
    if not ok:
        await callback.answer(f"⏳ Следующий квест можно начать через {fmt_time(left)}", show_alert=True)
        return
    await update_quest_cooldown(uid)
    q = await get_q(uid, "quest2")
    if not q:
        q = new_q2()
        notes = await apply_node(uid, q, "intro2", NODES_Q2, CLUES_Q2, ITEMS_Q2, "quest2")
        await save_q(uid, q, "quest2")
    await show_node(callback, q, NODES_Q2, CLUES_Q2, ITEMS_Q2, "q2", "quest2")

@dp.callback_query(F.data.startswith("q2:"))
async def q2_move(callback: types.CallbackQuery):
    uid = callback.from_user.id
    target = callback.data[3:]
    async with user_locks[uid]:
        q = await get_q(uid, "quest2")
        notes = await apply_node(uid, q, target, NODES_Q2, CLUES_Q2, ITEMS_Q2, "quest2")
        await save_q(uid, q, "quest2")
        await show_node(callback, q, NODES_Q2, CLUES_Q2, ITEMS_Q2, "q2", "quest2")

@dp.callback_query(F.data == "q2cp")
async def q2_cp(callback: types.CallbackQuery):
    uid = callback.from_user.id
    q = await get_q(uid, "quest2")
    q["hp"] = MAX_HP
    q["node"] = q["cp"]
    await save_q(uid, q, "quest2")
    await show_node(callback, q, NODES_Q2, CLUES_Q2, ITEMS_Q2, "q2", "quest2")


@dp.callback_query(F.data == "fortune_wheel")
async def fortune_wheel(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Крутить (100 коинов)", callback_data="spin_wheel")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])
    await show(callback, "🎡 <b>Колесо Фортуны</b>", kb)

@dp.callback_query(F.data == "spin_wheel")
async def spin_wheel(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await spend(uid, 100):
        await callback.answer("❌ Мало коинов!", show_alert=True)
        return
    await callback.answer()
    win = random.choices([0, 50, 100, 200, 350], weights=[35, 30, 20, 10, 5], k=1)[0]
    if win:
        await add_coins(uid, win)
        res = f"🎉 Выигрыш: <b>+{win} коинов</b>!"
    else:
        res = "😢 Пусто."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]])
    await show(callback, res, kb)

@dp.callback_query(F.data == "casino")
async def casino(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Ставка 50", callback_data="dice_bet_50"), InlineKeyboardButton(text="🎲 Ставка 100", callback_data="dice_bet_100")],
        [InlineKeyboardButton(text="🎲 Ставка 500", callback_data="dice_bet_500"), InlineKeyboardButton(text="🎲 Ставка 1000", callback_data="dice_bet_1000")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])
    await show(callback, "🎰 <b>Казино (Кости)</b>\n\nВыбери размер своей ставки. Если выпадет 4, 5 или 6 — ты удваиваешь ставку!", kb)

@dp.callback_query(F.data.startswith("dice_bet_"))
async def play_dice_custom(callback: types.CallbackQuery):
    uid = callback.from_user.id
    bet = int(callback.data.split("_")[2])
    if not await spend(uid, bet):
        await callback.answer("❌ Мало коинов для такой ставки!", show_alert=True)
        return
    await callback.answer()
    chat_id = callback.message.chat.id
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    dice = await bot.send_dice(chat_id, emoji="🎲")
    await asyncio.sleep(3)
    val = dice.dice.value
    if val >= 4:
        win_sum = bet * 2
        await add_coins(uid, win_sum)
        res = f"🎉 Выпало {val}! Выигрыш: <b>+{win_sum} коинов</b> (ставка х2)!"
    else:
        res = f"😢 Выпало {val}. Ставка сгорела."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Играть снова", callback_data="casino")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await bot.send_message(chat_id, res, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "game_jelly")
async def game_jelly(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪼", callback_data="jelly_0"), InlineKeyboardButton(text="🪼", callback_data="jelly_1"), InlineKeyboardButton(text="🪼", callback_data="jelly_2")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])
    await show(callback, "🎯 <b>Тир по медузам</b> (цена 30, приз 90)", kb)

@dp.callback_query(F.data.startswith("jelly_"))
async def jelly_shot(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await spend(uid, 30):
        await callback.answer("❌ Мало коинов!", show_alert=True)
        return
    await callback.answer()
    if int(callback.data.split("_")[1]) == random.randint(0, 2):
        await add_coins(uid, 90)
        res = "🎯 Золотая медуза! <b>+90 коинов</b>!"
    else:
        res = "💨 Мимо."
    await show(callback, res, get_back_keyboard())

@dp.callback_query(F.data == "game_rps")
async def game_rps(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪨", callback_data="rps_rock"), InlineKeyboardButton(text="✂️", callback_data="rps_scissors"), InlineKeyboardButton(text="📄", callback_data="rps_paper")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])
    await show(callback, "✂️ <b>КНБ с Патриком</b> (ставка 40)", kb)

@dp.callback_query(F.data.startswith("rps_"))
async def rps_play(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await spend(uid, 40):
        await callback.answer("❌ Мало коинов!", show_alert=True)
        return
    await callback.answer()
    me, pat = callback.data.split("_")[1], random.choice(["rock", "scissors", "paper"])
    beats = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
    if me == pat:
        await add_coins(uid, 40)
        res = "🤝 Ничья."
    elif beats[me] == pat:
        await add_coins(uid, 80)
        res = "🎉 Победа! <b>+80 коинов</b>"
    else:
        res = "😢 Патрик выиграл."
    await show(callback, res, get_back_keyboard())

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(callback: types.CallbackQuery):
    uid = callback.from_user.id
    now = int(time.time())
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        last = row[0] if row else 0
        if uid != OWNER_ID and now - last < 86400:
            await callback.answer("⏳ Бонус раз в 24 часа!", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance + 250, last_bonus = ? WHERE user_id = ?", (now, uid))
        await db.commit()
    await callback.answer()
    await show(callback, "🎁 Бонус: <b>+250 коинов</b>!", get_back_keyboard())

@dp.callback_query(F.data == "top")
async def top(callback: types.CallbackQuery):
    await callback.answer()
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 5") as cur:
            rows = await cur.fetchall()
    text = "🏆 <b>Топ-5 игроков:</b>\n\n" + "\n".join(f"{i}. {escape(str(r[0]))} — <b>{r[1]} коинов</b>" for i, r in enumerate(rows, 1))
    await show(callback, text, get_back_keyboard())


async def main():
    logging.basicConfig(level=logging.INFO)
    await db_start()
    dp.message.outer_middleware(SubscriptionMiddleware())
    dp.callback_query.outer_middleware(SubscriptionMiddleware())
    print("Бот со всеми огромными квестами запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
