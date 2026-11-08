"""
Телеграм-бот: база отзывов (скрин + текст + ссылка).

Зависимости: requirements.txt (aiogram, aiosqlite)
Запуск:      python main.py (токен берётся из переменной BOT_TOKEN)
"""
import asyncio
import logging
import os
import re
import shutil
import sqlite3
from difflib import SequenceMatcher

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

TOKEN = os.getenv("BOT_TOKEN", "")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_data_dir = os.path.join(BASE_DIR, "data")
if os.getenv("DB_PATH"):
    DB_PATH = os.getenv("DB_PATH")
elif os.path.isdir(_data_dir):
    # на хостинге есть папка data (она сохраняется между обновлениями)
    DB_PATH = os.path.join(_data_dir, "reviews.db")
    _old = os.path.join(BASE_DIR, "reviews.db")
    if os.path.exists(_old) and not os.path.exists(DB_PATH):
        shutil.copy2(_old, DB_PATH)
else:
    DB_PATH = os.path.join(BASE_DIR, "reviews.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
# кому можно делать /backup и восстановление (ID через запятую). Пусто = всем.
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
URL_RE = re.compile(r"https?://\S+")
MATCH_THRESHOLD = 0.75  # минимальная похожесть текста (0..1)
MAX_RESULTS = 3

BTN_ADD = "➕ Добавить"
BTN_CANCEL = "❌ Отмена"
BTN_PASSED = "✅ Пройденные"
BTN_RESET = "🔄 Сбросить пройденные"

router = Router()


class Adding(StatesGroup):
    collecting = State()


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PASSED), KeyboardButton(text=BTN_RESET)],
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
        "• Кнопка «✅ Пройденные» — скрины и ссылки всех пройденных.\n"
        "• Кнопка «🔄 Сбросить пройденные» — начать новый отчёт.",
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


def is_admin(message: Message) -> bool:
    return not ADMIN_IDS or message.from_user.id in ADMIN_IDS


@router.message(Command("backup"))
async def backup(message: Message):
    if not is_admin(message) or not os.path.exists(DB_PATH):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reviews")
        (n,) = await cur.fetchone()
    await message.answer_document(
        FSInputFile(DB_PATH, filename="reviews.db"),
        caption=f"Копия базы, записей: {n}. Чтобы восстановить, просто отправь этот файл боту.",
    )


@router.message(F.document & F.document.file_name.endswith(".db"))
async def restore(message: Message):
    if not is_admin(message):
        return
    tmp = DB_PATH + ".restore"
    await message.bot.download(message.document, destination=tmp)
    try:
        con = sqlite3.connect(tmp)
        con.execute("SELECT COUNT(*) FROM reviews").fetchone()
        con.close()
    except Exception:
        os.remove(tmp)
        await message.answer("❌ Это не похоже на файл базы бота, не восстановил.")
        return
    os.replace(tmp, DB_PATH)
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reviews")
        (n,) = await cur.fetchone()
    await message.answer(f"✅ База восстановлена, записей: {n}")


@router.message(F.text == BTN_RESET)
async def reset_ask(message: Message):
    if not is_admin(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reviews WHERE passed = 1")
        (n,) = await cur.fetchone()
    if n == 0:
        await message.answer("Пройденных нет, сбрасывать нечего.")
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, сбросить", callback_data="reset_yes"),
            InlineKeyboardButton(text="Отмена", callback_data="reset_no"),
        ]]
    )
    await message.answer(
        f"Сбросить пройденные ({n} шт.)? Сами отзывы останутся в базе, "
        "просто снова станут непройденными.",
        reply_markup=markup,
    )


@router.callback_query(F.data == "reset_yes")
async def reset_yes(call: CallbackQuery):
    if ADMIN_IDS and call.from_user.id not in ADMIN_IDS:
        await call.answer()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE reviews SET passed = 0, passed_at = NULL WHERE passed = 1"
        )
        n = cur.rowcount
        await db.commit()
    await call.message.edit_text(f"🔄 Сброшено пройденных: {n}. Можно начинать новый отчёт.")
    await call.answer()


@router.callback_query(F.data == "reset_no")
async def reset_no(call: CallbackQuery):
    await call.message.edit_text("Отменено, пройденные остались как были.")
    await call.answer()


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
