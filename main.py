"""
Телеграм-бот: база отзывов (скрин + текст + ссылка).

Токен берётся из переменной окружения BOT_TOKEN.
Запуск: python main.py
"""
import asyncio
import logging
import os
import re
import shutil
import sqlite3
import time
from difflib import SequenceMatcher

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

TOKEN = os.getenv("BOT_TOKEN", "")

# ---------- где лежит база ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_data_dir = os.path.join(BASE_DIR, "data")
if os.getenv("DB_PATH"):
    DB_PATH = os.getenv("DB_PATH")
elif os.path.isdir(_data_dir):
    DB_PATH = os.path.join(_data_dir, "reviews.db")
    _old = os.path.join(BASE_DIR, "reviews.db")
    if os.path.exists(_old) and not os.path.exists(DB_PATH):
        shutil.copy2(_old, DB_PATH)
else:
    DB_PATH = os.path.join(BASE_DIR, "reviews.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

URL_RE = re.compile(r"https?://\S+")
MATCH_THRESHOLD = 0.90   # насколько текст должен совпасть, чтобы считать отзыв найденным
ADD_TTL = 900            # режим добавления сам выключается через 15 минут

BTN_PASSED = "✅ Пройденные"
BTN_RESET = "🔄 Сбросить пройденные"
BTN_UNPASS = "↩️ Убрать из пройденных"
BTN_ADD = "➕ Добавить"
BTN_CANCEL = "❌ Отмена"

router = Router()
ADD: dict[int, dict] = {}              # user_id -> то, что уже прислали для нового отзыва
PENDING_TEXT: dict[tuple, str] = {}    # (chat_id, message_id) -> ненайденный текст


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PASSED), KeyboardButton(text=BTN_RESET)],
            [KeyboardButton(text=BTN_UNPASS)],
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


# ---------- вспомогательное ----------

def norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < MATCH_THRESHOLD or sm.quick_ratio() < MATCH_THRESHOLD:
        return 0.0
    return sm.ratio()


def extract_link(message: Message):
    """Ссылка из сообщения: видимая или спрятанная под словом."""
    raw = message.text or message.caption or ""
    ents = list(message.entities or []) + list(message.caption_entities or [])
    for e in ents:
        if e.type == "text_link" and e.url:
            return e.url
        if e.type == "url":
            return e.extract_from(raw)
    m = URL_RE.search(raw)
    return m.group(0) if m else None


def valid_db(path: str) -> bool:
    try:
        con = sqlite3.connect(path)
        con.execute("SELECT COUNT(*) FROM reviews").fetchone()
        con.close()
        return True
    except Exception:
        return False


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                text_norm TEXT NOT NULL,
                photo TEXT NOT NULL,
                link TEXT NOT NULL,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                passed INTEGER NOT NULL DEFAULT 0,
                passed_at TIMESTAMP,
                was_passed INTEGER NOT NULL DEFAULT 0
            )"""
        )
        cur = await db.execute("PRAGMA table_info(reviews)")
        cols = [r[1] for r in await cur.fetchall()]
        if "passed" not in cols:
            await db.execute("ALTER TABLE reviews ADD COLUMN passed INTEGER NOT NULL DEFAULT 0")
        if "passed_at" not in cols:
            await db.execute("ALTER TABLE reviews ADD COLUMN passed_at TIMESTAMP")
        if "was_passed" not in cols:
            # «когда-либо был в пройденных» — не сбрасывается кнопкой «Сбросить пройденные»
            await db.execute("ALTER TABLE reviews ADD COLUMN was_passed INTEGER NOT NULL DEFAULT 0")
            await db.execute("UPDATE reviews SET was_passed = 1 WHERE passed = 1")
        await db.commit()


async def count_reviews() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reviews")
        (n,) = await cur.fetchone()
    return n


# ---------- режим добавления ----------

def get_add(uid: int):
    data = ADD.get(uid)
    if data and time.time() - data["_t"] > ADD_TTL:
        ADD.pop(uid, None)
        return None
    return data


def new_add(uid: int) -> dict:
    ADD[uid] = {"_t": time.time()}
    return ADD[uid]


async def save_review(message: Message, data: dict):
    text, photo, link = data["text"], data["photo"], data["link"]
    tn = norm(text)
    if not tn:
        await message.answer("Текст пустой, не сохранил.", reply_markup=kb())
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT photo, link, passed, was_passed FROM reviews WHERE text_norm = ?", (tn,)
        )
        row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO reviews (text, text_norm, photo, link) VALUES (?, ?, ?, ?)",
                (text, tn, photo, link),
            )
            await db.commit()
    if row is None:
        await message.answer(f"✅ Сохранено в базу.\n«{text[:80]}»", reply_markup=kb())
        return
    # такой отзыв уже есть — ничего не меняем, показываем старую запись
    old_photo, old_link, passed, was_passed = row
    note = (
        "⚠️ Уже был в пройденных, не сохранил."
        if passed or was_passed
        else "⚠️ Такой текст уже есть в базе, не сохранил."
    )
    await message.answer_photo(old_photo, caption=f"{note}\n🔗 {old_link}"[:1024], reply_markup=kb())


async def collect(message: Message, uid: int, data: dict, raw: str, link):
    if message.photo:
        data["photo"] = message.photo[-1].file_id
    if link:
        data["link"] = link          # текст рядом со ссылкой не берём
    elif raw:
        data["text"] = raw
    data["_t"] = time.time()

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

    ADD.pop(uid, None)
    await save_review(message, data)


# ---------- поиск ----------

async def search(message: Message, raw: str):
    q = norm(raw)
    if not q:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, text, text_norm, photo, link, passed, was_passed FROM reviews")
        rows = await cur.fetchall()

        best = None
        for rid, text, tn, photo, link, passed, was_passed in rows:
            score = similarity(q, tn)
            if score >= MATCH_THRESHOLD and (best is None or score > best[0]):
                best = (score, rid, text, photo, link, passed, was_passed)

        if best is None:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="➕ Добавить этот текст", callback_data="addtext")
                ]]
            )
            sent = await message.answer("Такого текста в базе нет.", reply_markup=markup)
            if len(PENDING_TEXT) > 200:
                PENDING_TEXT.pop(next(iter(PENDING_TEXT)))
            PENDING_TEXT[(sent.chat.id, sent.message_id)] = raw
            return

        score, rid, text, photo, link, passed, was_passed = best
        if not passed:
            await db.execute(
                "UPDATE reviews SET passed = 1, was_passed = 1, passed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (rid,),
            )
            await db.commit()

    if passed:
        mark = "✅ Уже в пройденных"
    elif was_passed:
        mark = "⚠️ Был в пройденных раньше (до сброса). Отметил снова"
    else:
        mark = "✅ Отмечен как пройденный"
    pct = "" if score >= 0.999 else f"\nСовпадение: {int(score * 100)}%"
    await message.answer_photo(photo, caption=f"{mark}\n🔗 {link}{pct}"[:1024])


@router.callback_query(F.data == "addtext")
async def add_text(call: CallbackQuery):
    text = PENDING_TEXT.pop((call.message.chat.id, call.message.message_id), None)
    if not text:
        await call.answer("Устарело, отправь текст заново", show_alert=True)
        return
    new_add(call.from_user.id)["text"] = text
    await call.message.edit_text("Текст принят. Теперь пришли скрин и ссылку.")
    await call.answer()


# ---------- кнопки и команды ----------

@router.message(CommandStart())
async def start(message: Message):
    ADD.pop(message.from_user.id, None)
    await message.answer(
        "Привет! Как пользоваться:\n\n"
        "• Найти отзыв: отправь его текст — пришлю скрин и ссылку, отзыв попадёт в пройденные.\n"
        "• Добавить: пришли скрин и ссылку (каждое отдельным сообщением), потом текст. "
        "Или начни с текста: если его нет в базе, нажми «➕ Добавить этот текст».\n"
        "• «✅ Пройденные» — список по ссылкам.\n"
        "• «↩️ Убрать из пройденных» — убрать отдельные.\n"
        "• «🔄 Сбросить пройденные» — начать новый отчёт.",
        reply_markup=kb(),
    )


@router.message(Command("add"))
@router.message(F.text == BTN_ADD)
async def add_mode(message: Message):
    new_add(message.from_user.id)
    await message.answer("Режим добавления. Присылай скрин, ссылку и текст.", reply_markup=kb())


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cancel(message: Message):
    ADD.pop(message.from_user.id, None)
    await message.answer("Отменено. Снова в режиме поиска.", reply_markup=kb())


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

    groups: dict[str, list[str]] = {}
    for photo, link in rows:
        groups.setdefault(link, []).append(photo)

    await message.answer(f"Пройденные: ссылок {len(groups)}, отзывов {len(rows)}")
    no_preview = LinkPreviewOptions(is_disabled=True)
    for num, (link, photos) in enumerate(groups.items(), 1):
        await message.answer(f"{num}) {link}", link_preview_options=no_preview)
        for i in range(0, len(photos), 10):
            chunk = photos[i:i + 10]
            if len(chunk) == 1:
                await message.answer_photo(chunk[0], caption=link[:1024])
            else:
                await message.answer_media_group(
                    [InputMediaPhoto(media=p, caption=link[:1024]) for p in chunk]
                )
        await asyncio.sleep(0.5)


@router.message(F.text == BTN_UNPASS)
async def unpass_list(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, photo, link, text FROM reviews WHERE passed = 1 ORDER BY passed_at, id"
        )
        rows = await cur.fetchall()
    if not rows:
        await message.answer("Пройденных пока нет.")
        return
    await message.answer("Нажми «❌ Убрать» под отзывом, который нужно убрать из пройденных:")
    for rid, photo, link, text in rows:
        snippet = text if len(text) <= 150 else text[:150] + "…"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="❌ Убрать", callback_data=f"unpass:{rid}")
            ]]
        )
        await message.answer_photo(photo, caption=f"🔗 {link}\n\n{snippet}"[:1024], reply_markup=markup)
        await asyncio.sleep(0.3)


@router.callback_query(F.data.startswith("unpass:"))
async def unpass(call: CallbackQuery):
    rid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reviews SET passed = 0, was_passed = 0, passed_at = NULL WHERE id = ?", (rid,))
        await db.commit()
    try:
        await call.message.edit_caption(caption="↩️ Убрано из пройденных")
    except Exception:
        pass
    await call.answer("Убрано")


@router.message(F.text == BTN_RESET)
async def reset_ask(message: Message):
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
        f"Сбросить пройденные ({n} шт.)? Сами отзывы останутся в базе.", reply_markup=markup
    )


@router.callback_query(F.data == "reset_yes")
async def reset_yes(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE reviews SET passed = 0, passed_at = NULL WHERE passed = 1")
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
    await message.answer(f"В базе записей: {await count_reviews()}")


@router.message(Command("backup"))
async def backup(message: Message):
    n = await count_reviews()
    await message.answer_document(
        FSInputFile(DB_PATH, filename="reviews.db"),
        caption=f"Копия базы, записей: {n}. Чтобы восстановить — отправь этот файл боту.",
    )


@router.message(lambda m: m.document is not None and (m.document.file_name or "").endswith(".db"))
async def restore(message: Message):
    tmp = DB_PATH + ".restore"
    await message.bot.download(message.document, destination=tmp)
    if not valid_db(tmp):
        os.remove(tmp)
        await message.answer("❌ Это не похоже на файл базы бота, не восстановил.")
        return
    os.replace(tmp, DB_PATH)
    await init_db()
    await message.answer(f"✅ База восстановлена, записей: {await count_reviews()}")


# ---------- все остальные сообщения ----------

@router.message(F.text | F.photo)
async def on_message(message: Message):
    uid = message.from_user.id
    raw = (message.text or message.caption or "").strip()
    if raw.startswith("/") and not message.photo:
        return
    link = extract_link(message)

    data = get_add(uid)
    if data is None and (message.photo or link):
        data = new_add(uid)      # скрин или ссылка сами включают добавление

    if data is None:
        await search(message, raw)
    else:
        await collect(message, uid, data, raw, link)


async def main():
    if not TOKEN:
        raise SystemExit("Укажи токен в переменной окружения BOT_TOKEN (в настройках хостинга)")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
