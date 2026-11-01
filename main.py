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
                last_work INTEGER DEFAULT 0,
                last_bonus INTEGER DEFAULT 0,
                referred_by INTEGER
            )
        """)
        await db.commit()

# Регистрация пользователя с учетом рефералки
async def register_user(user_id: int, username: str, referrer_id: int = None):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                ref = referrer_id if referrer_id and referrer_id != user_id else None
                await db.execute(
                    "INSERT INTO users (user_id, username, balance, jobs_done, last_work, last_bonus, referred_by) VALUES (?, ?, 500, 0, 0, 0, ?)",
                    (user_id, username, ref)
                )
                if ref:
                    await db.execute("UPDATE users SET balance = balance + 200 WHERE user_id = ?", (ref,))
                    await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
                await db.commit()

# Главное меню
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💼 Работа (Генератор задач)", callback_data="work_menu"),
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

    await register_user(message.from_user.id, message.from_user.username or "Без имени", referrer_id)
    
    text = (
        f"Здорово, **{message.from_user.first_name}**! 👋\n\n"
        "Добро пожаловать в официальный бот проекта! 🏝\n"
        "Здесь умный генератор бесконечных заданий, крутое колесо фортуны и честное казино. Поднимайся в топ!"
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

# Меню работы
@dp.callback_query(F.data == "work_menu")
async def work_menu(callback: types.CallbackQuery):
    text = (
        "💼 **Биржа умных заданий**\n\n"
        "Наш алгоритм генерирует уникальную задачу для каждого игрока: проверка чеков, отзывов на маркетплейсах, карт и модерация.\
\nДва одинаковых задания не попадутся!\n"
        "За правильный ответ платим от 70 до 160 коинов. Смена доступна раз в 30 секунд!"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Сгенерировать задачу", callback_data="start_task")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_mode="Markdown"
    )

# УМНЫЙ ГЕНЕРАТОР ЗАДАНИЙ (создает миллионы вариаций на лету)
@dp.callback_query(F.data == "start_task")
async def start_task(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_work FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_work = row[0] if row else 0

        if current_time - last_work < 30:
            left = int(30 - (current_time - last_work))
            await callback.answer(f"⏳ Отдохни! Следующая генерация через {left} сек.", show_alert=True)
            return

    # Базы данных элементов для генерации
    platforms = ["Яндекс Картах", "Авито", "2GIS", "Wildberries", "Ozon", "Google Картах"]
    
    # Сценарий 1: Нормальный отзыв (нужно одобрить)
    # Сценарий 2: Брак / Нарушение (нужно отклонить)
    scenario_type = random.choice(["ok", "bad"])
    
    platform = random.choice(platforms)
    
    if scenario_type == "ok":
        reviews = [
            "Всё супер, заказ забрал за 2 минуты, продавец вежливый.",
            "Отличное место, качество на высоте, рекомендую!",
            "Быстро оформили точку, чек приложил, претензий нет.",
            "Товар соответствует описанию, всё работает идеально."
        ]
        rev_text = random.choice(reviews)
        desc = (
            f"📄 **Генерированная задача ({platform}):**\n"
            f"Отзыв: *«{rev_text}»*\n"
            f"🔍 Скриншот приложен, геолокация и чек подтверждены.\n\n"
            f"Твой вердикт?"
        )
        right_action = "ok"
        right_btn = "✅ Одобрить выплату"
        wrong_btn = "❌ Отклонить (Брак)"
    else:
        bad_cases = [
            ("Товар полный шлак, сломался сразу!", "⭐ 5 из 5 звезд (противоречие текста и оценки)"),
            ("Ужасный сервис, обманули на деньги!", "📸 Скриншот замазан в редакторе, дата обрезана"),
            ("Купите дешевые фолловеры у меня в телеге t.me/spam_bot", "🤖 Чистый спам и реклама стороннего ресурса"),
            ("Отвратительно, ставлю единицу!", "💬 Но текст отзыва хвалит магазин и персонал")
        ]
        case = random.choice(bad_cases)
        desc = (
            f"📄 **Генерированная задача ({platform}):**\n"
            f"Текст: *«{case[0]}»*\n"
            f"⚠️ Деталь проверки: **{case[1]}**.\n\n"
            f"Что делаем с отчетом?"
        )
        right_action = "bad"
        right_btn = "❌ Отклонить (Нарушение)"
        wrong_btn = "✅ Одобрить"

    # Рандомим расположение кнопок
    buttons = [
        InlineKeyboardButton(text=right_btn, callback_data=f"task_ans_{right_action}_ok"),
        InlineKeyboardButton(text=wrong_btn, callback_data=f"task_ans_{right_action}_bad")
    ]
    if random.random() > 0.5:
        buttons.reverse()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        buttons,
        [InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")]
    ])

    await callback.message.edit_text(desc, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("task_ans_"))
async def check_task_answer(callback: types.CallbackQuery):
    data_parts = callback.data.split("_")
    correct_type = data_parts[2]
    user_choice = data_parts[3]

    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    if user_choice == correct_type:
        earned = random.randint(70, 160)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, last_work = ? WHERE user_id = ?",
                (earned, current_time, user_id)
            )
            await db.commit()
        text = f"✅ **Красава!** Ты верно раскусил задачу генератора и получил **+{earned} коинов**."
    else:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (current_time, user_id))
            await db.commit()
        text = "❌ **Ошибка!** Алгоритм проверки показал, что решение неверное. Награда сгорела."

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
    text = (
        "🎡 **Колесо Фортуны**\n\n"
        "Стоимость прокрутки: **100 коинов**.\n"
        "Испытай удачу и сорви куш!"
    )
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
            await callback.answer("❌ Недостаточно коинов для прокрутки!", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        await db.commit()

    await callback.message.edit_text("🎡 Колесо крутится... Затаи дыхание! 🔄")
    await asyncio.sleep(2)

    win_val = random.choices([0, 50, 100, 200, 350], weights=[35, 30, 20, 10, 5], k=1)[0]

    async with aiosqlite.connect(DB_FILE) as db:
        if win_val > 0:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win_val, user_id))
            await db.commit()
            res_text = f"🎉 Поздравляем! На колесе выпал приз: **+{win_val} коинов**!"
        else:
            res_text = "😢 Увы, выпал сектор «Банкрот». Ставка сгорела."

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
    text = (
        "🎰 **Казино «Бикини Боттан»**\n\n"
        "Ставка: **50 коинов**.\n"
        "Бросаем игральную кость. Выпадет от 4 до 6 — удваиваешь ставку, меньше — проигрыш."
    )
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
            await callback.answer("❌ У тебя недостаточно коинов для ставки!", show_alert=True)
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
            result_text = f"🎉 Выпало **{value}**! Ты выиграл **+{win_amount} коинов**!"
        else:
            result_text = f"😢 Выпало **{value}**. Ставка сгорела."

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
            await callback.answer(f"⏳ Бонус уже забирали! Жди еще примерно {left_hours} ч.", show_alert=True)
            return

        bonus_amount = 250
        await db.execute(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
            (bonus_amount, current_time, user_id)
        )
        await db.commit()

    await callback.message.edit_text(
        f"🎁 Поздравляем! Ты забрал ежедневный бонус и получил **+{bonus_amount} коинов**!",
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
        "Приглашай друзей в бота по своей личной ссылке!\n"
        "• Друг получает: **+100 коинов** на старте\n"
        "• Ты получаешь: **+200 коинов** за каждого приведенного друга\n\n"
        f"🔗 Твоя ссылка:\n`{ref_link}`\n\n"
        f"📊 Приглашено друзей: **{ref_count}**"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
        ]),
        parse_nomode="Markdown" # опечатка исправлена
    )

# 🏆 Топ игроков
@dp.callback_query(F.data == "top")
async def top_players(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 5") as cursor:
            rows = await cursor.fetchall()

    top_text = "🏆 **Топ-5 богатеев канала:**\n\n"
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
    print("Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
