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
    LinkPreviewOptions,
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
AUTO_THRESHOLD = 0.90   # от этого значения бот сам отмечает отзыв пройденным
MAYBE_THRESHOLD = 0.60  # от этого значения показывает как «возможно, это он»
MAX_RESULTS = 3

BTN_ADD = "➕ Добавить"
BTN_CANCEL = "❌ Отмена"
BTN_PASSED = "✅ Пройденные"
BTN_RESET = "🔄 Сбросить пройденные"
BTN_UNPASS = "↩️ Убрать из пройденных"

router = Router()
PENDING_DUP: dict[int, dict] = {}  # данные, ждущие решения «заменить / оставить»


class Adding(StatesGroup):
    collecting = State()


def kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PASSED), KeyboardButton(text=BTN_RESET)],
            [KeyboardButton(text=BTN_UNPASS)],
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def extract_link(message: Message):
    """Ссылка из сообщения: видимая или спрятанная под текстом («Смотрите»)."""
    raw = message.text or message.caption or ""
    ents = list(message.entities or []) + list(message.caption_entities or [])
    for e in ents:
        if e.type == "text_link" and e.url:
            return e.url
        if e.type == "url":
            return e.extract_from(raw)
    m = URL_RE.search(raw)
    return m.group(0) if m else None


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


# ---------- автокопия базы в Telegram (чтобы данные не пропадали) ----------
# Нужна переменная ADMIN_IDS (твой Telegram ID, узнать: команда /id).
# Бот сам присылает файл базы в личку и закрепляет его. Если после обновления
# кода база окажется пустой, бот сам достанет закреплённую копию и восстановит её.

_dirty = False


def mark_dirty():
    global _dirty
    _dirty = True


def valid_db(path: str) -> bool:
    try:
        con = sqlite3.connect(path)
        con.execute("SELECT COUNT(*) FROM reviews").fetchone()
        con.close()
        return True
    except Exception:
        return False


async def count_reviews() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM reviews")
        (n,) = await cur.fetchone()
    return n


def _snapshot(dst: str):
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()


async def send_backup(bot: Bot):
    n = await count_reviews()
    if n == 0:
        return  # пустой базой не затираем хорошую копию
    snap = DB_PATH + ".snap"
    await asyncio.to_thread(_snapshot, snap)
    for admin in ADMIN_IDS:
        try:
            msg = await bot.send_document(
                admin,
                FSInputFile(snap, filename="reviews.db"),
                caption=f"💾 Авто-копия базы, записей: {n}",
                disable_notification=True,
            )
            try:
                await bot.unpin_all_chat_messages(admin)
            except Exception:
                pass
            await bot.pin_chat_message(admin, msg.message_id, disable_notification=True)
        except Exception:
            logging.exception("Не удалось отправить копию базы %s", admin)
    try:
        os.remove(snap)
    except OSError:
        pass


async def backup_loop(bot: Bot):
    global _dirty
    while True:
        await asyncio.sleep(30)
        if _dirty and ADMIN_IDS:
            _dirty = False
            try:
                await send_backup(bot)
            except Exception:
                logging.exception("backup_loop")


async def auto_restore(bot: Bot):
    """Если база пустая (после переустановки) — берём закреплённую копию из Telegram."""
    if not ADMIN_IDS or await count_reviews() > 0:
        return
    for admin in ADMIN_IDS:
        try:
            chat = await bot.get_chat(admin)
            pm = getattr(chat, "pinned_message", None)
            doc = pm.document if pm else None
            if not doc or not (doc.file_name or "").endswith(".db"):
                continue
            tmp = DB_PATH + ".restore"
            await bot.download(doc, destination=tmp)
            if not valid_db(tmp):
                os.remove(tmp)
                continue
            os.replace(tmp, DB_PATH)
            await init_db()
            n = await count_reviews()
            await bot.send_message(admin, f"✅ База восстановлена автоматически, записей: {n}")
            return
        except Exception:
            logging.exception("auto_restore")


# ---------- добавление ----------

async def collect(message: Message, state: FSMContext):
    """Забирает из сообщения фото / ссылку / текст и копит в состоянии."""
    data = await state.get_data()

    if message.photo:
        data["photo"] = message.photo[-1].file_id

    raw = message.text or message.caption or ""
    link = extract_link(message)
    if link:
        data["link"] = link
        raw = ""  # текст рядом со ссылкой (название организации и т.п.) не берём
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
        cur = await db.execute(
            "SELECT id, photo, link, text FROM reviews WHERE text_norm = ?", (tn,)
        )
        exists = await cur.fetchone()
        if exists:
            old_id, photo, link, old_text = exists
            snippet = old_text if len(old_text) <= 300 else old_text[:300] + "…"
            PENDING_DUP[message.from_user.id] = dict(data)
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="🔁 Заменить старую новой", callback_data=f"dup_replace:{old_id}"),
                    InlineKeyboardButton(text="Оставить", callback_data="dup_keep"),
                ]]
            )
            await message.answer_photo(
                photo,
                caption=f"⚠️ Такой текст уже есть в базе.\n🔗 {link}\n\n{snippet}"[:1024],
                reply_markup=markup,
            )
        else:
            await db.execute(
                "INSERT INTO reviews (text, text_norm, photo, link) VALUES (?, ?, ?, ?)",
                (data["text"], tn, data["photo"], data["link"]),
            )
            await db.commit()
            mark_dirty()
            await message.answer("✅ Сохранено в базу.", reply_markup=kb())
    await state.clear()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Как пользоваться:\n\n"
        "• Добавить отзыв: перешли скрин, текст и ссылку (каждое отдельным сообщением, в любом порядке). Из сообщения со ссылкой бот берёт только ссылку. "
        "Если начинаешь с текста — сначала нажми «➕ Добавить».\n"
        "• Найти: просто отправь текст отзыва — пришлю скрин и ссылку, а отзыв попадёт в пройденные.\n"
        "• Кнопка «✅ Пройденные» — пройденные по порядку: ссылка и её скрины со ссылками.\n"
        "• Кнопка «↩️ Убрать из пройденных» — убрать отдельные отзывы из списка.\n"
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

    # группируем по ссылке, порядок — по первому пройденному
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
                media = [InputMediaPhoto(media=p, caption=link[:1024]) for p in chunk]
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
    n = await count_reviews()
    mark_dirty()
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
    mark_dirty()
    await call.message.edit_text(f"🔄 Сброшено пройденных: {n}. Можно начинать новый отчёт.")
    await call.answer()


@router.callback_query(F.data == "reset_no")
async def reset_no(call: CallbackQuery):
    await call.message.edit_text("Отменено, пройденные остались как были.")
    await call.answer()


@router.message(F.text == BTN_UNPASS)
async def unpass_list(message: Message):
    if not is_admin(message):
        return
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
                InlineKeyboardButton(text="❌ Убрать из пройденных", callback_data=f"unpass:{rid}")
            ]]
        )
        await message.answer_photo(photo, caption=f"🔗 {link}\n\n{snippet}"[:1024], reply_markup=markup)
        await asyncio.sleep(0.3)


@router.callback_query(F.data.startswith("unpass:"))
async def unpass(call: CallbackQuery):
    if ADMIN_IDS and call.from_user.id not in ADMIN_IDS:
        await call.answer()
        return
    rid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE reviews SET passed = 0, passed_at = NULL WHERE id = ?", (rid,))
        await db.commit()
    mark_dirty()
    try:
        await call.message.edit_caption(caption="↩️ Убрано из пройденных")
    except Exception:
        pass
    await call.answer("Убрано")


@router.callback_query(F.data.startswith("dup_replace:"))
async def dup_replace(call: CallbackQuery):
    old_id = int(call.data.split(":")[1])
    data = PENDING_DUP.pop(call.from_user.id, None)
    if not data:
        await call.answer("Данные устарели, добавь отзыв заново", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM reviews WHERE id = ?", (old_id,))
        await db.execute(
            "INSERT INTO reviews (text, text_norm, photo, link) VALUES (?, ?, ?, ?)",
            (data["text"], norm(data["text"]), data["photo"], data["link"]),
        )
        await db.commit()
    mark_dirty()
    try:
        await call.message.edit_caption(caption="🔁 Старая запись заменена новой.")
    except Exception:
        pass
    await call.answer("Заменено")


@router.callback_query(F.data == "dup_keep")
async def dup_keep(call: CallbackQuery):
    PENDING_DUP.pop(call.from_user.id, None)
    try:
        await call.message.edit_caption(caption="Оставил старую запись как есть.")
    except Exception:
        pass
    await call.answer()


@router.message(Command("id"))
async def my_id(message: Message):
    await message.answer(f"Твой Telegram ID: {message.from_user.id}")


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


@router.message(lambda m: bool(m.text) and extract_link(m) is not None)
async def auto_add_link(message: Message, state: FSMContext):
    await collect(message, state)


# ---------- поиск ----------

def similarity(q: str, tn: str) -> float:
    if q == tn:
        return 1.0
    short, long_ = sorted((q, tn), key=len)
    # один текст целиком внутри другого — только если они почти одной длины
    if len(short) >= 15 and short in long_ and len(short) / len(long_) >= 0.8:
        return 0.97
    return SequenceMatcher(None, q, tn).ratio()


@router.callback_query(F.data.startswith("pass:"))
async def pass_one(call: CallbackQuery):
    if ADMIN_IDS and call.from_user.id not in ADMIN_IDS:
        await call.answer()
        return
    rid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reviews SET passed = 1, passed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND passed = 0",
            (rid,),
        )
        await db.commit()
    mark_dirty()
    try:
        await call.message.edit_caption(caption="✅ Отмечен как пройденный")
    except Exception:
        pass
    await call.answer("Отмечено")


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
            score = similarity(q, tn)
            if score >= MAYBE_THRESHOLD:
                scored.append((score, rid, text, photo, link, passed))

        if not scored:
            await message.answer("Такого текста в базе нет.")
            return

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0]

        # уверенное совпадение — отмечаем пройденным сами
        if best[0] >= AUTO_THRESHOLD:
            score, rid, text, photo, link, passed = best
            upd = await db.execute(
                "UPDATE reviews SET passed = 1, passed_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND passed = 0",
                (rid,),
            )
            changed = upd.rowcount
            await db.commit()
            if changed:
                mark_dirty()
            snippet = text if len(text) <= 300 else text[:300] + "…"
            mark = "✅ Уже в пройденных" if passed else "✅ Отмечен как пройденный"
            caption = f"{mark}\n🔗 {link}\n\nСовпадение: {int(score * 100)}%\n\n{snippet}"
            await message.answer_photo(photo, caption=caption[:1024])
            return

    # неуверенно — ничего не отмечаем, даём выбрать
    await message.answer("Точного совпадения нет. Возможно, это один из них:")
    for score, rid, text, photo, link, passed in scored[:MAX_RESULTS]:
        snippet = text if len(text) <= 300 else text[:300] + "…"
        caption = f"🔗 {link}\n\nСовпадение: {int(score * 100)}%\n\n{snippet}"
        markup = None
        if not passed:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Это он, отметить", callback_data=f"pass:{rid}")
                ]]
            )
        else:
            caption = "✅ Уже в пройденных\n" + caption
        await message.answer_photo(photo, caption=caption[:1024], reply_markup=markup)


async def main():
    if not TOKEN:
        raise SystemExit("Укажи токен в переменной окружения BOT_TOKEN (в настройках хостинга)")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(TOKEN)
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS не задан: автокопия базы в Telegram выключена")
    await auto_restore(bot)
    backup_task = asyncio.create_task(backup_loop(bot))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        backup_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
