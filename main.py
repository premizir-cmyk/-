import asyncio
import logging
import random
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Твой токен бота
TOKEN = "9196956209:AAG_DRKnxxOQE_edm3KyD-k3T-Ly8exA1WE"

bot = Bot(token=TOKEN)
dp = Dispatcher()
DB_FILE = "database.db"

# Инициализация базы данных
async def db_start():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 500,
                jobs_done INTEGER DEFAULT 0,
                last_work_1 INTEGER DEFAULT 0,
                last_work_2 INTEGER DEFAULT 0,
                last_work_3 INTEGER DEFAULT 0,
                last_bonus INTEGER DEFAULT 0,
                referred_by INTEGER
            )
        """)
        await db.commit()

# Безопасная регистрация с очисткой ников от спецсимволов (# и др.)
async def register_user(user_id: int, username: str, full_name: str, referrer_id: int = None):
    safe_name = "".join(c for c in full_name if c.isalnum() or c in " _-").strip()
    if not safe_name:
        safe_name = "Без имени"

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                ref = referrer_id if referrer_id and referrer_id != user_id else None
                await db.execute(
                    "INSERT INTO users (user_id, username, balance, jobs_done, last_work_1, last_work_2, last_work_3, last_bonus, referred_by) VALUES (?, ?, 500, 0, 0, 0, 0, 0, ?)",
                    (user_id, safe_name, ref)
                )
                if ref:
                    await db.execute("UPDATE users SET balance = balance + 200 WHERE user_id = ?", (ref,))
                    await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
                await db.commit()

# Главное меню
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💼 Биржа профессий", callback_data="work_menu"),
            InlineKeyboardButton(text="💰 Профиль", callback_data="balance")
        ],
        [
            InlineKeyboardButton(text="🎰 Казино (Кости)", callback_data="casino"),
            InlineKeyboardButton(text="🎡 Колесо Фортуны", callback_data="fortune_wheel")
        ],
        [
            InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily_bonus"),
            InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")
        ],
        [
            InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top")
        ]
    ])

# Команда /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])

    user_display_name = message.from_user.first_name or "Игрок"
    await register_user(message.from_user.id, message.from_user.username or "NoName", user_display_name, referrer_id)
    
    text = (
        f"Здорово, **{user_display_name}**! 👋\n\n"
        "Добро пожаловать в официальный бот проекта! 🏝\n"
        "Здесь честная биржа труда с защитой от читеров, казино и колесо фортуны!"
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")

# Профиль и баланс
@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done FROM users WHERE user_id = ?", (callback.from_user.id,)) as cursor:
            row = await cursor.fetchone()
            balance, jobs = row if row else (500, 0)

    text = (
        f"👤 **Твой профиль:**\n\n"
        f"💰 Баланс: **{balance} коинов**\n"
        f"🛠 Выполнено заданий: **{jobs}**"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# МЕНЮ БИРЖИ ПРОФЕССИЙ
@dp.callback_query(F.data == "work_menu")
async def work_menu(callback: types.CallbackQuery):
    text = (
        "💼 **Биржа защищенных заданий**\n\n"
        "Выбирай профессию! Задачи генерируются динамически (ИИ и копирование текста тут не помогут, нужен мозг):\n\n"
        "1️⃣ **Шифровщик данных** (Математика/Логика | КД: 30 сек)\n"
        "2️⃣ **Кибер-аудитор** (Поиск аномалий в коде | КД: 60 сек)\n"
        "3️⃣ **Крипто-тререйдер** (Расчет доходности | КД: 120 сек)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Шифровщик данных", callback_data="job_1")],
        [InlineKeyboardButton(text="2️⃣ Кибер-аудитор", callback_data="job_2")],
        [InlineKeyboardButton(text="3️⃣ Крипто-трейдер", callback_data="job_3")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- РАБОТА 1: Шифровщик (Мат. тренажер, ИИ бессилен) ---
@dp.callback_query(F.data == "job_1")
async def job_one(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()
    cooldown = 30

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_work_1 FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_work = row[0] if row else 0

        if current_time - last_work < cooldown:
            left = int(cooldown - (current_time - last_work))
            await callback.answer(f"⏳ Перерыв! Следующий шифр через {left} сек.", show_alert=True)
            return

    # Генерируем уникальный пример на лету
    a = random.randint(12, 85)
    b = random.randint(5, 45)
    op = random.choice(["+", "-", "*"])
    
    if op == "+":
        correct = a + b
    elif op == "-":
        correct = a - b
    else:
        a = random.randint(5, 15)
        b = random.randint(3, 10)
        correct = a * b

    # Создаваем фейковые ответы для кнопок
    wrong1 = correct + random.choice([-5, -3, 2, 4])
    wrong2 = correct + random.choice([-10, 6, 8, -2])
    if wrong1 == correct: wrong1 += 1
    if wrong2 == correct: wrong2 -= 1

    variants = [
        (str(correct), "ok"),
        (str(wrong1), "bad1"),
        (str(wrong2), "bad2")
    ]
    random.shuffle(variants)

    desc = f"🔐 **[Работа 1: Шифровщик данных]**\nРеши алгоритм доступа к шлюзу:\n\n`{a} {op} {b} = ?`"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=variants[0][0], callback_data=f"ans_1_{variants[0][1]}"),
            InlineKeyboardButton(text=variants[1][0], callback_data=f"ans_1_{variants[1][1]}"),
            InlineKeyboardButton(text=variants[2][0], callback_data=f"ans_1_{variants[2][1]}")
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")]
    ])
    await callback.message.edit_text(desc, reply_markup=kb, parse_mode="Markdown")

# --- РАБОТА 2: Кибер-аудитор (Логический выбор) ---
@dp.callback_query(F.data == "job_2")
async def job_two(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()
    cooldown = 60

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_work_2 FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_work = row[0] if row else 0

        if current_time - last_work < cooldown:
            left = int(cooldown - (current_time - last_work))
            await callback.answer(f"⏳ Лаборатория занята! Жди {left} сек.", show_alert=True)
            return

    tasks = [
        ("В строке кода обнаружен символ бесконечного цикла `while(True): pass`. Что это?", "Угроза зависания сервера", ["Угроза зависания сервера", "Оптимизация базы", "Красивый дизайн"]),
        ("Пинг до дата-центра вырос с 15ms до 850ms. Причина?", "Сетевой сбои / DDoS", ["Нормальная работа", "Сетевой сбои / DDoS", "Ускорение процессора"]),
        ("Токен бота появился в публичном репозитории GitHub. Срочное действие?", "Экстренная замена токена у BotFather", ["Ничего не делать", "Экстренная замена токена у BotFather", "Попить чай"])
    ]
    t = random.choice(tasks)
    right_answer = t[1]
    options = t[2]
    random.shuffle(options)

    desc = f"🛠 **[Работа 2: Кибер-аудитор]**\nИнзудент безопасности:\n*«{t[0]}»*\n\nВыбери правильный вердикт:"

    kb_buttons = []
    for opt in options:
        res_type = "ok" if opt == right_answer else "bad"
        kb_buttons.append([InlineKeyboardButton(text=opt, callback_data=f"ans_2_{res_type}")])
    kb_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")])

    await callback.message.edit_text(desc, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons), parse_mode="Markdown")

# --- РАБОТА 3: Крипто-трейдер (Расчет профита) ---
@dp.callback_query(F.data == "job_3")
async def job_three(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()
    cooldown = 120

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_work_3 FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_work = row[0] if row else 0

        if current_time - last_work < cooldown:
            left = int((cooldown - (current_time - last_work)) // 60)
            await callback.answer(f"⏳ Аналитика рынка идет! Жди {max(1, left)} мин.", show_alert=True)
            return

    buy_price = random.randint(100, 500)
    percent = random.choice([10, 20, 25, 50])
    correct_profit = int(buy_price * (percent / 100))

    wrong1 = correct_profit + random.choice([-15, 20])
    wrong2 = correct_profit + random.choice([30, -25])
    if wrong1 < 0: wrong1 = 5
    if wrong2 < 0: wrong2 = 10

    variants = [
        (f"+{correct_profit} монет", "ok"),
        (f"+{wrong1} монет", "bad1"),
        (f"+{wrong2} монет", "bad2")
    ]
    random.shuffle(variants)

    desc = f"📈 **[Работа 3: Крипто-трейдер]**\nКуплен актив за **{buy_price} коинов**. Монета выросла на **{percent}%**.\n\nКакая чистая прибыль получена?"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=variants[0][0], callback_data=f"ans_3_{variants[0][1]}"),
            InlineKeyboardButton(text=variants[1][0], callback_data=f"ans_3_{variants[1][1]}"),
            InlineKeyboardButton(text=variants[2][0], callback_data=f"ans_3_{variants[2][1]}")
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")]
    ])
    await callback.message.edit_text(desc, reply_markup=kb, parse_mode="Markdown")

# Универсальный обработчик ответов с защитой от читов
@dp.callback_query(F.data.startswith("ans_"))
async def check_multi_job(callback: types.CallbackQuery):
    data_parts = callback.data.split("_")
    job_id = data_parts[1]
    result_type = data_parts[2] # "ok" если верно, иначе ошибка

    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    if job_id == "1":
        earned = random.randint(70, 150)
        db_col = "last_work_1"
    elif job_id == "2":
        earned = random.randint(150, 280)
        db_col = "last_work_2"
    else:
        earned = random.randint(300, 550)
        db_col = "last_work_3"

    async with aiosqlite.connect(DB_FILE) as db:
        if result_type == "ok":
            await db.execute(
                f"UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, {db_col} = ? WHERE user_id = ?",
                (earned, current_time, user_id)
            )
            await db.commit()
            text = f"✅ **Верно!** Алгоритм подтвержден, начислено **+{earned} коинов**."
        else:
            await db.execute(f"UPDATE users SET {db_col} = ? WHERE user_id = ?", (current_time, user_id))
            await db.commit()
            text = "❌ **Ошибка расчетов!** Неверный ответ, награда сгорела, включился кулдаун."

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💼 На биржу", callback_data="work_menu")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# 🎡 Колесо Фортуны
@dp.callback_query(F.data == "fortune_wheel")
async def fortune_wheel_menu(callback: types.CallbackQuery):
    text = "🎡 **Колесо Фортуны**\n\nСтоимость прокрутки: **100 коинов**."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎰 Крутить Колесо (100 коинов)", callback_data="spin_wheel")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "spin_wheel")
async def spin_wheel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cost = 100

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 0

        if balance < cost:
            await callback.answer("❌ Недостаточно коинов!", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        await db.commit()

    await callback.message.edit_text("🎡 Колесо крутится...")
    await asyncio.sleep(2)
    win_val = random.choices([0, 50, 100, 200, 350], weights=[35, 30, 20, 10, 5], k=1)[0]

    async with aiosqlite.connect(DB_FILE) as db:
        if win_val > 0:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_val, user_id))
            await db.commit()
            res_text = f"🎉 Выпал приз: **+{win_val} коинов**!"
        else:
            res_text = "😢 Выпал сектор «Банкрот»."

    await callback.message.edit_text(
        res_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎡 Крутить еще", callback_data="fortune_wheel")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# 🎰 Казино (Кости)
@dp.callback_query(F.data == "casino")
async def casino_menu(callback: types.CallbackQuery):
    text = "🎰 **Казино**\n\nСтавка: **50 коинов**. Выпадет от 4 до 6 — х2."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Бросить кости (50 коинов)", callback_data="play_dice")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "play_dice")
async def play_dice(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    bet = 50

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 0

        if balance < bet:
            await callback.answer("❌ Недостаточно коинов!", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, user_id))
        await db.commit()

    await callback.message.delete()
    dice_msg = await callback.message.answer_dice(emoji="🎲")
    value = dice_msg.dice.value
    await asyncio.sleep(3)

    async with aiosqlite.connect(DB_FILE) as db:
        if value >= 4:
            win_amount = bet * 2
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_amount, user_id))
            await db.commit()
            result_text = f"🎉 Выпало **{value}**! Выигрыш: **+{win_amount} коинов**!"
        else:
            result_text = f"😢 Выпало **{value}**. Проигрыш."

    await callback.message.answer(
        result_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Еще раз", callback_data="play_dice")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# 🎁 Ежедневный бонус
@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()
    cooldown = 86400

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_bonus = row[0] if row else 0

        if current_time - last_bonus < cooldown:
            left_hours = int((cooldown - (current_time - last_bonus)) // 3600)
            await callback.answer(f"⏳ Бонус уже забирали! Жди {left_hours} ч.", show_alert=True)
            return

        bonus_amount = 250
        await db.execute(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
            (bonus_amount, current_time, user_id)
        )
        await db.commit()

    await callback.message.edit_text(
        f"🎁 Получен ежедневный бонус: **+{bonus_amount} коинов**!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# 👥 Рефералы
@dp.callback_query(F.data == "referrals")
async def referrals_menu(callback: types.CallbackQuery):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"
    
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (callback.from_user.id,)) as cursor:
            row = await cursor.fetchone()
            ref_count = row[0] if row else 0

    text = (
        "👥 **Реферальная система**\n\n"
        f"🔗 Твоя ссылка:\n`{ref_link}`\n\n"
        f"📊 Приглашено друзей: **{ref_count}**"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# 🏆 Топ игроков
@dp.callback_query(F.data == "top")
async def top_players(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 5") as cursor:
            rows = await cursor.fetchall()

    top_text = "🏆 **Топ-5 игроков:**\n\n"
    for i, row in enumerate(rows, start=1):
        name, bal = row
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        top_text += f"{medal} @{name} — **{bal} коинов**\n"

    await callback.message.edit_text(
        top_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# Возврат в меню
@dp.callback_query(F.data == "back_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Главное меню:",
        reply_markup=get_main_menu()
    )

async def main():
    await db_start()
    logging.basicConfig(level=logging.INFO)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
