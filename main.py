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

# ВАЖНО: токен храни в переменной окружения BOT_TOKEN, а не в коде.
TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")
OWNER_ID = 7605961809
CHANNEL_ID = -1003932211788  # канал для обязательной подписки (бот должен быть админом)

# База лежит в /app/data (если папка есть на хостинге) — так она переживает пересборку
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
                referred_by INTEGER,
                earned INTEGER DEFAULT 0
            )
        """)
        # Старая таблица квеста (один квест). Оставлена только для переноса данных.
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
        # Новая таблица: сохранение на каждый квест отдельно
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quests (
                user_id INTEGER,
                qid TEXT,
                node TEXT,
                hp INTEGER,
                inv TEXT,
                flags TEXT,
                checkpoint TEXT,
                endings TEXT,
                claimed TEXT,
                started INTEGER,
                PRIMARY KEY (user_id, qid)
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS work_cd (user_id INTEGER, job TEXT, last_ts INTEGER, PRIMARY KEY (user_id, job))")
        for sql in (
            "ALTER TABLE users ADD COLUMN earned INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN last_quest INTEGER DEFAULT 0",
            "ALTER TABLE quest ADD COLUMN started INTEGER",
        ):
            try:
                await db.execute(sql)
            except Exception:
                pass
        # Перенос старых сохранений «Тайны формулы» в новую таблицу
        try:
            await db.execute(
                "INSERT OR IGNORE INTO quests (user_id, qid, node, hp, inv, flags, checkpoint, endings, claimed, started) "
                "SELECT user_id, 'formula', node, hp, inv, flags, checkpoint, endings, claimed, started FROM quest"
            )
        except Exception as e:
            logging.warning("Перенос сохранений квеста: %s", e)
        await db.commit()


async def register_user(user_id: int, username: str, full_name: str):
    safe_name = "".join(c for c in full_name if c.isalnum() or c in " _-").strip() or "Без имени"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, balance, jobs_done, last_work, last_bonus, story_status, earned) "
            "VALUES (?, ?, 500, 0, 0, 0, 'not_started', 0)",
            (user_id, safe_name),
        )
        await db.commit()


async def add_coins(user_id: int, amount: int, earn: bool = False):
    """earn=True — сумма идёт в зачёт розыгрыша (работы и квест)."""
    async with aiosqlite.connect(DB_FILE) as db:
        if earn:
            await db.execute("UPDATE users SET balance = balance + ?, earned = earned + ? WHERE user_id = ?", (amount, amount, user_id))
        else:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def spend(user_id: int, amount: int) -> bool:
    """Атомарно списывает коины. False — если не хватает."""
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


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
        # Если бот не админ в канале, не блокируем всех, но пишем ошибку в лог
        logging.error("Проверка подписки не удалась (бот админ в канале?): %s", e)
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
        text = "🔒 Чтобы пользоваться ботом и участвовать в розыгрыше, подпишись на наш канал, а потом нажми «Я подписался»."
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
            InlineKeyboardButton(text="📖 Эпичные квесты", callback_data="story_start"),
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
    bet_wait.discard(message.from_user.id)
    await message.answer("Главное меню:", reply_markup=get_main_menu())


@dp.callback_query(F.data == "back_menu")
async def back_menu(callback: types.CallbackQuery):
    await callback.answer()
    bet_wait.discard(callback.from_user.id)
    await show(callback, "Главное меню:", get_main_menu())


@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done, earned FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
    balance, jobs, earned = row if row else (500, 0, 0)
    endings = 0
    for qid in QUESTS:
        q = await get_q(uid, qid)
        endings += len(q["endings"]) if q else 0
    text = (
        f"👤 <b>Твой профиль:</b>\n\n💰 Баланс: <b>{balance} коинов</b>\n🛠 Смен: <b>{jobs}</b>\n"
        f"🏆 Заработано для розыгрыша: <b>{earned}</b>\n"
        f"📖 Концовок квестов: <b>{endings}/{len(ENDINGS)}</b>"
    )
    await show(callback, text, get_back_keyboard())


# ======================================================================
#                 РАБОТЫ (КД 4 ЧАСА НА КАЖДУЮ) И РОЗЫГРЫШ
# ======================================================================
WORK_CD = 4 * 3600  # кулдаун каждой работы, секунд

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

sessions = {}  # активные смены: user_id -> состояние


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
        return  # смена уже закончена (защита от двойной выплаты)
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


# ---------------------------- меню работ ----------------------------
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
    text = (
        "💼 <b>Работа</b>\n\nУ каждой работы свой таймер: следующая смена на той же работе будет через 4 часа. "
        "Задания надо выполнять самому: они на память, реакцию и скорость, а время ограничено.\n\n" + "\n".join(lines)
    )
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
    await set_cd(uid, key)  # кулдаун идёт с начала смены, сбросить его выходом нельзя
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


# ---------------------------- 🍔 Повар (память) ----------------------------
async def start_chef(cb, uid):
    picked = random.sample(LAYERS, 4)
    s = {"job": "chef", "recipe": [k for k, _ in picked], "step": 0}
    sessions[uid] = s
    names = " ➡️ ".join(n for _, n in picked)
    await show(cb, f"🍔 <b>Повар в «Красти Краб»</b>\n\n📜 Запомни рецепт:\n<code>{names}</code>\n\n⏱ Показываю <b>4 секунды</b>...")
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


# ---------------------------- 🛵 Курьер (реакция) ----------------------------
async def start_courier(cb, uid):
    s = {"job": "courier", "state": "wait"}
    sessions[uid] = s
    await show(cb, "🛵 <b>Курьер</b>\n\nЖди заказ у окошка. Нажми кнопку, когда появится 🍔. <b>Не раньше!</b>",
               kb_rows([[btn("⏳ Жди...", "j:a:early")]]))
    await asyncio.sleep(random.uniform(2.5, 7))
    if sessions.get(uid) is not s:
        return
    s["state"] = "go"
    await show(cb, "🍔 <b>ЗАКАЗ ГОТОВ!</b> Хватай скорее!", kb_rows([[btn("🍔 ЗАБРАТЬ!", "j:a:go")]]))
    s["t"] = time.time()


async def courier_answer(cb, uid, s, arg):
    if arg == "early" or s.get("state") != "go":
        await finish_job(cb, uid, s, False, fail_text="Ты нажал слишком рано, заказ ещё не был готов.")
    elif time.time() - s["t"] > 3.0:
        await finish_job(cb, uid, s, False, fail_text="Слишком медленно, клиент ушёл.")
    else:
        await finish_job(cb, uid, s, True, ok_text="Заказ доставлен вовремя!")


# ---------------------------- 🧽 Мойщик посуды (последовательность) ----------------------------
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
        await finish_job(cb, uid, s, False, fail_text="Перепутал порядок, посуда разбита.")
        return
    s["pos"] += 1
    if s["pos"] >= len(s["seq"]):
        await finish_job(cb, uid, s, True, ok_text="Вся посуда сверкает!")
    else:
        await dishes_ask(cb, s)


# ---------------------------- 🪼 Ловец медуз (тайминг) ----------------------------
JELLY_CELLS = 8


def jelly_render(s):
    zone = (s["target"], s["target"] + 1)
    cells = []
    for i in range(JELLY_CELLS):
        if i == s["pos"]:
            cells.append("🪼")
        elif i in zone:
            cells.append("🥅")
        else:
            cells.append("🌊")
    return " ".join(cells)


async def start_jelly(cb, uid):
    s = {"job": "jelly", "pos": 0, "target": random.randint(2, 5)}
    sessions[uid] = s
    kb = kb_rows([[btn("🥅 ЛОВИ!", "j:a:catch")]])
    for pos in range(JELLY_CELLS):
        s["pos"] = pos
        await show(cb, f"🪼 <b>Ловец медуз</b>\n\nЖми «ЛОВИ!», когда 🪼 заплывёт в 🥅 (две клетки сачка).\n\n{jelly_render(s)}", kb)
        await asyncio.sleep(1.5)
        if sessions.get(uid) is not s:
            return
    await finish_job(cb, uid, s, False, fail_text="Медуза уплыла.")


async def jelly_answer(cb, uid, s, arg):
    if s["target"] <= s["pos"] <= s["target"] + 1:
        await finish_job(cb, uid, s, True, ok_text="Медуза в сачке!")
    else:
        await finish_job(cb, uid, s, False, fail_text="Сачок пуст: ты нажал не вовремя.")


# ---------------------------- 💵 Кассир (быстрый счёт) ----------------------------
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
    await show(cb, f"💵 <b>Кассир — клиент {s['round'] + 1} из 2</b>\n\n{order}\n\nКлиент даёт <b>{paid}</b>. Сколько сдачи? У тебя 12 секунд.", kb_rows(rows))


async def start_cashier(cb, uid):
    s = {"job": "cashier", "round": 0}
    sessions[uid] = s
    await cashier_show(cb, s)


async def cashier_answer(cb, uid, s, arg):
    if time.time() > s["deadline"] + 1.5:
        await finish_job(cb, uid, s, False, fail_text="Клиент не дождался и ушёл.")
    elif s["opts"][int(arg)] != s["change"]:
        await finish_job(cb, uid, s, False, fail_text="Неверная сдача, клиент возмущён.")
    else:
        s["round"] += 1
        if s["round"] >= 2:
            await finish_job(cb, uid, s, True, ok_text="Оба клиента довольны!")
        else:
            await cashier_show(cb, s)


# ---------------------------- 🕵️ Охранник (внимание) ----------------------------
GUARD_PAIRS = [("🦀", "🦞"), ("😀", "😃"), ("🐟", "🐠"), ("🔴", "🟠"), ("🐙", "🦑"), ("🌕", "🌝")]


async def guard_show(cb, s):
    a, b = random.choice(GUARD_PAIRS)
    if random.random() < 0.5:
        a, b = b, a
    odd = random.randrange(16)
    s["odd"] = odd
    s["deadline"] = time.time() + 8
    rows = [[btn(b if r * 4 + c == odd else a, f"j:a:{r * 4 + c}") for c in range(4)] for r in range(4)]
    await show(cb, f"🕵️ <b>Охранник — обход {s['round'] + 1} из 3</b>\n\nНайди лишнего! У тебя 8 секунд.", kb_rows(rows))


async def start_guard(cb, uid):
    s = {"job": "guard", "round": 0}
    sessions[uid] = s
    await guard_show(cb, s)


async def guard_answer(cb, uid, s, arg):
    if time.time() > s["deadline"] + 1.5:
        await finish_job(cb, uid, s, False, fail_text="Время вышло, нарушитель скрылся.")
    elif int(arg) != s["odd"]:
        await finish_job(cb, uid, s, False, fail_text="Не тот! Сработала тревога.")
    else:
        s["round"] += 1
        if s["round"] >= 3:
            await finish_job(cb, uid, s, True, ok_text="Все нарушители пойманы!")
        else:
            await guard_show(cb, s)


# ---------------------------- 🧮 Бухгалтер Крабса (память + счёт) ----------------------------
async def start_acc(cb, uid):
    nums = [random.randint(12, 59) for _ in range(4)]
    s = {"job": "acc", "total": sum(nums)}
    sessions[uid] = s
    shown = "  •  ".join(str(n) for n in nums)
    await show(cb, f"🧮 <b>Бухгалтер Крабса</b>\n\nЗапомни числа:\n\n<b>{shown}</b>\n\n⏱ Показываю <b>5 секунд</b>, потом спрошу сумму.")
    await asyncio.sleep(5)
    if sessions.get(uid) is not s:
        return
    s["opts"] = make_opts(s["total"], [-1, 1, -2, 2, -10, 10, -11, 11], 4)
    s["deadline"] = time.time() + 20
    rows = [[btn(str(s["opts"][r * 2 + c]), f"j:a:{r * 2 + c}") for c in range(2)] for r in range(2)]
    await show(cb, "🧮 Чему равна сумма всех четырёх чисел? У тебя 20 секунд.", kb_rows(rows))


async def acc_answer(cb, uid, s, arg):
    if time.time() > s["deadline"] + 1.5:
        await finish_job(cb, uid, s, False, fail_text="Время вышло, Крабс недоволен.")
    elif s["opts"][int(arg)] != s["total"]:
        await finish_job(cb, uid, s, False, fail_text="Ошибка в отчёте! Крабс в ярости.")
    else:
        await finish_job(cb, uid, s, True, ok_text="Отчёт сошёлся до копейки!")


STARTERS = {"chef": start_chef, "courier": start_courier, "dishes": start_dishes, "jelly": start_jelly,
            "cashier": start_cashier, "guard": start_guard, "acc": start_acc}
ANSWERS = {"chef": chef_answer, "courier": courier_answer, "dishes": dishes_answer, "jelly": jelly_answer,
           "cashier": cashier_answer, "guard": guard_answer, "acc": acc_answer}


# ---------------------------- 🏆 Розыгрыш ----------------------------
async def contest_text(uid):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT username, earned FROM users WHERE user_id != ? AND earned > 0 ORDER BY earned DESC LIMIT 10", (OWNER_ID,)
        ) as cur:
            rows = await cur.fetchall()
        async with db.execute("SELECT earned FROM users WHERE user_id = ?", (uid,)) as cur:
            me = await cur.fetchone()
        mine = me[0] if me else 0
        async with db.execute("SELECT COUNT(*) FROM users WHERE user_id != ? AND earned > ?", (OWNER_ID, mine)) as cur:
            ahead = (await cur.fetchone())[0]
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, earned) in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{mark} {escape(str(name))} — <b>{earned}</b>")
    top_text = "\n".join(lines) or "Пока никто не заработал. Будь первым!"
    text = (
        "🏆 <b>Розыгрыш</b>\n\nПобеждают <b>3 игрока</b>, которые заработают больше всех. "
        "В зачёт идут только зарплата с работ и итоговая награда квестов. Казино, колесо и бонусы не считаются.\n\n"
        f"<b>Топ-10:</b>\n{top_text}"
    )
    if uid != OWNER_ID:
        place = f"{ahead + 1} место" if mine > 0 else "ты пока вне рейтинга"
        text += f"\n\n👤 Ты: <b>{mine}</b> ({place})"
    return text


@dp.callback_query(F.data == "contest")
async def contest_cb(callback: types.CallbackQuery):
    await callback.answer()
    kb = kb_rows([[btn("💼 К работам", "work_menu")], [btn("◀️ Назад в меню", "back_menu")]])
    await show(callback, await contest_text(callback.from_user.id), kb)


@dp.message(Command("contest"))
async def contest_cmd(message: types.Message):
    await message.answer(await contest_text(message.from_user.id), parse_mode="HTML")


@dp.message(Command("contest_winners"))
async def contest_winners(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT user_id, username, earned FROM users WHERE user_id != ? AND earned > 0 ORDER BY earned DESC LIMIT 3", (OWNER_ID,)
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await message.answer("Пока нет участников.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i]} <a href=\"tg://user?id={uid}\">{escape(str(name))}</a> — {earned} (id <code>{uid}</code>)" for i, (uid, name, earned) in enumerate(rows)]
    await message.answer("🏆 <b>Победители:</b>\n\n" + "\n".join(lines), parse_mode="HTML")


@dp.message(Command("contest_reset"))
async def contest_reset(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET earned = 0")
        await db.commit()
    await message.answer("✅ Счётчик розыгрыша обнулён. Можно запускать новый розыгрыш.")


# ======================================================================
#                              КВЕСТЫ
#   Данные квестов — это граф узлов. Чтобы добавить сцену, просто
#   добавь add("id", "текст", [C("кнопка", "куда")], ...)
#   Перед блоком сцен нужного квеста ставится CUR_Q = "id квеста".
#
#   Параметры узла: flags=[...] (улики/метки), give="предмет", hp=±N,
#   cp=True (чекпоинт), end="id концовки", fail=True
#   Параметры кнопки: item="предмет", all=[флаги], clues=N,
#   hint="подсказка при блокировке", hide=True (прятать, пока закрыто)
# ======================================================================
MAX_HP = 5

# Награда за прохождение квеста (за все 7 его концовок):
# до 15 минут с первого запуска — максимум, дальше -1000 за каждую начатую минуту, но не меньше минимума.
FINAL_MAX = 15000
FINAL_MIN = 4000
FINAL_FREE_SEC = 15 * 60
FINAL_STEP = 1000
QUEST_COOLDOWN = 24 * 3600  # новый квест можно начать раз в сутки

REWARD_TEXT = (
    f"🏆 <b>Награда одна, за все 7 концовок квеста.</b> Время считается с первого запуска:\n"
    f"до 15 мин — <b>{FINAL_MAX}</b>, дальше −{FINAL_STEP} за каждую начатую минуту, но не меньше <b>{FINAL_MIN}</b>."
)


def final_reward(elapsed):
    if elapsed <= FINAL_FREE_SEC:
        return FINAL_MAX
    minutes_over = -(-(elapsed - FINAL_FREE_SEC) // 60)  # округление вверх
    return max(FINAL_MIN, FINAL_MAX - minutes_over * FINAL_STEP)


ITEMS = {
    # Тайна формулы
    "lupa": "🔎 Лупа",
    "drone": "🚁 Дрон «Белка-1»",
    "note": "🗒 Записка из мусорки",
    "blueprint": "📐 Чертёж робо-ноги",
    "napkin": "🧻 Салфетка Патрика",
    # Похищение Губки Боба
    "b_lupa": "🔎 Лупа Сэнди",
    "b_flash": "🔦 Фонарик Боба",
    "b_jar": "🥒 Банка маринада №7",
    "b_badge": "🎖 Значок «Почётный помощник»",
    "b_key": "🗝 Ключ от подвала",
    "b_radio": "📻 Рация Сэнди",
}

CLUES_F = {
    "c_lock": "🔐 Царапины на замке сейфа",
    "c_fuel": "🧪 Водорослевое топливо (анализ Сэнди)",
    "c_inside": "🪟 Окно открыто ИЗНУТРИ",
    "c_policy": "📄 Свежая страховка на формулу",
    "c_print": "🖐 Металлический отпечаток на фритюре",
    "c_witness": "🗣 Показания Губки Боба",
    "c_snore": "😴 Храп с присвистом",
    "c_pink": "🩷 Розовые липкие следы",
    "c_patrick": "🪨 Патрик и странная салфетка",
    "c_tracks": "🛞 Следы гусениц к Чам Баккет",
    "c_note": "🗒 Записка «Ровно в 3:00… — К.»",
    "c_cam": "📹 Запись камеры в 3:00",
    "c_squid_clear": "🦑 Сквидвард не при делах",
    "c_fuelmatch": "⛽ Топливо в баке Планктона совпало",
    "c_plankton_alibi": "🧪 Признание Планктона",
    "c_plankton_saw": "👀 Планктон видел розовую фигуру и красную клешню",
}

CLUES_B = {
    "c_door": "🚪 Дверь снята с петель аккуратно, не выломана",
    "c_slime": "💧 Капли рассола на дверной ручке",
    "c_ransom": "📨 Записка с требованием выкупа",
    "c_label": "🏷 Буквы вырезаны с этикетки «Маринад №7»",
    "c_gary": "🐌 Гэри показал, куда ушёл похититель",
    "c_boots": "👢 Огромный отпечаток ботинка у плиты",
    "c_bubble_visit": "🥒 Пузырь-Бандит вчера поссорился с Бобом из-за маринадов",
    "c_krabs_refuse": "🦀 Крабс не собирается платить выкуп",
    "c_plankton_alibi": "🪣 У Планктона железное алиби (он сидит в бочке)",
    "c_squid_alibi": "🎷 У Сквидварда алиби: жалобы соседей на кларнет",
    "c_mermaid_alibi": "🦸 У героев алиби: они спали по расписанию",
    "c_receipt": "🧾 Чек на 300 банок «Маринада №7»",
    "c_cannery": "🏭 Следы ведут к заброшенному консервному заводу",
    "c_radio": "📻 Перехват рации: «груз на складе»",
    "c_cam_b": "📹 Запись камеры: здоровяк несёт мешок с Бобом",
    "c_diary": "📓 Дневник Пузыря: «Хочу друзей»",
}

ENDINGS_F = {
    "hasty": "Поспешный арест",
    "squid": "Невинная жертва кларнета",
    "plankton": "Правосудие по Планктону",
    "patrick": "Сон, в котором была формула",
    "merger": "Красти-Чам Корп",
    "truth_mercy": "Прощённый Крабс",
    "truth_justice": "Крабс за решёткой",
}

ENDINGS_B = {
    "b_hasty": "Планктон в наручниках (зря)",
    "b_squid": "Оскорблённый музыкант",
    "b_heroes": "Напарник героев",
    "b_ransom": "Деньги на ветер",
    "b_mercy": "Прощённый Пузырь",
    "b_justice": "Пузырь за решёткой",
    "b_pickle": "Маринадная вечеринка",
}

CLUES_BY_Q = {"formula": CLUES_F, "bob": CLUES_B}
ENDINGS_BY_Q = {"formula": ENDINGS_F, "bob": ENDINGS_B}
CLUES = {**CLUES_F, **CLUES_B}
ENDINGS = {**ENDINGS_F, **ENDINGS_B}

QUESTS = {
    "formula": {
        "title": "Тайна формулы", "icon": "🔐", "start": "intro", "hub": "hall", "dead": "dead",
        "desc": "Из сейфа «Красти Краб» исчезла секретная формула крабсбургера. Кто же вор?",
    },
    "bob": {
        "title": "Похищение Губки Боба", "icon": "🧽", "start": "b_intro", "hub": "b_hall", "dead": "b_dead",
        "desc": "Губка Боб пропал, а на столе лежит записка с требованием выкупа. Найди похитителя и спаси друга!",
    },
}

NODES = {}
CUR_Q = "formula"


def add(nid, text, choices=(), **kw):
    NODES[nid] = {"t": text, "c": list(choices), "q": CUR_Q, **kw}


def C(label, to, **kw):
    return {"l": label, "to": to, **kw}


# ======================================================================
#                      КВЕСТ 1: «ТАЙНА ФОРМУЛЫ»
# ======================================================================
CUR_Q = "formula"

# ---------------------------- Пролог и главный зал ----------------------------
add("intro",
    "🌊 <b>Пролог. Дело о пропавшей формуле</b>\n\n"
    "Утро в Бикини Боттом. У «Красти Краб» — полицейская лента, толпа зевак и вопли мистера Крабса: "
    "ночью из сейфа исчезла <b>секретная формула крабсбургера</b>.\n\n"
    "Полиция разводит плавниками, поэтому Крабс нанял тебя — лучшего (и единственного свободного) детектива города.\n\n"
    "<i>Прогресс сохраняется автоматически: можно закрыть бот и вернуться в любой момент. "
    "Всего в квесте 7 концовок. Награда одна, за все 7: до 15 минут — 15000 коинов, дальше −1000 за каждую минуту (но не меньше 4000).</i>",
    [C("🚪 Приступить к расследованию", "hall")])

add("hall",
    "🏠 <b>Главный зал «Красти Краб»</b>\n\nЗапах жареных котлет смешался с запахом паники. "
    "Куда отправимся? Улики можно искать в любом порядке.",
    [
        C("🔐 Кабинет Крабса", "office"),
        C("🍳 Кухня", "kitchen"),
        C("🌃 Задний переулок", "alley"),
        C("🦀 Поговорить с Крабсом", "krabs_talk"),
        C("🎷 Навестить Сквидварда", "squid_house"),
        C("🧪 Вернуться в Чам Баккет", "chum_gate", all=["c_tracks"], hide=True),
        C("📋 Назвать виновного (нужно 4 улики)", "theory", clues=4),
    ], cp=True)

# ---------------------------- Кабинет ----------------------------
add("office",
    "🔐 <b>Кабинет Крабса</b>\n\nСейф нараспашку, на полу блестят монеты. Странно: вор не взял ни одной. "
    "Окно приоткрыто, стол в беспорядке.",
    [
        C("🔐 Изучить замок сейфа", "lock_check"),
        C("🪟 Осмотреть окно", "window"),
        C("🗄 Порыться в столе", "desk"),
        C("◀️ В зал", "hall"),
    ])

add("lock_check",
    "На дверце сейфа — глубокие царапины, а в них блестят капельки зеленоватого топлива с запахом водорослей. "
    "Образец стоит показать эксперту.",
    [C("🌳 Отвезти образец Сэнди", "sandy_lab"), C("◀️ Назад", "office")],
    flags=["c_lock"])

add("sandy_lab",
    "🌳 <b>Купол Сэнди</b>\n\nБелка в скафандре сует образец в спектрометр.\n"
    "— Био-топливо из водорослей, партнёр! Такое жгут только роботы из Чам Баккет. "
    "Но слишком уж очевидно... Держи лупу, пригодится.",
    [C("🛠 Расспросить про гаджеты", "sandy_advice"), C("◀️ В ресторан", "hall")],
    flags=["c_fuel"], give="lupa")

add("sandy_advice",
    "Сэнди с гордостью вручает мини-дрон «Белка-1».\n— Достанет камеру, куда сам не залезешь. "
    "Только не трогай красную кнопку!",
    [C("◀️ В ресторан", "hall")], give="drone")

add("window",
    "Рама окна цела, следов взлома нет, а защёлка отодвинута <b>изнутри</b>. "
    "Либо вор уже был в здании, либо ему кто-то открыл.",
    [C("🌃 Выглянуть наружу", "alley"), C("◀️ Назад", "office")],
    flags=["c_inside"])

add("desk",
    "В ящике стола — свежий страховой полис. Дата подписи — <b>вчера</b>. "
    "Почему-то Крабс застраховал формулу за день до кражи.",
    [C("📄 Прочитать мелкий шрифт", "policy"), C("◀️ Назад", "office")])

add("policy",
    "Мелкий шрифт: выплата полагается, если формулу «похитят при взломе». "
    "Сумма с шестью нулями. Подпись: Юджин Х. Крабс. Мило.",
    [C("◀️ Назад", "office")], flags=["c_policy"])

# ---------------------------- Кухня ----------------------------
add("kitchen",
    "🍳 <b>Кухня</b>\n\nГубка Боб оттирает гриль и тихо всхлипывает. Фритюрница в царапинах, холодильник распахнут.",
    [
        C("🍟 Осмотреть фритюрницу", "fryer"),
        C("🧽 Поговорить с Губкой Бобом", "bob_talk"),
        C("🧊 Заглянуть в холодильник", "fridge"),
        C("◀️ В зал", "hall"),
    ])

add("fryer",
    "На ручке фритюрницы виден жирный отпечаток. Невооружённым глазом разобрать его невозможно.",
    [C("🔎 Снять отпечаток лупой", "print", item="lupa", hint="Нужна лупа (её даёт Сэнди)"),
     C("◀️ Назад", "kitchen")])

add("print",
    "Под лупой видно: след оставила не клешня и не рука, а <b>металлическое сочленение</b>. "
    "Робо-нога? Явно не мистер Крабс.",
    [C("◀️ Назад", "kitchen")], flags=["c_print"])

add("bob_talk",
    "— Я ночевал на кухне! — Боб теребит колпак. — Слышал шаги, потом бульканье... и храп!",
    [C("😴 Расспросить про храп", "bob_snore"), C("🤗 Утешить Боба", "bob_comfort"), C("◀️ Назад", "kitchen")],
    flags=["c_witness"])

add("bob_snore",
    "— Громкий, с присвистом, и всё время бормотал «ммм... желе...». Но чей — не могу вспомнить!",
    [C("◀️ Назад", "kitchen")], flags=["c_snore"])

add("bob_comfort",
    "Боб вручает тебе тёплый крабсбургер: «Для друга!» Силы возвращаются.",
    [C("◀️ Назад", "kitchen")], hp=1)

add("fridge",
    "Холодильник вылизан до блеска: всё желе съедено. На дверце — липкие <b>розовые</b> отпечатки ладоней.",
    [C("👣 Идти по розовым следам", "pink_trail"), C("◀️ Назад", "kitchen")], flags=["c_pink"])

add("pink_trail",
    "Следы ведут через заднюю дверь, через двор и дальше — к знакомому камню на соседней улице.",
    [C("🪨 Подойти к камню", "patrick_rock"), C("◀️ Назад", "kitchen")])

add("patrick_rock",
    "🪨 <b>Камень Патрика</b>\n\nИз-под камня доносится храп с присвистом. Совпадает с рассказом Боба?",
    [C("🤫 Тихо заглянуть под камень", "rock_search"), C("📢 Разбудить Патрика", "wake_patrick"), C("◀️ Уйти", "hall")])

add("rock_search",
    "Патрик спит, обнимая салфетку, исписанную неразборчивыми значками. Ты осторожно вытягиваешь её. "
    "— Желе... формула... вкусно... — бормочет он.",
    [C("◀️ В ресторан", "hall")], give="napkin", flags=["c_patrick"])

add("wake_patrick",
    "Патрик спросонья бьёт тебя подушкой-камнем: «Это был мой сон!» Ты отлетаешь на три метра.",
    [C("🤕 Отряхнуться", "patrick_rock")], hp=-1)

# ---------------------------- Переулок ----------------------------
add("alley",
    "🌃 <b>Задний переулок</b>\n\nФонарь мигает, у стены — мусорный бак, на асфальте — странные следы.",
    [
        C("🛞 Изучить следы на асфальте", "tracks"),
        C("🗑 Осмотреть мусорный бак", "dumpster"),
        C("💡 Осмотреть фонарный столб", "lamp"),
        C("◀️ В ресторан", "hall"),
    ])

add("tracks",
    "Крошечные гусеницы с металлическими отпечатками тянутся прочь от ресторана — в сторону Чам Баккет.",
    [C("🧪 Пойти к Чам Баккет", "chum_gate"), C("◀️ Назад", "alley")], flags=["c_tracks"])

add("dumpster",
    "Вонь стоит страшная. На дне бака что-то белеет.",
    [C("🗑 Нырнуть в бак", "dumpster_dive"), C("◀️ Назад", "alley")])

add("dumpster_dive",
    "Ты весь в соусе, зато нашёл скомканную записку: <i>«Ровно в 3:00. Окно открою изнутри. — К.»</i>",
    [C("◀️ Назад", "alley")], hp=-1, give="note", flags=["c_note"])

add("lamp",
    "На столбе, высоко над землёй, висит камера наблюдения. Вручную не достать.",
    [C("🚁 Достать запись дроном", "cam_hack", item="drone", hint="Нужен дрон (спроси Сэнди)"),
     C("◀️ Назад", "alley")])

add("cam_hack",
    "Дрон подключился к панели камеры. Экран требует код:\n\n<b>(50 − 14) ÷ 4 = ?</b>",
    [C("6", "cam_wrong"), C("9", "cam_ok"), C("12", "cam_wrong")])

add("cam_wrong",
    "Бззз! Дрон бьёт током. Придётся попробовать ещё раз.",
    [C("🔁 Ещё раз", "cam_hack")], hp=-1)

add("cam_ok",
    "Код принят! Запись 3:00: в окне кабинета горит свет, кто-то изнутри открывает раму. "
    "Со двора к окну подкрадывается тень на металлических ногах. А у окна стоит силуэт с острыми клешнями — "
    "слишком крупный для Планктона.",
    [C("◀️ Назад", "alley")], flags=["c_cam"])

# ---------------------------- Крабс и Сквидвард ----------------------------
add("krabs_talk",
    "🦀 <b>Мистер Крабс</b>\n\n— Мои деньги! Мой бизнес! Сделайте хоть что-нибудь! — Он носится по залу, не выпуская из клешни калькулятор.",
    [
        C("🌙 Спросить, где он был ночью", "krabs_night"),
        C("📄 Спросить про страховку", "krabs_policy", all=["c_policy"], hint="Сначала найди страховой полис"),
        C("💰 Попросить аванс", "krabs_advance"),
        C("◀️ Назад", "hall"),
    ])

add("krabs_night",
    "— Дома! Спал! — слишком быстро отвечает Крабс. Он нервно теребит клешню и выглядит так, будто не спал всю ночь.",
    [C("◀️ Назад", "krabs_talk")])

add("krabs_policy",
    "Крабс бледнеет: «Страховка? Простая формальность!» — и резко начинает рассказывать про цены на булочки.",
    [C("◀️ Назад", "krabs_talk")], flags=["krabs_sweat"])

add("krabs_advance",
    "Крабс хватается за сердце: «Аванс?! Аванс — это миф, придуманный лентяями!» И закрывает кассу на замок.",
    [C("◀️ Назад", "krabs_talk")])

add("squid_house",
    "🎷 <b>Дом Сквидварда</b>\n\n— Я спал. Уходите. Я сыграю тише, если вы уйдёте, — бурчит Сквидвард, не отрываясь от кларнета.",
    [C("🦯 Сравнить трость с царапинами", "squid_cane"), C("◀️ Уйти", "hall")])

add("squid_cane",
    "Диаметр трости не подходит к царапинам на сейфе. Сквидвард чист — хотя приятнее он от этого не стал.",
    [C("◀️ Уйти", "hall")], flags=["c_squid_clear"])

# ---------------------------- Чам Баккет ----------------------------
add("chum_gate",
    "🧪 <b>Чам Баккет</b>\n\nНад входом мигает вывеска «ЛУЧШИЕ ЦЕНЫ В ГОРОДЕ». На страже — Карен. "
    "Как проникнем?",
    [
        C("🌬 Через вентиляцию", "vent"),
        C("🚪 С парадного входа", "front_door"),
        C("📣 Вызвать Планктона на разговор", "plankton_call"),
        C("◀️ Назад в переулок", "alley"),
    ], cp=True)

add("vent",
    "Вентиляция узкая и пыльная. Впереди развилка.",
    [C("⬅️ Налево", "lab_floor"), C("➡️ Направо", "vent_fail")])

add("vent_fail",
    "Тупик. Пыль, чих — и ты съезжаешь обратно к воротам.",
    [C("🤧 Отряхнуться", "chum_gate")], hp=-1)

add("front_door",
    "— Покупатель!!! — Планктон выпрыгивает из-за стойки. — Закажи Чам-бургер! Всего один раз!",
    [C("🍔 Съесть Чам-бургер", "chum_burger"), C("😤 Потребовать самого Планктона", "plankton_call")])

add("chum_burger",
    "Ты героически съел это. Планктон растроган до слёз: «Никто и никогда!» Теперь он тебе доверяет. "
    "А у тебя бурлит в животе.",
    [C("➡️ К Планктону", "plankton_call")], hp=-2, flags=["plankton_trust"])

add("lab_floor",
    "🔬 <b>Лаборатория Планктона</b>\n\nЧертежи, колбы, ящик с запчастями и большой бак с топливом.",
    [
        C("📐 Взять чертёж со стола", "blueprint"),
        C("⛽ Проверить бак с топливом", "fuel_tank"),
        C("🤫 Затаиться и подслушать", "eavesdrop"),
        C("◀️ К воротам", "chum_gate"),
    ])

add("blueprint",
    "Чертёж робо-ноги Карен. Кто-то обвёл красным маркером окно на втором этаже и приписал: «3:00».",
    [C("◀️ Назад", "lab_floor")], give="blueprint")

add("fuel_tank",
    "Тот же запах водорослей и тот же состав, что на замке сейфа. Топливо из этого бака — прямая улика против Чам Баккет.",
    [C("◀️ Назад", "lab_floor")], flags=["c_fuelmatch"])

add("eavesdrop",
    "Из-за двери доносится голос Планктона:\n— Да не брал я формулу! Мне кто-то подбросил чертёж и записку! "
    "Я пришёл, а сейф уже был пуст!",
    [C("🙋 Выйти из укрытия", "plankton_call")], flags=["plankton_overheard"])

add("plankton_call",
    "🧪 <b>Шелдон Планктон</b>\n\n— Опять ты?! Это не я! В кои-то веки не я!",
    [
        C("🔦 Допросить", "interrogate"),
        C("🤝 Предложить союз", "end_merger", all=["plankton_trust", "c_plankton_alibi"],
          hint="Нужно доверие Планктона (Чам-бургер) и его признание"),
        C("⛓ Арестовать прямо сейчас", "end_hasty"),
        C("◀️ К воротам", "chum_gate"),
    ])

add("interrogate",
    "Как будешь говорить с Планктоном?",
    [C("😠 Давить", "pressure"), C("😊 Дружелюбно", "friendly"), C("◀️ Назад", "plankton_call")])

add("pressure",
    "Карен включает электрошок. Планктон хохочет, а ты отскакиваешь с дымящимися плавниками.",
    [C("◀️ Назад", "plankton_call")], hp=-1)

add("friendly",
    "Планктон ломается: «Да! Хотел украсть! Но пришёл в 3:00, а сейф уже был пуст!»",
    [C("👂 Слушать дальше", "plankton_story")], flags=["c_plankton_alibi"])

add("plankton_story",
    "— Я видел розовую фигуру, сопящую возле мусорки. А в окне кабинета мелькнула красная клешня. "
    "Сначала я подумал, что мерещится.",
    [C("🏃 Вернуться в Красти Краб", "hall"), C("◀️ Остаться в Чам Баккет", "chum_gate")],
    flags=["c_plankton_saw"])

# ---------------------------- Развязка ----------------------------
add("theory",
    "📋 <b>Развязка</b>\n\nВсе собрались в обеденном зале. Тишина. Вот-вот упадёт монетка. "
    "Кого ты назовёшь виновным?",
    [
        C("🦑 Сквидварда", "acc_squid"),
        C("🧪 Планктона", "acc_plankton"),
        C("🦀 Мистера Крабса", "acc_krabs", all=["c_policy", "c_inside", "c_cam"],
          hint="Нужны улики: страховка, окно изнутри и запись камеры"),
        C("⭐ Патрика", "acc_patrick", item="napkin", hint="Нужна салфетка Патрика"),
        C("🔎 Ещё поищу улики", "hall"),
    ], cp=True)

add("acc_squid",
    "Сквидвард медленно опускает кларнет: «Я? Серьёзно? У меня алиби — моё одиночество».",
    [C("😠 Настаивать", "end_squid"), C("🙇 Извиниться и передумать", "squid_sorry")])

add("squid_sorry",
    "Сквидвард вышвыривает тебя за дверь кларнетом. Ты потираешь шишку и возвращаешься к версиям.",
    [C("◀️ К версиям", "theory")], hp=-1)

add("acc_plankton",
    "Планктон вскакивает на стол: «Опять я?! Это вопиющая несправедливость!»",
    [
        C("📐 Предъявить чертёж и анализ топлива", "plankton_trial", item="blueprint", all=["c_fuelmatch"],
          hint="Нужны чертёж робо-ноги и топливо из бака Планктона"),
        C("🗣 Давить на эмоции", "end_hasty"),
        C("◀️ Передумать", "theory"),
    ])

add("plankton_trial",
    "Судья Наточник изучает чертёж и колбу с топливом. Зал замирает. Улики выглядят весомо.",
    [C("⚖️ Выслушать вердикт", "end_plankton")])

add("acc_krabs",
    "Зал ахает. Крабс бледнеет: «Это клевета! Я потратил на этот ресторан всю жизнь!»",
    [C("📄 Показать страховой полис", "krabs_break1"), C("◀️ Передумать", "theory")])

add("krabs_break1",
    "— Страховка — обычное дело! — пищит Крабс, но на лбу выступает испарина.",
    [
        C("📹 Включить запись камеры", "krabs_break2"),
        C("🗒 Зачитать записку «Ровно в 3:00…»", "krabs_break2", item="note", hint="Нужна записка из мусорки"),
    ])

add("krabs_break2",
    "На записи видно, как Крабс сам открывает окно изнутри, чтобы «ограбить» собственный сейф. "
    "Боб ахает: «Мистер Крабс, но формула же у вас?!»",
    [C("🕵️ Потребовать открыть сейф с двойным дном", "krabs_confess")])

add("krabs_confess",
    "Крабс падает на колени: «Да! Я спрятал формулу в потайной нише, хотел и страховку получить, и рецепт сохранить! "
    "А след на Планктона навёл чертежом!» Что дальше?",
    [C("🤝 Простить Крабса", "end_truth_mercy"), C("🚔 Передать полиции", "end_truth_justice")])

add("acc_patrick",
    "Патрик зевает: «Я просто хотел желе...» Ты разворачиваешь найденную салфетку — на ней вся формула, "
    "записанная детским почерком.",
    [C("📄 Показать всем салфетку", "end_patrick"), C("◀️ Передумать", "theory")])

# ---------------------------- Концовки ----------------------------
add("end_hasty",
    "🚔 Планктона увозят в участок. Назавтра его выпускают за недостатком улик, а формула так и не найдена. "
    "Кажется, ты поторопился...",
    end="hasty")

add("end_squid",
    "⚖️ Сквидвард подал на тебя в суд за клевету, и ты проиграл. Лицензия детектива отозвана. "
    "Зато кларнет теперь играет тише — из злорадства.",
    end="squid")

add("end_plankton",
    "⚖️ Планктона признают виновным и сажают под домашний арест вместе с Карен. Город ликует! "
    "Правда, формулу так и не нашли... Что-то в этой истории осталось недосказанным.",
    end="plankton")

add("end_patrick",
    "🤯 Выясняется: лунатик Патрик залез в окно, которое Крабс открыл для «вора», съел желе, а формулу "
    "записал на салфетке и использовал вместо подушки. Боб восстановил рецепт, Патрик получил пожизненное желе. "
    "Крабс же остался без страховки.",
    end="patrick")

add("end_merger",
    "🤝 Ты убеждаешь Планктона и Крабса объединиться. Рождается «Красти-Чам Корп»: Чам-бургеры с крабсбургерным соусом. "
    "Никто не понял вкус, но очередь стоит до горизонта. Секретная концовка!",
    end="merger")

add("end_truth_mercy",
    "🕊 Ты уговариваешь не сажать Крабса. Он возвращает страховку, извиняется перед Планктоном и повышает Бобу зарплату "
    "(на целый коин). Справедливость торжествует. Настоящая концовка!",
    end="truth_mercy")

add("end_truth_justice",
    "🚔 Крабса уводят в наручниках, а ресторан временно возглавляет Губка Боб. "
    "Страховая выплачивает тебе премию за раскрытие мошенничества. Настоящая концовка!",
    end="truth_justice")

add("dead",
    "💀 <b>Силы кончились</b>\n\nТы вымотан до предела. Придётся перевести дух и вернуться к последнему чекпоинту. "
    "Здоровье восстановится.",
    fail=True)


# ======================================================================
#                 КВЕСТ 2: «ПОХИЩЕНИЕ ГУБКИ БОБА»
# ======================================================================
CUR_Q = "bob"

# ---------------------------- Пролог и ананас ----------------------------
add("b_intro",
    "🍍 <b>Пролог. Похищение Губки Боба</b>\n\n"
    "Раннее утро в Бикини Боттом. Ты ещё не допил водорослевый кофе, как в дверь врывается Патрик:\n"
    "— Губка Боб пропал! Дома никого, на столе записка, а Гэри смотрит на меня как на преступника!\n\n"
    "Без Боба «Красти Краб» не открылся, а значит, нет крабсбургеров. Город в панике. Дело придётся раскрыть тебе.\n\n"
    "<i>Прогресс сохраняется автоматически. Всего в квесте 7 концовок. Награда одна, за все 7: "
    "до 15 минут — 15000 коинов, дальше −1000 за каждую минуту (но не меньше 4000).</i>",
    [C("🍍 Бежать к ананасу", "b_hall")])

add("b_hall",
    "🍍 <b>Ананас на дне океана</b>\n\nДом Губки Боба выглядит так, будто хозяин вышел на минуточку. "
    "Гэри жалобно мяукает у порога. Откуда начнём?",
    [
        C("🚪 Осмотреть входную дверь", "b_door"),
        C("🛋 Войти в дом", "b_inside"),
        C("🐌 Поговорить с Гэри", "b_gary"),
        C("🗺 Карта Бикини Боттом", "b_map"),
        C("📋 Назвать похитителя (нужно 4 улики)", "b_theory", clues=4),
    ], cp=True)

add("b_door",
    "🚪 Входная дверь не выломана. Её <b>аккуратно сняли с петель</b>, а потом поставили обратно. "
    "Профессионал? Или очень вежливый гость?",
    [C("🔎 Присмотреться к ручке", "b_handle", item="b_lupa", hint="Нужна лупа (у Сэнди)"), C("◀️ Назад", "b_hall")],
    flags=["c_door"])

add("b_handle",
    "Под лупой на ручке видны блестящие капельки. Пахнет укропом и уксусом. Это <b>рассол</b>.",
    [C("◀️ Назад", "b_door")], flags=["c_slime"])

# ---------------------------- Внутри дома ----------------------------
add("b_inside",
    "🛋 <b>Гостиная</b>\n\nНа столике — записка, из кухни тянет холодной котлетой, а дверь спальни приоткрыта.",
    [
        C("📨 Прочитать записку", "b_ransom"),
        C("🍳 Пройти на кухню", "b_kitchen_b"),
        C("🛏 Заглянуть в спальню", "b_bedroom"),
        C("◀️ На улицу", "b_hall"),
    ])

add("b_ransom",
    "Записка из вырезанных букв:\n<i>«МИЛЛИОН КОИНОВ ИЛИ БОБ БОЛЬШЕ НЕ СПЕЧЁТ НИ ОДНОГО БУРГЕРА. ПОЛНОЧЬ. — ДОБРЫЙ ДРУГ»</i>\n\n"
    "Буквы вырезаны из какой-то упаковки.",
    [C("🔎 Изучить обратную сторону лупой", "b_label", item="b_lupa", hint="Нужна лупа (у Сэнди)"), C("◀️ Назад", "b_inside")],
    flags=["c_ransom"])

add("b_label",
    "На обороте букв — обрывки этикетки с нарисованным огурцом и клеймом <b>«Маринад №7»</b>. "
    "Такие банки продают не везде.",
    [C("◀️ Назад", "b_inside")], flags=["c_label"])

add("b_kitchen_b",
    "🍳 <b>Кухня Боба</b>\n\nНа плите стоит недоеденный ужин, холодильник немного приоткрыт.",
    [
        C("🔥 Осмотреть плиту", "b_stove"),
        C("🧊 Заглянуть в холодильник", "b_fridge_b"),
        C("◀️ В гостиную", "b_inside"),
    ])

add("b_stove",
    "Плита ещё тёплая: Боб что-то готовил, когда его прервали. Рядом на полу — <b>огромный отпечаток ботинка</b>, "
    "в два раза больше, чем у любого жителя города.",
    [C("◀️ Назад", "b_kitchen_b")], flags=["c_boots"])

add("b_fridge_b",
    "Дверца холодильника захлопывается на твоём плавнике! Зато внутри стоит открытая банка маринада №7, "
    "с которой капает рассол. Вещдок.",
    [C("◀️ Назад", "b_kitchen_b")], hp=-1, give="b_jar")

add("b_bedroom",
    "🛏 <b>Спальня Боба</b>\n\nИдеально заправленная кровать, одинаковые брюки в шкафу и ни одного следа борьбы.",
    [
        C("🔦 Заглянуть под кровать", "b_bed_under"),
        C("👖 Проверить шкаф", "b_wardrobe"),
        C("◀️ В гостиную", "b_inside"),
    ])

add("b_bed_under",
    "Под кроватью — пыльный носок, спящий бабочкоскат и... рабочий фонарик. Пригодится в тёмных местах.",
    [C("◀️ Назад", "b_bedroom")], give="b_flash")

add("b_wardrobe",
    "В кармане брюк лежит список заказов Боба. Вчерашний заказ №17 обведён: «БЕЗ маринадов! (тот самый гость)».",
    [C("◀️ Назад", "b_bedroom")])

# ---------------------------- Гэри ----------------------------
add("b_gary",
    "🐌 <b>Гэри</b>\n\nУлитка смотрит на тебя огромными глазами. Явно хочет что-то сказать.",
    [
        C("🧠 Позвать Патрика переводить", "b_patrick_translate"),
        C("🍽 Накормить Гэри", "b_gary_feed"),
        C("◀️ Назад", "b_hall"),
    ])

add("b_patrick_translate",
    "Патрик серьёзно кивает: «Он говорит... э-э... мяу». Гэри закатывает глаза. Переводчик из Патрика так себе.",
    [C("◀️ Назад", "b_gary")])

add("b_gary_feed",
    "Гэри мигом съедает корм и тянет тебя за собой к заднему двору. Там — всё те же <b>огромные следы</b> "
    "и сильный запах маринада. Они ведут на окраину города.",
    [C("◀️ Назад", "b_gary")], flags=["c_gary"], hp=1)

# ---------------------------- Карта города ----------------------------
add("b_map",
    "🗺 <b>Бикини Боттом</b>\n\nМного мест, много подозреваемых. Куда направимся?",
    [
        C("🍔 Красти Краб", "b_krusty"),
        C("🌳 Купол Сэнди", "b_sandy"),
        C("🧪 Чам Баккет", "b_chum"),
        C("🎷 Дом Сквидварда", "b_squid"),
        C("🦸 Дом героев", "b_heroes"),
        C("🚦 Перекрёсток", "b_street"),
        C("🛒 Магазин «Маринадный рай»", "b_store", all=["c_label"], hide=True),
        C("🏭 Консервный завод", "b_factory", all=["c_cannery"], hide=True),
        C("◀️ К ананасу", "b_hall"),
    ])

# ---------------------------- Красти Краб ----------------------------
add("b_krusty",
    "🍔 <b>Красти Краб</b>\n\nВ зале пусто, посетители ропщут. Мистер Крабс подсчитывает убытки.",
    [
        C("🦀 Поговорить с Крабсом", "b_krabs_talk"),
        C("📒 Заглянуть в журнал заказов", "b_kk_kitchen"),
        C("◀️ На карту", "b_map"),
    ])

add("b_krabs_talk",
    "🦀 <b>Мистер Крабс</b>\n\n— Боб пропал?! А кто будет жарить котлеты?! Кто будет работать за копейки?!",
    [
        C("💰 Спросить про выкуп", "b_krabs_ransom"),
        C("📅 Спросить про график Боба", "b_krabs_schedule"),
        C("◀️ Назад", "b_krusty"),
    ])

add("b_krabs_ransom",
    "— Миллион?! Ни за что! — Крабс хватается за сердце. — Выкуп — это выдумка мошенников! Ни одной монеты!",
    [
        C("📹 Показать запись камеры и уговорить", "b_krabs_persuade", all=["c_krabs_refuse", "c_cam_b"],
          hint="Нужна запись камеры с перекрёстка"),
        C("◀️ Назад", "b_krabs_talk"),
    ], flags=["c_krabs_refuse"])

add("b_krabs_persuade",
    "Крабс смотрит, как здоровяк тащит мешок с Бобом, и бледнеет. «Ладно... Но только монеты прошлого года!» "
    "Он трясущимися клешнями собирает чемодан.",
    [C("🧳 Взять чемодан", "b_ransom_bag"), C("◀️ Передумать", "b_krusty")])

add("b_ransom_bag",
    "Чемодан набит. До полуночи ещё есть время. Можно отнести выкуп, как требует записка, а можно продолжить расследование.",
    [C("🧳 Отнести выкуп в полночь", "b_end_ransom"), C("◀️ Продолжить расследование", "b_krusty")])

add("b_krabs_schedule",
    "— Боб вчера отпросился пораньше, сказал, что встречается с каким-то особенным гостем. "
    "Я, конечно, вычел это из зарплаты.",
    [C("◀️ Назад", "b_krabs_talk")])

add("b_kk_kitchen",
    "В журнале заказов жирным шрифтом: последний гость вчера — <b>Пузырь-Бандит</b>. "
    "Он орал, что в его бургере нашли маринады, а Боб клялся, что это не так. Скандал.",
    [C("◀️ Назад", "b_krusty")], flags=["c_bubble_visit"])

# ---------------------------- Сэнди ----------------------------
add("b_sandy",
    "🌳 <b>Купол Сэнди</b>\n\nБелка колдует над чем-то громоздким.\n— Боб пропал? Партнёр, держи лупу: с ней увидишь то, "
    "что не видно глазу.",
    [
        C("📻 Расспросить про рацию", "b_sandy_radio"),
        C("📡 Настроить рацию на сигнал", "b_radio_scan", item="b_radio", hint="Сначала возьми рацию у Сэнди"),
        C("◀️ На карту", "b_map"),
    ], give="b_lupa")

add("b_sandy_radio",
    "Сэнди протягивает потёртую рацию.\n— Ловит все частоты в городе. Только настроишь сам: там нужна математика.",
    [C("◀️ Назад", "b_sandy")], give="b_radio")

add("b_radio_scan",
    "Рация шипит. Экран просит ввести частоту:\n\n<b>7 × 6 − 6 = ?</b>",
    [C("36", "b_radio_ok"), C("48", "b_radio_wrong"), C("42", "b_radio_wrong")])

add("b_radio_wrong",
    "Рация взвизгивает на всю округу, и у тебя звенит в ушах. Попробуй ещё раз.",
    [C("🔁 Ещё раз", "b_radio_scan")], hp=-1)

add("b_radio_ok",
    "Сквозь треск слышен грубый голос: «...груз доставлен на склад. Маринад не забыть. Босс будет доволен...» "
    "Сигнал идёт с окраины города.",
    [C("◀️ Назад", "b_sandy")], flags=["c_radio"])

# ---------------------------- Чам Баккет ----------------------------
add("b_chum",
    "🧪 <b>Чам Баккет</b>\n\nИз бочки у входа торчит маленький глаз. Это Планктон.\n"
    "— Я тут застрял! Меня Карен в бочку запихнула за плохое поведение! Но на похищение я бы и сам согласился!",
    [
        C("🪣 Помочь Планктону вылезти", "b_plankton_free"),
        C("📹 Спросить у Карен записи камер", "b_karen"),
        C("◀️ На карту", "b_map"),
    ])

add("b_plankton_free",
    "Планктон вылезает и от радости делится с тобой печеньем. Крошка в мозгу, но силы возвращаются.",
    [C("◀️ Назад", "b_chum")], hp=1)

add("b_karen",
    "Карен показывает запись: Планктон сидит в бочке с 20:00 до самого утра и ругается на весь свет. "
    "Алиби железное.",
    [C("◀️ Назад", "b_chum")], flags=["c_plankton_alibi"])

# ---------------------------- Сквидвард ----------------------------
add("b_squid",
    "🎷 <b>Дом Сквидварда</b>\n\n— Если вы пришли сказать, что Боба нашли, не говорите. Мне и так хорошо, — бурчит Сквидвард.",
    [
        C("🎷 Спросить про вечер", "b_squid_alibi"),
        C("😤 Обыскать дом", "b_squid_search"),
        C("◀️ На карту", "b_map"),
    ])

add("b_squid_alibi",
    "— Я играл на кларнете до полуночи! — Он явно гордится. — Миссис Пафф стучала мне в стену двенадцать раз. "
    "Спросите кого угодно.",
    [C("◀️ Назад", "b_squid")], flags=["c_squid_alibi"])

add("b_squid_search",
    "Сквидвард бьёт тебя кларнетом по голове. В его доме нет ничего, кроме скуки и сорока нот «Ля».",
    [C("◀️ Назад", "b_squid")], hp=-1)

# ---------------------------- Дом героев ----------------------------
add("b_heroes",
    "🦸 <b>Дом героев</b>\n\nМужчина-Русалка и Мальчик-Ракушка пьют чай с печеньем. Увидев тебя, вскакивают: «ЗЛОДЕИ ГДЕ?!»",
    [
        C("🦸 Поговорить с героями", "b_heroes_talk"),
        C("🎖 Осмотреть коллекцию значков", "b_badges"),
        C("◀️ На карту", "b_map"),
    ])

add("b_heroes_talk",
    "— Мы были на дежурстве! — гордо заявляет Мужчина-Русалка. — Ровно в 21:00 мы уснули по расписанию. "
    "Мальчик-Ракушка подтвердит! — Мальчик-Ракушка храпит рядом.",
    [C("◀️ Назад", "b_heroes")], flags=["c_mermaid_alibi"])

add("b_badges",
    "На стене висят значки за подвиги. Один блестит особенно ярко: «Почётный помощник» с выгравированным именем Боба. "
    "Мужчина-Русалка машет рукой: «Бери, у нас таких ещё сто».",
    [C("◀️ Назад", "b_heroes")], give="b_badge")

# ---------------------------- Перекрёсток ----------------------------
add("b_street",
    "🚦 <b>Перекрёсток</b>\n\nСветофор мигает, рядом на столбе висит камера наблюдения, а на асфальте — странные следы.",
    [
        C("📹 Подключиться к камере", "b_cam_code", item="b_radio", hint="Нужна рация (спроси Сэнди)"),
        C("👣 Изучить следы на асфальте", "b_street_tracks"),
        C("◀️ На карту", "b_map"),
    ])

add("b_street_tracks",
    "Огромные ботинки оставляют глубокие следы. Рядом блестят капли рассола. Они тянутся на окраину, к "
    "<b>заброшенному консервному заводу</b>.",
    [C("◀️ Назад", "b_street")], flags=["c_cannery"])

add("b_cam_code",
    "Рация подключилась к панели камеры. Экран просит код:\n\n<b>3² + 4² = ?</b>",
    [C("14", "b_cam_wrong"), C("25", "b_cam_ok"), C("49", "b_cam_wrong")])

add("b_cam_wrong",
    "Бззз! Рация бьёт током. Придётся попробовать ещё раз.",
    [C("🔁 Ещё раз", "b_cam_code")], hp=-1)

add("b_cam_ok",
    "Запись вчерашнего вечера: здоровяк в жилете с огурцом на спине тащит большой мешок. Мешок кричит голосом Боба: "
    "«Я готов! Я всегда готов!» Лица не видно.",
    [C("◀️ Назад", "b_street")], flags=["c_cam_b"])

# ---------------------------- Магазин ----------------------------
add("b_store",
    "🛒 <b>Маринадный рай</b>\n\nПродавец вертит банку «Маринад №7».\n"
    "— Такие вчера купил один заказчик. Триста банок! Попросил доставить на старый консервный завод. Платил наличными, "
    "в жилете с огурцом.",
    [C("◀️ На карту", "b_map")], flags=["c_receipt", "c_cannery"])

# ---------------------------- Консервный завод ----------------------------
add("b_factory",
    "🏭 <b>Заброшенный консервный завод</b>\n\nРжавые трубы, выбитые окна, вонь укропа. На вывеске едва читается «Маринад №7». "
    "Внутри слышен чей-то голос.",
    [
        C("🚪 Постучать в дверь", "b_knock"),
        C("🔦 Обойти завод с тыла", "b_back_fac", item="b_flash", hint="Нужен фонарик (спальня Боба)"),
        C("◀️ На карту", "b_map"),
    ], cp=True)

add("b_knock",
    "Через щель доносится грубый голос: «Пароль?»",
    [
        C("«Огурец!»", "b_pass_wrong"),
        C("«Маринад №7»", "b_fin", all=["c_label"], hint="Где-то ты видел это название..."),
        C("«Крабсбургер!»", "b_pass_wrong"),
    ])

add("b_pass_wrong",
    "Голос хохочет, и тебе на голову опрокидывается ведро рассола. Фу!",
    [C("🤧 Отряхнуться", "b_factory")], hp=-1)

add("b_back_fac",
    "Фонарик освещает ржавую стену и узкое окошко.",
    [C("🪟 Протиснуться в окошко", "b_squeeze"), C("◀️ Назад", "b_factory")])

add("b_squeeze",
    "Ты застреваешь на секунду, потом вываливаешься внутрь. Ай!",
    [C("🏭 Осмотреться", "b_fin")], hp=-1)

add("b_fin",
    "🏭 <b>Внутри завода</b>\n\nРяды банок до потолка. В глубине — стол с бумагами и тяжёлая металлическая дверь подвала.",
    [
        C("📓 Осмотреть стол", "b_desk_f"),
        C("🫙 Обшарить стеллажи", "b_shelves"),
        C("🔒 Открыть подвал", "b_basement", item="b_key", hint="Ключ спрятан где-то на заводе"),
        C("◀️ Выйти", "b_factory"),
    ], cp=True)

add("b_desk_f",
    "На столе лежит потрёпанный дневник. Последняя запись: «Сегодня на мой день рождения опять никто не пришёл. "
    "Хочу друзей. И чтобы Боб сделал бургер с маринадом, как раньше».",
    [C("◀️ Назад", "b_fin")], flags=["c_diary"])

add("b_shelves",
    "Три банки стоят особняком. Какую откроем?",
    [C("🥒 Банка с огурцами", "b_jar_wrong"), C("🫙 Банка «Секретный ингредиент»", "b_jar_key"), C("🍅 Банка с томатами", "b_jar_wrong")])

add("b_jar_wrong",
    "Внутри просто рассол. Ты облизываешь плавник. Кисло.",
    [C("◀️ К стеллажам", "b_shelves")])

add("b_jar_key",
    "Под крышкой лежит ржавый ключ с биркой «ПОДВАЛ».",
    [C("◀️ Назад", "b_fin")], give="b_key")

# ---------------------------- Подвал и признание ----------------------------
add("b_basement",
    "🔒 <b>Подвал</b>\n\nНа стуле сидит Губка Боб с мягкими верёвками на ногах и радостно машет тебе.\n"
    "— Я знал, что ты придёшь! Но он очень милый, только вот...\n"
    "В углу мнётся огромный здоровяк в жилете с огурцом — <b>Пузырь-Бандит</b>.",
    [
        C("🕵️ Предъявить обвинение", "b_confront"),
        C("🥒 Предложить Пузырю дружбу", "b_friend", item="b_jar", all=["c_diary"],
          hint="Нужны дневник Пузыря и банка маринада"),
    ], cp=True)

add("b_confront",
    "Пузырь-Бандит опускает голову:\n— Да, это я. Я только хотел, чтобы кто-нибудь пришёл на мой день рождения! "
    "Боба похитил, чтобы он сделал мне идеальный бургер. А выкуп придумал, чтобы звучало серьёзно...",
    [C("🤝 Простить Пузыря", "b_end_mercy"), C("🚔 Сдать в полицию", "b_end_justice")])

add("b_friend",
    "Ты ставишь на стол банку маринада №7 и протягиваешь Пузырю лапу. Он вытирает слезу: «Правда? Я приглашён?» "
    "Боб уже зажигает свечки на торте.",
    [C("🎂 Начать праздник", "b_end_pickle")])

# ---------------------------- Развязка ----------------------------
add("b_theory",
    "📋 <b>Развязка</b>\n\nВсе собрались у ананаса. Патрик жуёт жвачку, Гэри нервно мяукает. Кого ты назовёшь похитителем?",
    [
        C("🦑 Сквидварда", "b_acc_squid"),
        C("🧪 Планктона", "b_acc_plankton"),
        C("🦸 Мужчину-Русалку", "b_acc_heroes", item="b_badge", hint="Нужен значок «Почётный помощник»"),
        C("🥒 Пузыря-Бандита", "b_acc_bubble", all=["c_label", "c_boots", "c_cannery"],
          hint="Нужны улики: этикетка маринада, следы ботинок и завод"),
        C("🔎 Ещё поищу улики", "b_hall"),
    ], cp=True)

add("b_acc_squid",
    "Сквидвард даже не вынимает кларнет изо рта: «Да, конечно, это я. В свободное время я ворую губок».",
    [C("😠 Настаивать", "b_end_squid"), C("🙇 Извиниться и передумать", "b_squid_sorry")])

add("b_squid_sorry",
    "Сквидвард метко запускает тебе кларнетом в лоб. Ты потираешь шишку и возвращаешься к версиям.",
    [C("◀️ К версиям", "b_theory")], hp=-1)

add("b_acc_plankton",
    "Планктон вылезает из бочки и вопит: «Опять я?! Да у меня алиби! Я сижу в бочке!»",
    [
        C("⛓ Настаивать и арестовать", "b_end_hasty"),
        C("◀️ Передумать", "b_theory"),
    ])

add("b_acc_heroes",
    "Мужчина-Русалка вскидывает бровь: «Мы?! Мы же защитники!» Мальчик-Ракушка шепчет: «Они нашли значок».",
    [C("🎖 Показать значок", "b_end_heroes"), C("◀️ Передумать", "b_theory")])

add("b_acc_bubble",
    "Толпа расступается. Пузырь-Бандит краснеет: «Я? Да вы что! Я просто мимо проходил!»",
    [C("🏷 Показать этикетку и следы ботинок", "b_bubble_break1")])

add("b_bubble_break1",
    "Ты прикладываешь вырезанные буквы к банке маринада и ставишь рядом огромный ботинок Пузыря. Всё сходится. "
    "Лицо Пузыря покрывается испариной.",
    [C("📹 Напомнить про завод", "b_bubble_break2")])

add("b_bubble_break2",
    "— Ладно, ладно! — Пузырь вскидывает руки. — Боб на старом заводе! Только не бейте!",
    [C("🏃 Бежать на завод", "b_chase")])

add("b_chase",
    "Вы всей толпой несётесь к заводу. Пузырь бежит впереди, показывает дорогу и приводит прямо к подвалу.",
    [C("🔓 Открыть подвал", "b_basement")], hp=-1)

# ---------------------------- Концовки ----------------------------
add("b_end_hasty",
    "🚔 Планктона увозят в участок. Назавтра его выпускают: у него алиби в виде бочки. А Боб так и не найден. "
    "Карен вздыхает: «Я же говорила».",
    end="b_hasty")

add("b_end_squid",
    "⚖️ Сквидвард подал на тебя в суд за клевету, и ты проиграл. Лицензия детектива отозвана. "
    "Зато в городе стало тише: Сквидвард больше не играет по утрам. Из вредности.",
    end="b_squid")

add("b_end_heroes",
    "🦸 Мужчина-Русалка радостно хлопает тебя по плечу: «Ты выдержал испытание! Теперь ты наш напарник!» "
    "Три дня подряд ты спасаешь город от воображаемых злодеев. Боба же найдут сами, спустя неделю. Он просто потерялся на сборе устриц.",
    end="b_heroes")

add("b_end_ransom",
    "🧳 В полночь чемодан забирает таинственная тень. Наутро Боб возвращается с мешком на голове и рассказывает, "
    "что похититель — «очень приятный человек с огурцом». Крабс неделю не разговаривает с тобой из-за потраченных денег.",
    end="b_ransom")

add("b_end_mercy",
    "🕊 Ты уговариваешь не сажать Пузыря. Боб жарит ему бургер с маринадом, весь город приходит на запоздалый день рождения, "
    "а Пузырь обещает больше никого не похищать. Настоящая концовка!",
    end="b_mercy")

add("b_end_justice",
    "🚔 Пузыря-Бандита уводят в участок. Боб возвращается в «Красти Краб», а Крабс снова вычитает опоздание из его зарплаты. "
    "Справедливость торжествует. Настоящая концовка!",
    end="b_justice")

add("b_end_pickle",
    "🎂 Пузырь впервые в жизни задувает свечи в окружении друзей. Боб жарит бургеры с маринадом №7, Патрик танцует с Гэри, "
    "Сэнди включает фейерверк. Секретная концовка!",
    end="b_pickle")

add("b_dead",
    "💀 <b>Силы кончились</b>\n\nТы вымотан до предела. Придётся перевести дух и вернуться к последнему чекпоинту. "
    "Здоровье восстановится.",
    fail=True)


# ---------------------------- Движок квестов ----------------------------
def new_q(qid):
    return {"node": QUESTS[qid]["start"], "hp": MAX_HP, "inv": [], "flags": [], "cp": QUESTS[qid]["hub"],
            "endings": [], "claimed": [], "started": int(time.time())}


async def get_q(uid, qid):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT node, hp, inv, flags, checkpoint, endings, claimed, started FROM quests WHERE user_id = ? AND qid = ?",
            (uid, qid),
        ) as cur:
            r = await cur.fetchone()
    if not r:
        return None
    return {
        "node": r[0], "hp": r[1], "inv": json.loads(r[2] or "[]"), "flags": json.loads(r[3] or "[]"),
        "cp": r[4] or QUESTS[qid]["hub"], "endings": json.loads(r[5] or "[]"), "claimed": json.loads(r[6] or "[]"),
        "started": int(r[7] or time.time()),
    }


async def save_q(uid, qid, q):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO quests (user_id, qid, node, hp, inv, flags, checkpoint, endings, claimed, started) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, qid, q["node"], q["hp"], json.dumps(q["inv"]), json.dumps(q["flags"]), q["cp"],
             json.dumps(q["endings"]), json.dumps(q["claimed"]), q["started"]),
        )
        await db.commit()


async def quest_gate(uid):
    """Лимит: новый квест можно начать раз в сутки. Возвращает текст ошибки или None."""
    if uid == OWNER_ID:
        return None
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_quest FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
    last = (row[0] if row else 0) or 0
    left = QUEST_COOLDOWN - (int(time.time()) - last)
    if left > 0:
        return f"Новый квест можно начать раз в сутки. Осталось ждать: {fmt_time(left)}"
    return None


async def mark_quest_start(uid):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET last_quest = ? WHERE user_id = ?", (int(time.time()), uid))
        await db.commit()


def qid_of(data):
    parts = data.split(":")
    return parts[1] if len(parts) > 1 and parts[1] in QUESTS else "formula"


def clue_count(flags, qid):
    return sum(1 for f in flags if f in CLUES_BY_Q[qid])


def hearts(hp):
    return "❤️" * hp + "🖤" * (MAX_HP - hp)


def choice_ok(ch, q, qid):
    if "item" in ch and ch["item"] not in q["inv"]:
        return False, ch.get("hint", f"Нужен предмет: {ITEMS[ch['item']]}")
    for f in ch.get("all", []):
        if f not in q["flags"]:
            return False, ch.get("hint", "Не хватает улик")
    if "clues" in ch and clue_count(q["flags"], qid) < ch["clues"]:
        return False, f"Нужно улик: {ch['clues']} (сейчас {clue_count(q['flags'], qid)})"
    return True, ""


async def apply_node(uid, qid, q, nid):
    """Применяет эффекты узла при входе. Возвращает список заметок для игрока."""
    n = NODES[nid]
    q["node"] = nid
    notes = []

    for f in n.get("flags", []):
        if f not in q["flags"]:
            q["flags"].append(f)
            if f in CLUES_BY_Q[qid]:
                notes.append(f"🧩 Новая улика: {CLUES_BY_Q[qid][f]}")

    g = n.get("give")
    if g and g not in q["inv"]:
        q["inv"].append(g)
        notes.append(f"🎒 Получено: {ITEMS[g]}")

    delta = n.get("hp", 0)
    if delta:
        key = f"hp_{nid}"
        if delta < 0:
            q["hp"] = max(0, q["hp"] + delta)
            notes.append(f"💔 Здоровье {delta}")
        elif key not in q["claimed"]:
            q["claimed"].append(key)
            q["hp"] = min(MAX_HP, q["hp"] + delta)
            notes.append(f"💚 Здоровье +{delta}")

    if n.get("cp"):
        q["cp"] = nid

    if n.get("end"):
        eid = n["end"]
        total = len(ENDINGS_BY_Q[qid])
        if eid not in q["endings"]:
            q["endings"].append(eid)
            notes.append(f"✨ Новая концовка открыта! ({len(q['endings'])}/{total})")
        else:
            notes.append("ℹ️ Эта концовка уже была открыта.")
        if len(q["endings"]) >= total and "quest_final" not in q["claimed"]:
            q["claimed"].append("quest_final")
            elapsed = max(0, int(time.time()) - q["started"])
            reward = final_reward(elapsed)
            await add_coins(uid, reward, earn=True)
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("UPDATE users SET story_status = 'completed' WHERE user_id = ?", (uid,))
                await db.commit()
            notes.append(f"🏆 Квест пройден полностью за {fmt_time(elapsed)}! Награда: +{reward} коинов")

    if q["hp"] <= 0 and not n.get("end"):
        q["node"] = QUESTS[qid]["dead"]
    return notes


def node_text(q, qid, notes):
    n = NODES[q["node"]]
    head = f"{hearts(q['hp'])}   🧩 {clue_count(q['flags'], qid)}   🎒 {len(q['inv'])}\n\n"
    body = n["t"]
    if n.get("end"):
        body += (f"\n\n🏁 <b>Концовка: {ENDINGS[n['end']]}</b>\n"
                 f"Открыто концовок: {len(q['endings'])}/{len(ENDINGS_BY_Q[qid])}")
    if notes:
        body += "\n\n" + "\n".join(notes)
    return head + body


def node_kb(q, qid):
    nid = q["node"]
    n = NODES[nid]
    rows = []
    for i, ch in enumerate(n["c"]):
        ok, _ = choice_ok(ch, q, qid)
        if ok:
            rows.append([InlineKeyboardButton(text=ch["l"], callback_data=f"q:{ch['to']}")])
        elif not ch.get("hide"):
            rows.append([InlineKeyboardButton(text="🔒 " + ch["l"], callback_data=f"ql:{qid}:{i}")])
    if n.get("end") or n.get("fail"):
        rows.append([InlineKeyboardButton(text="↩️ К последнему чекпоинту", callback_data=f"qcp:{qid}")])
        rows.append([InlineKeyboardButton(text="🔄 Новая игра (концовки сохранятся)", callback_data=f"qnew_yes:{qid}")])
    rows.append([
        InlineKeyboardButton(text="🎒 Блокнот", callback_data=f"qinv:{qid}"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="back_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_node(cb, qid, q, notes=None):
    if q["node"] not in NODES or NODES[q["node"]]["q"] != qid:
        hub = QUESTS[qid]["hub"]
        q["node"] = q["cp"] if q["cp"] in NODES and NODES[q["cp"]]["q"] == qid else hub
    await show(cb, node_text(q, qid, notes or []), node_kb(q, qid))


# ---------------------------- Хендлеры квестов ----------------------------
@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    await register_user(uid, callback.from_user.username or "NoName", callback.from_user.first_name or "Игрок")
    lines, rows = [], []
    for qid, info in QUESTS.items():
        q = await get_q(uid, qid)
        done = len(q["endings"]) if q else 0
        mark = "✅" if q and "quest_final" in q["claimed"] else ("▶️" if q else "🆕")
        lines.append(f"{mark} {info['icon']} <b>{info['title']}</b>: концовок {done}/{len(ENDINGS_BY_Q[qid])}")
        rows.append([btn(f"{info['icon']} {info['title']}", f"qinfo:{qid}")])
    rows.append([btn("◀️ В меню", "back_menu")])
    text = (
        "📖 <b>Эпичные квесты</b>\n\n" + "\n".join(lines) +
        "\n\n⏳ <b>Лимит:</b> начать новый квест можно раз в сутки. Уже начатые квесты можно проходить когда угодно.\n\n" + REWARD_TEXT
    )
    await show(callback, text, kb_rows(rows))


@dp.callback_query(F.data.startswith("qinfo:"))
async def q_info(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    qid = qid_of(callback.data)
    info = QUESTS[qid]
    q = await get_q(uid, qid)
    text = (
        f"{info['icon']} <b>Квест: {info['title']}</b>\n\n{info['desc']}\n\n"
        f"🌊 Огромное расследование с десятками сцен, ветками, предметами и <b>7 концовками</b>. Прогресс сохраняется автоматически.\n\n"
        + REWARD_TEXT
    )
    rows = []
    if q:
        text += (f"\n\n💾 <b>Твоё сохранение:</b>\n{hearts(q['hp'])}\n🧩 Улик: {clue_count(q['flags'], qid)}/{len(CLUES_BY_Q[qid])}\n"
                 f"🎒 Предметов: {len(q['inv'])}\n🏁 Концовок: {len(q['endings'])}/{len(ENDINGS_BY_Q[qid])}\n"
                 + ("✅ Награда за квест получена" if "quest_final" in q["claimed"]
                    else f"⏱ Время с первого запуска: {fmt_time(max(0, int(time.time()) - q['started']))}"))
        rows.append([btn("▶️ Продолжить", f"qgo:{qid}")])
        rows.append([btn("🎒 Блокнот", f"qinv:{qid}")])
        rows.append([btn("🔄 Новая игра", f"qnew_ask:{qid}")])
    else:
        gate = await quest_gate(uid)
        if gate:
            text += f"\n\n🔒 {gate}"
        rows.append([btn("🕵️ Начать расследование", f"qgo:{qid}")])
    rows.append([btn("◀️ К списку квестов", "story_start")])
    await show(callback, text, kb_rows(rows))


@dp.callback_query(F.data.startswith("qgo"))
async def q_go(callback: types.CallbackQuery):
    uid = callback.from_user.id
    qid = qid_of(callback.data)
    async with user_locks[uid]:
        q = await get_q(uid, qid)
        notes = []
        if not q:
            gate = await quest_gate(uid)
            if gate:
                await callback.answer(f"🔒 {gate}", show_alert=True)
                return
            await callback.answer()
            await register_user(uid, callback.from_user.username or "NoName", callback.from_user.first_name or "Игрок")
            q = new_q(qid)
            notes = await apply_node(uid, qid, q, QUESTS[qid]["start"])
            await save_q(uid, qid, q)
            await mark_quest_start(uid)
        else:
            await callback.answer()
        await show_node(callback, qid, q, notes)


@dp.callback_query(F.data.startswith("qnew_ask"))
async def q_new_ask(callback: types.CallbackQuery):
    await callback.answer()
    qid = qid_of(callback.data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, начать заново", callback_data=f"qnew_yes:{qid}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"qinfo:{qid}")],
    ])
    await show(callback, "🔄 Начать квест заново?\n\nУлики, предметы и текущее место сбросятся. "
                         "Открытые концовки и уже полученные награды <b>сохранятся</b>.", kb)


@dp.callback_query(F.data.startswith("qnew_yes"))
async def q_new_yes(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    qid = qid_of(callback.data)
    async with user_locks[uid]:
        old = await get_q(uid, qid)
        q = new_q(qid)
        if old:
            q["endings"] = old["endings"]
            q["claimed"] = old["claimed"]
            q["started"] = old["started"]
        notes = await apply_node(uid, qid, q, QUESTS[qid]["start"])
        await save_q(uid, qid, q)
        await show_node(callback, qid, q, notes)


@dp.callback_query(F.data.startswith("qcp"))
async def q_checkpoint(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    qid = qid_of(callback.data)
    async with user_locks[uid]:
        q = await get_q(uid, qid)
        if not q:
            return
        q["hp"] = MAX_HP
        q["node"] = q["cp"] if q["cp"] in NODES and NODES[q["cp"]]["q"] == qid else QUESTS[qid]["hub"]
        await save_q(uid, qid, q)
        await show_node(callback, qid, q, ["💚 Здоровье восстановлено"])


@dp.callback_query(F.data.startswith("ql:"))
async def q_locked(callback: types.CallbackQuery):
    qid = qid_of(callback.data)
    q = await get_q(callback.from_user.id, qid)
    try:
        idx = int(callback.data.split(":")[-1])
        ch = NODES[q["node"]]["c"][idx]
        _, msg = choice_ok(ch, q, qid)
    except Exception:
        msg = "Пока недоступно"
    await callback.answer(f"🔒 {msg}", show_alert=True)


@dp.callback_query(F.data.startswith("q:"))
async def q_move(callback: types.CallbackQuery):
    uid = callback.from_user.id
    target = callback.data[2:]
    if target not in NODES:
        await callback.answer("Эта кнопка устарела.")
        return
    qid = NODES[target]["q"]
    async with user_locks[uid]:
        q = await get_q(uid, qid)
        if not q:
            await callback.answer("Сохранение не найдено. Открой квест из меню.", show_alert=True)
            return
        cur = NODES.get(q["node"])
        ch = next((c for c in cur["c"] if c["to"] == target), None) if cur else None
        if not ch:
            await callback.answer("Эта кнопка устарела, продолжаем с текущего места.")
            await show_node(callback, qid, q)
            return
        ok, msg = choice_ok(ch, q, qid)
        if not ok:
            await callback.answer(f"🔒 {msg}", show_alert=True)
            return
        await callback.answer()
        notes = await apply_node(uid, qid, q, target)
        await save_q(uid, qid, q)
        await show_node(callback, qid, q, notes)


@dp.callback_query(F.data.startswith("qinv"))
async def q_inventory(callback: types.CallbackQuery):
    uid = callback.from_user.id
    qid = qid_of(callback.data)
    q = await get_q(uid, qid)
    if not q:
        await callback.answer("Сначала начни квест", show_alert=True)
        return
    await callback.answer()
    clues_all = CLUES_BY_Q[qid]
    items = "\n".join(f"• {ITEMS[i]}" for i in q["inv"]) or "— пусто"
    clues = "\n".join(f"• {clues_all[f]}" for f in q["flags"] if f in clues_all) or "— пока нет"
    ends = "\n".join(f"• {ENDINGS[e]}" for e in q["endings"]) or "— пока нет"
    text = (f"🎒 <b>Блокнот детектива: {QUESTS[qid]['title']}</b>\n\n{hearts(q['hp'])}\n\n<b>Предметы:</b>\n{items}\n\n"
            f"<b>Улики ({clue_count(q['flags'], qid)}/{len(clues_all)}):</b>\n{clues}\n\n"
            f"<b>Концовки ({len(q['endings'])}/{len(ENDINGS_BY_Q[qid])}):</b>\n{ends}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить расследование", callback_data=f"qgo:{qid}")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_menu")],
    ])
    await show(callback, text, kb)


# ======================================================================
#                      КОЛЕСО, КАЗИНО, ТИР, КНБ
# ======================================================================
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Ещё раз (100)", callback_data="spin_wheel")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")],
    ])
    await show(callback, res, kb)


# ---------------------------- 🎰 Казино: любая ставка ----------------------------
bet_wait = set()    # пользователи, от которых ждём сумму ставки сообщением
dice_busy = set()   # пользователи, у которых прямо сейчас катится кубик
MAX_BET_DIGITS = 12


def casino_kb():
    return kb_rows([
        [btn("🎲 50", "dice:50"), btn("🎲 100", "dice:100"), btn("🎲 500", "dice:500")],
        [btn("💰 Своя ставка", "casino_bet")],
        [btn("◀️ Назад", "back_menu")],
    ])


@dp.callback_query(F.data == "casino")
async def casino(callback: types.CallbackQuery):
    await callback.answer()
    bet_wait.discard(callback.from_user.id)
    bal = await get_balance(callback.from_user.id)
    await show(callback,
               "🎰 <b>Казино</b>\n\nБросаешь кубик: выпало <b>4, 5 или 6</b> — выигрыш <b>x2</b> от ставки (чистая прибыль = ставка). "
               "Выпало 1–3 — ставка сгорает.\n\n"
               f"💰 Баланс: <b>{bal}</b>\nВыбери ставку или нажми «Своя ставка» и введи любую сумму.",
               casino_kb())


@dp.callback_query(F.data == "casino_bet")
async def casino_bet(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    bal = await get_balance(uid)
    bet_wait.add(uid)
    await show(callback,
               f"💰 <b>Своя ставка</b>\n\nНа счету: <b>{bal}</b> коинов.\n"
               f"Отправь в чат число: сумму ставки (от 1 до {bal}).",
               kb_rows([[btn("◀️ Отмена", "casino")]]))


async def dice_round(uid, chat_id, bet):
    if uid in dice_busy:
        return
    dice_busy.add(uid)
    try:
        if not await spend(uid, bet):
            bal = await get_balance(uid)
            await bot.send_message(
                chat_id, f"❌ Не хватает коинов. Твой баланс: <b>{bal}</b>.",
                reply_markup=kb_rows([[btn("💰 Другая ставка", "casino_bet")], [btn("◀️ В меню", "back_menu")]]),
                parse_mode="HTML",
            )
            return
        dice = await bot.send_dice(chat_id, emoji="🎲")
        await asyncio.sleep(3.5)
        val = dice.dice.value
        if val >= 4:
            await add_coins(uid, bet * 2)
            res = f"🎉 Выпало {val}! Выигрыш: <b>+{bet} коинов</b> (ставка вернулась x2)!"
        else:
            res = f"😢 Выпало {val}. Проигрыш: <b>−{bet} коинов</b>."
        bal = await get_balance(uid)
        kb = kb_rows([
            [btn(f"🎲 Ещё раз ({bet})", f"dice:{bet}")],
            [btn("💰 Другая ставка", "casino_bet")],
            [btn("◀️ Назад в меню", "back_menu")],
        ])
        await bot.send_message(chat_id, f"{res}\n💰 Баланс: <b>{bal}</b>", reply_markup=kb, parse_mode="HTML")
    finally:
        dice_busy.discard(uid)


async def dice_from_callback(callback: types.CallbackQuery, bet: int):
    uid = callback.from_user.id
    if uid in dice_busy:
        await callback.answer("🎲 Кубик ещё катится, подожди секунду.")
        return
    bal = await get_balance(uid)
    if bet <= 0 or bet > bal:
        await callback.answer(f"❌ Мало коинов! Баланс: {bal}", show_alert=True)
        return
    await callback.answer()
    chat_id = callback.message.chat.id
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await dice_round(uid, chat_id, bet)


@dp.callback_query(F.data.startswith("dice:"))
async def play_dice_bet(callback: types.CallbackQuery):
    try:
        bet = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    await dice_from_callback(callback, bet)


@dp.callback_query(F.data == "play_dice")
async def play_dice_old(callback: types.CallbackQuery):
    # совместимость со старыми кнопками
    await dice_from_callback(callback, 50)


@dp.message(F.text, lambda m: m.from_user.id in bet_wait)
async def bet_message(message: types.Message):
    uid = message.from_user.id
    raw = message.text.strip().replace(" ", "").replace(",", "").replace("_", "")
    if not raw.isdigit() or int(raw) <= 0 or len(raw) > MAX_BET_DIGITS:
        await message.answer("Введи целое положительное число, например <b>250</b>.", parse_mode="HTML",
                             reply_markup=kb_rows([[btn("◀️ Отмена", "casino")]]))
        return
    bet = int(raw)
    bal = await get_balance(uid)
    if bet > bal:
        await message.answer(f"❌ Столько нет на счету. Твой баланс: <b>{bal}</b>. Введи сумму поменьше.", parse_mode="HTML",
                             reply_markup=kb_rows([[btn("◀️ Отмена", "casino")]]))
        return
    bet_wait.discard(uid)
    await dice_round(uid, message.chat.id, bet)


@dp.callback_query(F.data == "game_jelly")
async def game_jelly(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪼", callback_data="jelly_0"),
         InlineKeyboardButton(text="🪼", callback_data="jelly_1"),
         InlineKeyboardButton(text="🪼", callback_data="jelly_2")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])
    await show(callback, "🎯 <b>Тир по медузам</b>\n\nВыстрел стоит 30 коинов. Одна из трёх медуз золотая: попадёшь — <b>+90</b>.", kb)


@dp.callback_query(F.data.startswith("jelly_"))
async def jelly_shot(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await spend(uid, 30):
        await callback.answer("❌ Мало коинов!", show_alert=True)
        return
    await callback.answer()
    pick = int(callback.data.split("_")[1])
    gold = random.randint(0, 2)
    if pick == gold:
        await add_coins(uid, 90)
        res = "🎯 Попал в золотую медузу! <b>+90 коинов</b>!"
    else:
        res = "💨 Мимо! Золотая медуза уплыла."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Ещё выстрел (30)", callback_data="game_jelly")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")],
    ])
    await show(callback, res, kb)


@dp.callback_query(F.data == "game_rps")
async def game_rps(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪨", callback_data="rps_rock"),
         InlineKeyboardButton(text="✂️", callback_data="rps_scissors"),
         InlineKeyboardButton(text="📄", callback_data="rps_paper")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])
    await show(callback, "✂️ <b>КНБ с Патриком</b>\n\nСтавка 40 коинов. Победа: +80, ничья: ставка возвращается.", kb)


@dp.callback_query(F.data.startswith("rps_"))
async def rps_play(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await spend(uid, 40):
        await callback.answer("❌ Мало коинов!", show_alert=True)
        return
    await callback.answer()
    names = {"rock": "🪨", "scissors": "✂️", "paper": "📄"}
    beats = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
    me = callback.data.split("_")[1]
    pat = random.choice(list(names))
    if me == pat:
        await add_coins(uid, 40)
        res = "🤝 Ничья, ставка возвращена."
    elif beats[me] == pat:
        await add_coins(uid, 80)
        res = "🎉 Ты победил! <b>+80 коинов</b>"
    else:
        res = "😢 Патрик выиграл (случайно)."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✂️ Ещё раунд (40)", callback_data="game_rps")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")],
    ])
    await show(callback, f"Ты: {names[me]}  |  Патрик: {names[pat]}\n\n{res}", kb)


# ======================================================================
#                      БОНУС, ТОП
# ======================================================================
@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(callback: types.CallbackQuery):
    uid = callback.from_user.id
    now = int(time.time())
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        last = row[0] if row else 0
        if uid != OWNER_ID and now - last < 86400:
            left = 86400 - (now - last)
            await callback.answer(f"⏳ Следующий бонус через {left // 3600} ч {(left % 3600) // 60} мин", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance + 250, last_bonus = ? WHERE user_id = ?", (now, uid))
        await db.commit()
    await callback.answer()
    await show(callback, "🎁 Бонус получен: <b>+250 коинов</b>!", get_back_keyboard())


@dp.callback_query(F.data == "top")
async def top(callback: types.CallbackQuery):
    await callback.answer()
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 5") as cur:
            rows = await cur.fetchall()
    text = "🏆 <b>Топ-5 игроков:</b>\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {escape(str(r[0]))} — <b>{r[1]} коинов</b>\n"
    await show(callback, text, get_back_keyboard())


# ======================================================================
async def main():
    logging.basicConfig(level=logging.INFO)
    if TOKEN.startswith("ВСТАВЬ"):
        raise SystemExit("Укажи токен в переменной окружения BOT_TOKEN.")
    # Проверка графа квестов: все ссылки должны вести на существующие узлы
    for nid, node in NODES.items():
        for ch in node["c"]:
            assert ch["to"] in NODES, f"Квест: узел '{nid}' ведёт в несуществующий '{ch['to']}'"
            assert NODES[ch["to"]]["q"] == node["q"], f"Квест: узел '{nid}' ведёт в другой квест '{ch['to']}'"
    await db_start()
    dp.message.outer_middleware(SubscriptionMiddleware())
    dp.callback_query.outer_middleware(SubscriptionMiddleware())
    print(f"Бот запущен. База: {DB_FILE}. Узлов в квестах: {len(NODES)}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
