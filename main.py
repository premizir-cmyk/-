"""
Телеграм-бот: база отзывов (скрин + текст + ссылка).

Зависимости: requirements.txt (aiogram, aiosqlite)
Запуск:      python review_bot.py
"""
import asyncio
import logging
import os
import re
from difflib import SequenceMatcher

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

TOKEN = os.getenv("BOT_TOKEN") or "9149595437:AAFzA4uUdI8xOsH2i_CLLBRjDUdY9GocfzM"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reviews.db")
URL_RE = re.compile(r"https?://\S+")
MATCH_THRESHOLD = 0.75  # минимальная похожесть текста (0..1)
MAX_RESULTS = 3

BTN_ADD = "➕ Добавить"
BTN_CANCEL = "❌ Отмена"

router = Router()


class Adding(StatesGroup):
    collecting = State()


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                text_norm TEXT NOT NULL,
                photo TEXT NOT NULL,
                link TEXT NOT NULL,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        await db.commit()


# ---------- добавление ----------

async def collect(message: Message, state: FSMContext):
    """Забирает из сообщения фото / ссылку / текст и копит в состоянии."""
    data = await state.get_data()

    if message.photo:
        data["photo"] = message.photo[-1].file_id

    raw = message.text or message.caption or ""
    m = URL_RE.search(raw)
    if m:
        data["link"] = m.group(0)
        raw = URL_RE.sub("", raw)
    raw = raw.strip()
    if raw:
        data["text"] = raw

    await state.set_state(Adding.collecting)
    await state.set_data(data)

    missing = []
    if "photo" not in data:
        missing.append("скрин")
    if "text" not in data:
        missing.append("текст")
    if "link" not in data:
        missing.append("ссылку")

    if missing:
        await message.answer("Принял. Осталось: " + ", ".join(missing), reply_markup=kb())
        return

    # всё собрано — сохраняем
    tn = norm(data["text"])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM reviews WHERE text_norm = ?", (tn,))
        exists = await cur.fetchone()
        if exists:
            await message.answer("⚠️ Такой текст уже есть в базе, не сохранил.", reply_markup=kb())
        else:
            await db.execute(
                "INSERT INTO reviews (text, text_norm, photo, link) VALUES (?, ?, ?, ?)",
                (data["text"], tn, data["photo"], data["link"]),
            )
            await db.commit()
            await message.answer("✅ Сохранено в базу.", reply_markup=kb())
    await state.clear()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Как пользоваться:\n\n"
        "• Добавить отзыв: перешли скрин, текст и ссылку (по отдельности, в любом порядке). "
        "Если начинаешь с текста — сначала нажми «➕ Добавить».\n"
        "• Найти: просто отправь текст отзыва — пришлю скрин и ссылку.",
        reply_markup=kb(),
    )


@router.message(Command("add"))
@router.message(F.text == BTN_ADD)
async def add_mode(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Adding.collecting)
    await message.answer("Режим добавления. Присылай скрин, текст и ссылку.", reply_markup=kb())


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено. Теперь я в режиме поиска.", reply_markup=kb())


@router.message(Command("count"))
async def count(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reviews")
        (n,) = await cur.fetchone()
    await message.answer(f"В базе записей: {n}")


@router.message(Adding.collecting, F.photo | F.text)
async def adding_any(message: Message, state: FSMContext):
    await collect(message, state)


# ---------- автозапуск добавления без кнопки ----------

@router.message(F.photo)
async def auto_add_photo(message: Message, state: FSMContext):
    await collect(message, state)


@router.message(F.text.regexp(URL_RE))
async def auto_add_link(message: Message, state: FSMContext):
    await collect(message, state)


# ---------- поиск ----------

@router.message(F.text)
async def search(message: Message):
    q = norm(message.text)
    if not q:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT text, text_norm, photo, link FROM reviews")
        rows = await cur.fetchall()

    scored = []
    for text, tn, photo, link in rows:
        if q == tn:
            score = 1.0
        elif len(q) >= 15 and (q in tn or tn in q):
            score = 0.95
        else:
            score = SequenceMatcher(None, q, tn).ratio()
        if score >= MATCH_THRESHOLD:
            scored.append((score, text, photo, link))

    if not scored:
        await message.answer("Такого текста в базе нет.")
        return

    scored.sort(key=lambda x: x[0], reverse=True)
    for score, text, photo, link in scored[:MAX_RESULTS]:
        snippet = text if len(text) <= 300 else text[:300] + "…"
        caption = f"🔗 {link}\n\nСовпадение: {int(score * 100)}%\n\n{snippet}"
        await message.answer_photo(photo, caption=caption[:1024])


async def main():
    if not TOKEN:
        raise SystemExit("Укажи токен в переменной окружения BOT_TOKEN")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
