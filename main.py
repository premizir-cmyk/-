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

# Инициализация базы данных с дополнительными полями под кулдауны разных работ
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

# Безопасная регистрация пользователя
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
        "Выбирай работу на бирже профессий, крути колесо фортуны и поднимайся в топ!"
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

# МЕНЮ БИРЖИ ПРОФЕССИЙ (Выбор работы)
@dp.callback_query(F.data == "work_menu")
async def work_menu(callback: types.CallbackQuery):
    text = (
        "💼 **Биржа профессий и заработка**\n\n"
        "Выбирай направление! У каждой работы своя сложность, награда и персональный таймер (кулдаун):\n\n"
        "1️⃣ **Модератор отзывов** (КД: 30 сек | Награда: 60-150 коинов)\n"
        "2️⃣ **Тестировщик багов** (КД: 60 сек | Награда: 120-250 коинов)\n"
        "3️⃣ **Крипто-аналитик** (КД: 120 сек | Награда: 250-500 коинов)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Модератор отзывов", callback_data="job_1")],
        [InlineKeyboardButton(text="2️⃣ Тестировщик багов", callback_data="job_2")],
        [InlineKeyboardButton(text="3️⃣ Крипто-аналитик", callback_data="job_3")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- РАБОТА 1: Модератор отзывов (КД 30 сек) ---
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
            await callback.answer(f"⏳ Отдохни! Смена модератора будет доступна через {left} сек.", show_alert=True)
            return

    platforms = ["Яндекс Карты", "Авито", "2GIS", "Google Карты"]
    scenario = random.choice(["ok", "bad"])
    plat = random.choice(platforms)

    if scenario == "ok":
        desc = f"🔍 **[Работа 1: Модератор ({plat})]**\nОтзыв: *«Всё отлично, быстро забрал заказ, персонал вежливый»*.\n✅ Чек и геолокация подтверждены.\n\nЧто делаем?"
        right_ans = "ok"
        r_btn, w_btn = "✅ Одобрить", "❌ Отклонить"
    else:
        desc = f"🔍 **[Работа 1: Модератор ({plat})]**\nОтзыв: *«Ужасный товар, всё сломалось!»*, но оценка ⭐⭐⭐⭐⭐ (5 звезд).\n\nПротиворечие в отчете. Что делаем?"
        right_ans = "bad"
        r_btn, w_btn = "❌ Отклонить (Брак)", "✅ Одобрить"

    buttons = [
        InlineKeyboardButton(text=r_btn, callback_data=f"ans_1_{right_ans}_ok"),
        InlineKeyboardButton(text=w_btn, callback_data=f"ans_1_{right_ans}_bad")
    ]
    if random.random() > 0.5:
        buttons.reverse()

    kb = InlineKeyboardMarkup(inline_keyboard=[buttons, [InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")]])
    await callback.message.edit_text(desc, reply_markup=kb, parse_mode="Markdown")

# --- РАБОТА 2: Тестировщик багов (КД 60 сек) ---
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
            await callback.answer(f"⏳ Тест-лаборатория закрыта! Следующий тест через {left} сек.", show_alert=True)
            return

    bugs = [
        ("🐛 Баг в корзине: при нажатии 'Оплатить' сумма уходит в минус.", "critical", "🚨 Передать в баг-трекер"),
        ("📱 Металлический баг интерфейса: съехала кнопка профиля на iPhone.", "visual", "🎨 На оформление дизайнеру"),
        ("⚡ Сервер стабилен, логов ошибок нет, нагрузка 2%.", "stable", "✅ Закрыть тикет (Все ОК)")
    ]
    bug = random.choice(bugs)
    
    desc = f"🛠 **[Работа 2: Тестировщик ПО]**\nЛог системы: *{bug[0]}*\n\nОпредели статус проблемы:"
    
    buttons = [
        InlineKeyboardButton(text=bug[2], callback_data=f"ans_2_{bug[1]}_ok"),
        InlineKeyboardButton(text="❌ Ложный баг", callback_data=f"ans_2_{bug[1]}_bad")
    ]
    if random.random() > 0.5:
        buttons.reverse()

    kb = InlineKeyboardMarkup(inline_keyboard=[buttons, [InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")]])
    await callback.message.edit_text(desc, reply_markup=kb, parse_mode="Markdown")

# --- РАБОТА 3: Крипто-аналитик (КД 120 сек) ---
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
            await callback.answer(f"⏳ Рынок анализируется! Жди еще примерно {max(1, left)} мин.", show_alert=True)
            return

    coins_list = ["TON", "USDT", "BTC", "NOT"]
    c_name = random.choice(coins_list)
    trend = random.choice(["bull", "bear"])

    if trend == "bull":
        desc = f"🚀 **[Работа 3: Крипто-анализ ({c_name})]**\nГрафик пробил уровень сопротивления, объемы торгов растут на 300%!\n\nТвой прогноз по тренду?"
        right_ans = "bull"
        r_btn, w_btn = "📈 Ланг (Рост)", "📉 Шорт (Падение)"
    else:
        desc = f"📉 **[Работа 3: Крипто-анализ ({c_name})]**\nКрупный кит сливает актив на бирже, поддержка пробита, паника на рынке.\n\nТвой прогноз?"
        right_ans = "bear"
        r_btn, w_btn = "📉 Шорт (Падение)", "📈 Ланг (Рост)"

    buttons = [
        InlineKeyboardButton(text=r_btn, callback_data=f"ans_3_{right_ans}_ok"),
        InlineKeyboardButton(text=w_btn, callback_data=f"ans_3_{right_ans}_bad")
    ]
    if random.random() > 0.5:
        buttons.reverse()

    kb = InlineKeyboardMarkup(inline_keyboard=[buttons, [InlineKeyboardButton(text="❌ Отмена", callback_data="work_menu")]])
    await callback.message.edit_text(desc, reply_markup=kb, parse_mode="Markdown")

# Универсальный обработчик ответов на все три работы
@dp.callback_query(F.data.startswith("ans_"))
async def check_multi_job(callback: types.CallbackQuery):
    data_parts = callback.data.split("_")
    job_id = data_parts[1]       # 1, 2 или 3
    correct_type = data_parts[2] # правильный тип
    user_choice = data_parts[3]  # ok или bad

    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    # Награды и таймеры в зависимости от работы
    if job_id == "1":
        earned = random.randint(60, 150)
        db_col = "last_work_1"
    elif job_id == "2":
        earned = random.randint(120, 250)
        db_col = "last_work_2"
    else:
        earned = random.randint(250, 500)
        db_col = "last_work_3"

    async with aiosqlite.connect(DB_FILE) as db:
        if user_choice == "ok":
            await db.execute(
                f"UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, {db_col} = ? WHERE user_id = ?",
                (earned, current_time, user_id)
            )
            await db.commit()
            text = f"✅ **Отличная работа!** Задача выполнена успешно, начислено **+{earned} коинов**."
        else:
            await db.execute(f"UPDATE users SET {db_col} = ? WHERE user_id = ?", (current_time, user_id))
            await db.commit()
            text = "❌ **Ошибка в расчетах!** Задание выполнено неверно, оплата сгорела, кулдаун активирован."

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💼 Вернуться на биржу", callback_data="work_menu")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_menu")]
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
        parse_mode="Markdown"
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
