"""
Телеграм-бот: база отзывов (скрин + текст + ссылка).

Зависимости: requirements.txt (aiogram, aiosqlite)
Запуск:      python main.py (токен берётся из переменной BOT_TOKEN)
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
from aiogram.types import InputMediaPhoto, KeyboardButton, Message, ReplyKeyboardMarkup

TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reviews.db")
URL_RE = re.compile(r"https?://\S+")
MATCH_THRESHOLD = 0.75  # минимальная похожесть текста (0..1)
MAX_RESULTS = 3

BTN_ADD = "➕ Добавить"
BTN_CANCEL = "❌ Отмена"
BTN_PASSED = "✅ Пройденные"

router = Router()


class Adding(StatesGroup):
    collecting = State()


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PASSED)],
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_CANCEL)],
        ],
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
        # миграция для старых баз: колонки «пройден»
        cur = await db.execute("PRAGMA table_info(reviews)")
        cols = [r[1] for r in await cur.fetchall()]
        if "passed" not in cols:
            await db.execute("ALTER TABLE reviews ADD COLUMN passed INTEGER NOT NULL DEFAULT 0")
        if "passed_at" not in cols:
            await db.execute("ALTER TABLE reviews ADD COLUMN passed_at TIMESTAMP")
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
        "• Найти: просто отправь текст отзыва — пришлю скрин и ссылку, а отзыв попадёт в пройденные.\n"
        "• Кнопка «✅ Пройденные» — скрины и ссылки всех пройденных.",
        reply_markup=kb(),
    )


@router.message(Command("add"))
@router.message(F.text == BTN_ADD)
async def add_mode(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Adding.collecting)
    await message.answer("Режим добавления. Присылай скрин, текст и ссылку.", reply_markup=kb())


@router.message(F.text == BTN_PASSED)
async def passed_list(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT photo, link FROM reviews WHERE passed = 1 ORDER BY passed_at, id"
        )
        rows = await cur.fetchall()

    if not rows:
        await message.answer("Пройденных пока нет.")
        return

    await message.answer(f"Пройденные отзывы: {len(rows)}")
    for i in range(0, len(rows), 10):
        chunk = rows[i:i + 10]
        if len(chunk) == 1:
            photo, link = chunk[0]
            await message.answer_photo(photo, caption=link[:1024])
        else:
            media = [InputMediaPhoto(media=photo, caption=link[:1024]) for photo, link in chunk]
            await message.answer_media_group(media)
        await asyncio.sleep(0.5)


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
        cur = await db.execute("SELECT id, text, text_norm, photo, link, passed FROM reviews")
        rows = await cur.fetchall()

        scored = []
        for rid, text, tn, photo, link, passed in rows:
            if q == tn:
                score = 1.0
            elif len(q) >= 15 and (q in tn or tn in q):
                score = 0.95
            else:
                score = SequenceMatcher(None, q, tn).ratio()
            if score >= MATCH_THRESHOLD:
                scored.append((score, rid, text, photo, link, passed))

        if not scored:
            await message.answer("Такого текста в базе нет.")
            return

        scored.sort(key=lambda x: x[0], reverse=True)

        # лучший результат считаем пройденным
        best_id = scored[0][1]
        await db.execute(
            "UPDATE reviews SET passed = 1, passed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND passed = 0",
            (best_id,),
        )
        await db.commit()

    for n, (score, rid, text, photo, link, passed) in enumerate(scored[:MAX_RESULTS]):
        snippet = text if len(text) <= 300 else text[:300] + "…"
        if n == 0:
            mark = "✅ Уже в пройденных" if passed else "✅ Отмечен как пройденный"
        else:
            mark = "Похожий отзыв"
        caption = f"{mark}\n🔗 {link}\n\nСовпадение: {int(score * 100)}%\n\n{snippet}"
        await message.answer_photo(photo, caption=caption[:1024])


async def main():
    if not TOKEN:
        raise SystemExit("Укажи токен в переменной окружения BOT_TOKEN (в настройках хостинга)")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
