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
        # Сохранение квеста: место, здоровье, предметы, улики, чекпоинт, концовки
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
        await db.execute("CREATE TABLE IF NOT EXISTS work_cd (user_id INTEGER, job TEXT, last_ts INTEGER, PRIMARY KEY (user_id, job))")
        try:
            await db.execute("ALTER TABLE users ADD COLUMN earned INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE quest ADD COLUMN started INTEGER")
        except Exception:
            pass
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
            InlineKeyboardButton(text="📖 Эпичный квест", callback_data="story_start"),
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
    q = await get_q(uid)
    endings = len(q["endings"]) if q else 0
    text = (
        f"👤 <b>Твой профиль:</b>\n\n💰 Баланс: <b>{balance} коинов</b>\n🛠 Смен: <b>{jobs}</b>\n"
        f"🏆 Заработано для розыгрыша: <b>{earned}</b>\n"
        f"📖 Концовок квеста: <b>{endings}/{len(ENDINGS)}</b>"
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
        "В зачёт идут только зарплата с работ и итоговая награда квеста. Казино, колесо и бонусы не считаются.\n\n"
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
#                      КВЕСТ: «ТАЙНА ФОРМУЛЫ»
#   Данные квеста — это граф узлов. Чтобы добавить сцену, просто
#   добавь add("id", "текст", [C("кнопка", "куда")], ...)
#
#   Параметры узла: flags=[...] (улики/метки), give="предмет", hp=±N,
#   cp=True (чекпоинт), end="id концовки", fail=True
#   Параметры кнопки: item="предмет", all=[флаги], clues=N,
#   hint="подсказка при блокировке", hide=True (прятать, пока закрыто)
# ======================================================================
MAX_HP = 5

# Единственная награда квеста: за все 7 концовок. Зависит от времени с первого запуска квеста.
FINAL_TIERS = [(1 * 3600, 5000), (3 * 3600, 4000), (6 * 3600, 3000), (12 * 3600, 2000), (24 * 3600, 1500)]
FINAL_MIN = 1000


def final_reward(elapsed):
    for limit, reward in FINAL_TIERS:
        if elapsed <= limit:
            return reward
    return FINAL_MIN


ITEMS = {
    "lupa": "🔎 Лупа",
    "drone": "🚁 Дрон «Белка-1»",
    "note": "🗒 Записка из мусорки",
    "blueprint": "📐 Чертёж робо-ноги",
    "napkin": "🧻 Салфетка Патрика",
}

CLUES = {
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

ENDINGS = {
    "hasty": "Поспешный арест",
    "squid": "Невинная жертва кларнета",
    "plankton": "Правосудие по Планктону",
    "patrick": "Сон, в котором была формула",
    "merger": "Красти-Чам Корп",
    "truth_mercy": "Прощённый Крабс",
    "truth_justice": "Крабс за решёткой",
}

NODES = {}


def add(nid, text, choices=(), **kw):
    NODES[nid] = {"t": text, "c": list(choices), **kw}


def C(label, to, **kw):
    return {"l": label, "to": to, **kw}


# ---------------------------- Пролог и главный зал ----------------------------
add("intro",
    "🌊 <b>Пролог. Дело о пропавшей формуле</b>\n\n"
    "Утро в Бикини Боттом. У «Красти Краб» — полицейская лента, толпа зевак и вопли мистера Крабса: "
    "ночью из сейфа исчезла <b>секретная формула крабсбургера</b>.\n\n"
    "Полиция разводит плавниками, поэтому Крабс нанял тебя — лучшего (и единственного свободного) детектива города.\n\n"
    "<i>Прогресс сохраняется автоматически: можно закрыть бот и вернуться в любой момент. "
    "Всего в квесте 7 концовок. Награда одна, за все 7, и чем быстрее ты их соберёшь, тем она больше.</i>",
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


# ---------------------------- Движок квеста ----------------------------
def new_q():
    return {"node": "intro", "hp": MAX_HP, "inv": [], "flags": [], "cp": "hall", "endings": [], "claimed": [], "started": int(time.time())}


async def get_q(uid):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT node, hp, inv, flags, checkpoint, endings, claimed, started FROM quest WHERE user_id = ?", (uid,)
        ) as cur:
            r = await cur.fetchone()
    if not r:
        return None
    return {
        "node": r[0], "hp": r[1], "inv": json.loads(r[2] or "[]"), "flags": json.loads(r[3] or "[]"),
        "cp": r[4] or "hall", "endings": json.loads(r[5] or "[]"), "claimed": json.loads(r[6] or "[]"),
        "started": int(r[7] or time.time()),
    }


async def save_q(uid, q):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO quest (user_id, node, hp, inv, flags, checkpoint, endings, claimed, started) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, q["node"], q["hp"], json.dumps(q["inv"]), json.dumps(q["flags"]), q["cp"],
             json.dumps(q["endings"]), json.dumps(q["claimed"]), q["started"]),
        )
        await db.commit()


def clue_count(flags):
    return sum(1 for f in flags if f in CLUES)


def hearts(hp):
    return "❤️" * hp + "🖤" * (MAX_HP - hp)


def choice_ok(ch, q):
    if "item" in ch and ch["item"] not in q["inv"]:
        return False, ch.get("hint", f"Нужен предмет: {ITEMS[ch['item']]}")
    for f in ch.get("all", []):
        if f not in q["flags"]:
            return False, ch.get("hint", "Не хватает улик")
    if "clues" in ch and clue_count(q["flags"]) < ch["clues"]:
        return False, f"Нужно улик: {ch['clues']} (сейчас {clue_count(q['flags'])})"
    return True, ""


async def apply_node(uid, q, nid):
    """Применяет эффекты узла при входе. Возвращает список заметок для игрока."""
    n = NODES[nid]
    q["node"] = nid
    notes = []

    for f in n.get("flags", []):
        if f not in q["flags"]:
            q["flags"].append(f)
            if f in CLUES:
                notes.append(f"🧩 Новая улика: {CLUES[f]}")

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
        if eid not in q["endings"]:
            q["endings"].append(eid)
            notes.append(f"✨ Новая концовка открыта! ({len(q['endings'])}/{len(ENDINGS)})")
        else:
            notes.append("ℹ️ Эта концовка уже была открыта.")
        if len(q["endings"]) >= len(ENDINGS) and "quest_final" not in q["claimed"]:
            q["claimed"].append("quest_final")
            elapsed = max(0, int(time.time()) - q["started"])
            reward = final_reward(elapsed)
            await add_coins(uid, reward, earn=True)
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("UPDATE users SET story_status = 'completed' WHERE user_id = ?", (uid,))
                await db.commit()
            notes.append(f"🏆 Квест пройден полностью за {fmt_time(elapsed)}! Награда: +{reward} коинов")

    if q["hp"] <= 0 and not n.get("end"):
        q["node"] = "dead"
    return notes


def node_text(q, notes):
    n = NODES[q["node"]]
    head = f"{hearts(q['hp'])}   🧩 {clue_count(q['flags'])}   🎒 {len(q['inv'])}\n\n"
    body = n["t"]
    if n.get("end"):
        body += (f"\n\n🏁 <b>Концовка: {ENDINGS[n['end']]}</b>\n"
                 f"Открыто концовок: {len(q['endings'])}/{len(ENDINGS)}")
    if notes:
        body += "\n\n" + "\n".join(notes)
    return head + body


def node_kb(q):
    nid = q["node"]
    n = NODES[nid]
    rows = []
    for i, ch in enumerate(n["c"]):
        ok, _ = choice_ok(ch, q)
        if ok:
            rows.append([InlineKeyboardButton(text=ch["l"], callback_data=f"q:{ch['to']}")])
        elif not ch.get("hide"):
            rows.append([InlineKeyboardButton(text="🔒 " + ch["l"], callback_data=f"ql:{i}")])
    if n.get("end") or n.get("fail"):
        rows.append([InlineKeyboardButton(text="↩️ К последнему чекпоинту", callback_data="qcp")])
        rows.append([InlineKeyboardButton(text="🔄 Новая игра (концовки сохранятся)", callback_data="qnew_yes")])
    rows.append([
        InlineKeyboardButton(text="🎒 Блокнот", callback_data="qinv"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="back_menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_node(cb, q, notes=None):
    if q["node"] not in NODES:
        q["node"] = q["cp"] if q["cp"] in NODES else "hall"
    await show(cb, node_text(q, notes or []), node_kb(q))


# ---------------------------- Хендлеры квеста ----------------------------
@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    await register_user(uid, callback.from_user.username or "NoName", callback.from_user.first_name or "Игрок")
    q = await get_q(uid)
    text = (
        "📖 <b>Эпичный квест: Тайна формулы</b>\n\n"
        "🌊 Огромное расследование с десятками сцен, ветками, предметами и <b>7 концовками</b>. "
        "Прогресс сохраняется автоматически.\n\n"
        "🏆 <b>Награда одна, за все 7 концовок</b>, и зависит от времени с первого запуска:\n"
        "до 1 ч — 5000, до 3 ч — 4000, до 6 ч — 3000, до 12 ч — 2000, до 24 ч — 1500, дольше — 1000."
    )
    rows = []
    if q:
        text += (f"\n\n💾 <b>Твоё сохранение:</b>\n{hearts(q['hp'])}\n🧩 Улик: {clue_count(q['flags'])}/{len(CLUES)}\n"
                 f"🎒 Предметов: {len(q['inv'])}\n🏁 Концовок: {len(q['endings'])}/{len(ENDINGS)}\n"
                 + ("✅ Награда за квест получена" if "quest_final" in q["claimed"]
                    else f"⏱ Время с первого запуска: {fmt_time(max(0, int(time.time()) - q['started']))}"))
        rows.append([InlineKeyboardButton(text="▶️ Продолжить", callback_data="qgo")])
        rows.append([InlineKeyboardButton(text="🎒 Блокнот", callback_data="qinv")])
        rows.append([InlineKeyboardButton(text="🔄 Новая игра", callback_data="qnew_ask")])
    else:
        rows.append([InlineKeyboardButton(text="🕵️ Начать расследование", callback_data="qgo")])
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")])
    await show(callback, text, InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "qgo")
async def q_go(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    async with user_locks[uid]:
        q = await get_q(uid)
        notes = []
        if not q:
            q = new_q()
            notes = await apply_node(uid, q, "intro")
            await save_q(uid, q)
        await show_node(callback, q, notes)


@dp.callback_query(F.data == "qnew_ask")
async def q_new_ask(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, начать заново", callback_data="qnew_yes")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="story_start")],
    ])
    await show(callback, "🔄 Начать квест заново?\n\nУлики, предметы и текущее место сбросятся. "
                         "Открытые концовки и уже полученные награды <b>сохранятся</b>.", kb)


@dp.callback_query(F.data == "qnew_yes")
async def q_new_yes(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    async with user_locks[uid]:
        old = await get_q(uid)
        q = new_q()
        if old:
            q["endings"] = old["endings"]
            q["claimed"] = old["claimed"]
            q["started"] = old["started"]
        notes = await apply_node(uid, q, "intro")
        await save_q(uid, q)
        await show_node(callback, q, notes)


@dp.callback_query(F.data == "qcp")
async def q_checkpoint(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    async with user_locks[uid]:
        q = await get_q(uid)
        if not q:
            return
        q["hp"] = MAX_HP
        q["node"] = q["cp"] if q["cp"] in NODES else "hall"
        await save_q(uid, q)
        await show_node(callback, q, ["💚 Здоровье восстановлено"])


@dp.callback_query(F.data.startswith("ql:"))
async def q_locked(callback: types.CallbackQuery):
    q = await get_q(callback.from_user.id)
    try:
        idx = int(callback.data[3:])
        ch = NODES[q["node"]]["c"][idx]
        _, msg = choice_ok(ch, q)
    except Exception:
        msg = "Пока недоступно"
    await callback.answer(f"🔒 {msg}", show_alert=True)


@dp.callback_query(F.data.startswith("q:"))
async def q_move(callback: types.CallbackQuery):
    uid = callback.from_user.id
    target = callback.data[2:]
    async with user_locks[uid]:
        q = await get_q(uid)
        if not q:
            await callback.answer("Сохранение не найдено. Открой квест из меню.", show_alert=True)
            return
        cur = NODES.get(q["node"])
        ch = next((c for c in cur["c"] if c["to"] == target), None) if cur else None
        if not ch or target not in NODES:
            await callback.answer("Эта кнопка устарела, продолжаем с текущего места.")
            await show_node(callback, q)
            return
        ok, msg = choice_ok(ch, q)
        if not ok:
            await callback.answer(f"🔒 {msg}", show_alert=True)
            return
        await callback.answer()
        notes = await apply_node(uid, q, target)
        await save_q(uid, q)
        await show_node(callback, q, notes)


@dp.callback_query(F.data == "qinv")
async def q_inventory(callback: types.CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    q = await get_q(uid)
    if not q:
        await callback.answer("Сначала начни квест", show_alert=True)
        return
    items = "\n".join(f"• {ITEMS[i]}" for i in q["inv"]) or "— пусто"
    clues = "\n".join(f"• {CLUES[f]}" for f in q["flags"] if f in CLUES) or "— пока нет"
    ends = "\n".join(f"• {ENDINGS[e]}" for e in q["endings"]) or "— пока нет"
    text = (f"🎒 <b>Блокнот детектива</b>\n\n{hearts(q['hp'])}\n\n<b>Предметы:</b>\n{items}\n\n"
            f"<b>Улики ({clue_count(q['flags'])}/{len(CLUES)}):</b>\n{clues}\n\n"
            f"<b>Концовки ({len(q['endings'])}/{len(ENDINGS)}):</b>\n{ends}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить расследование", callback_data="qgo")],
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


@dp.callback_query(F.data == "casino")
async def casino(callback: types.CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Бросить кости (50 коинов)", callback_data="play_dice")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])
    await show(callback, "🎰 <b>Казино</b>\n\n4, 5 или 6 — выигрыш +100.", kb)


@dp.callback_query(F.data == "play_dice")
async def play_dice(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not await spend(uid, 50):
        await callback.answer("❌ Мало коинов!", show_alert=True)
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
        await add_coins(uid, 100)
        res = f"🎉 Выпало {val}! Выигрыш: <b>+100 коинов</b>!"
    else:
        res = f"😢 Выпало {val}. Проигрыш."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Ещё раз (50)", callback_data="play_dice")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")],
    ])
    await bot.send_message(chat_id, res, reply_markup=kb, parse_mode="HTML")


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
        raise SystemExit("Укажи токен в BOT_TOKEN (или в переменной TOKEN в коде).")
    # Проверка графа квеста: все ссылки должны вести на существующие узлы
    for nid, node in NODES.items():
        for ch in node["c"]:
            assert ch["to"] in NODES, f"Квест: узел '{nid}' ведёт в несуществующий '{ch['to']}'"
    await db_start()
    dp.message.outer_middleware(SubscriptionMiddleware())
    dp.callback_query.outer_middleware(SubscriptionMiddleware())
    print(f"Бот запущен. База: {DB_FILE}. Узлов в квесте: {len(NODES)}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
