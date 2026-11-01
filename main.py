import asyncio
import logging
import random
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = "9196956209:AAG_DRKnxxOQE_edm3KyD-k3T-Ly8exA1WE"

bot = Bot(token=TOKEN)
dp = Dispatcher()
DB_FILE = "database.db"

# Инициализация базы данных с сохранением (проверяет файл на диске)
async def db_start():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 500,
                jobs_done INTEGER DEFAULT 0,
                last_work INTEGER DEFAULT 0,
                last_bonus INTEGER DEFAULT 0,
                referred_by INTEGER
            )
        """)
        await db.commit()

# Безопасная регистрация пользователей без сброса существующих
async def register_user(user_id: int, username: str, full_name: str, referrer_id: int = None):
    safe_name = "".join(c for c in full_name if c.isalnum() or c in " _-").strip()
    if not safe_name:
        safe_name = "Без имени"

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id, balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                ref = referrer_id if referrer_id and referrer_id != user_id else None
                await db.execute(
                    "INSERT INTO users (user_id, username, balance, jobs_done, last_work, last_bonus, referred_by) VALUES (?, ?, 500, 0, 0, 0, ?)",
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
            InlineKeyboardButton(text="💼 Защищенная работа", callback_data="work_menu"),
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
        "Добро пожаловать в официальный бот! База данных теперь сохраняется надежно, а задания защищены от ИИ."
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

# МЕНЮ РАБОТЫ (Интерактивная капча, ИИ бессилен)
@dp.callback_query(F.data == "work_menu")
async def work_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_work FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_work = row[0] if row else 0

        if current_time - last_work < 25:
            left = int(25 - (current_time - last_work))
            await callback.answer(f"⏳ Перерыв! Следующая задача через {left} сек.", show_alert=True)
            return

    # Набор эмодзи для анти-ИИ капчи
    targets = [("🐱", "кота"), ("🚗", "машину"), ("🍎", "яблоко"), ("⭐", "звезду")]
    target_emoji, target_name = random.choice(targets)

    # Создаем варианты кнопок со случайными эмодзи
    distractors = ["🐶", "🍐", "🚀", "⚡", "🎸", "🍕", "⚽", "🌵"]
    chosen_distractors = random.sample(distractors, 2)
    
    options = [
        (target_emoji, "win"),
        (chosen_distractors[0], "fail1"),
        (chosen_distractors[1], "fail2")
    ]
    random.shuffle(options)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=options[0][0], callback_data=f"captcha_{options[0][1]}"),
            InlineKeyboardButton(text=options[1][0], callback_data=f"captcha_{options[1][1]}"),
            InlineKeyboardButton(text=options[2][0], callback_data=f"captcha_{options[2][1]}")
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_menu")]
    ])

    text = (
        f"🔒 **Защищенная проверка от ИИ-читеров**\n\n"
        f"Нажми на правильный символ на клавиатуре ниже:\n"
        f"👉 Найди и нажми: **{target_name} ({target_emoji})**"
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# Обработка проверки капчи
@dp.callback_query(F.data.startswith("captcha_"))
async def check_captcha(callback: types.CallbackQuery):
    result = callback.data.split("_")[1]
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    async with aiosqlite.connect(DB_FILE) as db:
        if result == "win":
            earned = random.randint(80, 160)
            await db.execute(
                "UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, last_work = ? WHERE user_id = ?",
                (earned, current_time, user_id)
            )
            await db.commit()
            text = f"✅ **Красава!** Капча пройдена, начислено **+{earned} коинов**."
        else:
            await db.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (current_time, user_id))
            await db.commit()
            text = "❌ **Ошибка!** Выбран не тот символ. Награда сгорела, включился кулдаун."

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💼 Новая задача", callback_data="work_menu")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
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
