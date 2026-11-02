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

# Инициализация базы данных (сохранение при перезапусках)
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
                story_completed INTEGER DEFAULT 0,
                referred_by INTEGER
            )
        """)
        await db.commit()

# Регистрация пользователя с защитой баланса от перезаписи
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
                    "INSERT INTO users (user_id, username, balance, jobs_done, last_work, last_bonus, story_completed, referred_by) VALUES (?, ?, 500, 0, 0, 0, 0, ?)",
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
            InlineKeyboardButton(text="🍔 Повар в Красти Краб", callback_data="chef_work"),
            InlineKeyboardButton(text="📖 Сюжетный квест", callback_data="story_start")
        ],
        [
            InlineKeyboardButton(text="🎰 Казино (Кости)", callback_data="casino"),
            InlineKeyboardButton(text="🎡 Колесо Фортуны", callback_data="fortune_wheel")
        ],
        [
            InlineKeyboardButton(text="🎯 Тир по медузам", callback_data="game_jelly"),
            InlineKeyboardButton(text="✂️ КНБ с Патриком", callback_data="game_rps")
        ],
        [
            InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily_bonus"),
            InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")
        ],
        [
            InlineKeyboardButton(text="💰 Профиль", callback_data="balance"),
            InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top")
        ]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])

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
        "Добро пожаловать в Бикини Боттон! Ошибка исправлена, бот успешно запущен и работает."
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done, story_completed FROM users WHERE user_id = ?", (callback.from_user.id,)) as cursor:
            row = await cursor.fetchone()
            balance, jobs, story = row if row else (500, 0, 0)

    story_status = "✅ Пройден" if story == 1 else "❌ Не пройден"
    text = (
        f"👤 **Твой профиль:**\n\n"
        f"💰 Баланс: **{balance} коинов**\n"
        f"🛠 Выполнено смен: **{jobs}**\n"
        f"📖 Сюжетный квест: **{story_status}**"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")


# --- МИНИ-ИГРА: ПОВАР В «КРАСТИ КРАБ» (С таймером 5 сек и рандомом) ---
@dp.callback_query(F.data == "chef_work")
async def chef_work_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()
    cooldown = 45

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT last_work FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_work = row[0] if row else 0

        if current_time - last_work < cooldown:
            left = int(cooldown - (current_time - last_work))
            await callback.answer(f"⏳ Кухня занята! Следующая смена через {left} сек.", show_alert=True)
            return

    ingredients = [
        ("bun_top", "🍞 Верхняя булочка"),
        ("meat", "🥩 Сочная котлета"),
        ("cheese", "🧀 Сыр"),
        ("salad", "🥬 Салат лист"),
        ("bun_bot", "🍞 Нижняя булочка")
    ]
    
    correct_item = random.choice(ingredients)
    correct_key = correct_item[0]
    correct_name = correct_item[1]

    await callback.message.edit_text(
        f"🍔 **Смена: Повар в «Красти Краб»**\n\n"
        f"📜 **Внимание, рецепт заказа:**\n"
        f"👉 Первым нужно положить: <b>{correct_name}</b>\n\n"
        f"⏱ У тебя есть **5 секунд** на запоминание...",
        parse_mode="HTML"
    )

    await asyncio.sleep(5)

    shuffled_options = list(ingredients)
    random.shuffle(shuffled_options)

    kb_buttons = []
    for key, name in shuffled_options:
        res_type = "win" if key == correct_key else "fail"
        kb_buttons.append([InlineKeyboardButton(text=name, callback_data=f"chef_{res_type}")])

    kb_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_menu")])

    await callback.message.edit_text(
        "🍳 **Сборка бургера началась!**\n\nЧто повар должен положить **первым** по памяти?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("chef_"))
async def chef_result_handler(callback: types.CallbackQuery):
    result = callback.data.split("_")[1]
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    async with aiosqlite.connect(DB_FILE) as db:
        if result == "win":
            reward = random.randint(110, 200)
            await db.execute(
                "UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, last_work = ? WHERE user_id = ?",
                (reward, current_time, user_id)
            )
            await db.commit()
            text = f"✅ **Заказ успешно отдан!** Бургер идеален. Награда: **+{reward} коинов** 🍔"
        else:
            await db.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (current_time, user_id))
            await db.commit()
            text = "❌ **Ошибка в рецепте!** Перепутал ингредиенты, заказ сгорел, кулдаун 45 секунд."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍔 На кухню", callback_data="chef_work")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


# --- МИНИ-ИГРА: ТИР ПО МЕДУЗАМ ---
@dp.callback_query(F.data == "game_jelly")
async def game_jelly_menu(callback: types.CallbackQuery):
    text = (
        "🎯 **Тир по медузам в Полях Медуз**\n\n"
        "Стоимость выстрела: **60 коинов**.\n"
        "• Обычная медуза: +100 коинов\n"
        "• Золотая королева: +250 коинов"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Стрелять! (60 коинов)", callback_data="shoot_jelly")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "shoot_jelly")
async def shoot_jelly(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    cost = 60

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 500

        if balance < cost:
            await callback.answer("❌ Недостаточно коинов!", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
        await db.commit()

    await callback.message.edit_text("🎯 Выстрел гарпуном... Летит медуза! 🪼")
    await asyncio.sleep(2)

    outcome = random.choices(["miss", "normal", "golden"], weights=[40, 45, 15], k=1)[0]

    async with aiosqlite.connect(DB_FILE) as db:
        if outcome == "miss":
            res_text = "💨 Промах! Медуза уплыла."
        elif outcome == "normal":
            win = 100
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await db.commit()
            res_text = f"🎯 Попадание! Выигрыш: **+{win} коинов**!"
        else:
            win = 250
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await db.commit()
            res_text = f"🌟 ДЖЕКПОТ! Золотая медуза! Выигрыш: **+{win} коинов**!"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Стрелять еще", callback_data="game_jelly")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(res_text, reply_markup=kb, parse_mode="Markdown")


# --- МИНИ-ИГРА: КАМЕНЬ, НОЖНИЦЫ, БУМАГА ---
@dp.callback_query(F.data == "game_rps")
async def game_rps_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🪨 Камень", callback_data="rps_rock"),
            InlineKeyboardButton(text="✂️ Ножницы", callback_data="rps_scissors"),
            InlineKeyboardButton(text="📄 Бумага", callback_data="rps_paper")
        ],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text("✂️ **КНБ против Патрика**\n\nСтавка: **50 коинов**. Победа — х2.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("rps_"))
async def play_rps(callback: types.CallbackQuery):
    user_choice = callback.data.replace("rps_", "")
    user_id = callback.from_user.id
    bet = 50

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 500

        if balance < bet:
            await callback.answer("❌ Недостаточно коинов!", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, user_id))
        await db.commit()

    bot_choice = random.choice(["rock", "scissors", "paper"])
    emoji_dict = {"rock": "🪨 Камень", "scissors": "✂️ Ножницы", "paper": "📄 Бумага"}

    if user_choice == bot_choice:
        res = "draw"
    elif (
        (user_choice == "rock" and bot_choice == "scissors") or
        (user_choice == "scissors" and bot_choice == "paper") or
        (user_choice == "paper" and bot_choice == "rock")
    ):
        res = "win"
    else:
        res = "lose"

    async with aiosqlite.connect(DB_FILE) as db:
        if res == "win":
            await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
            await db.commit()
            text = f"🎉 **Победа!**\nТы: {emoji_dict[user_choice]}\nПатрик: {emoji_dict[bot_choice]}\n\nВыигрыш: **+100 коинов**!"
        elif res == "draw":
            await db.execute("UPDATE users SET balance = balance + 50 WHERE user_id = ?", (user_id,))
            await db.commit()
            text = f"🤝 **Ничья!**\nТы: {emoji_dict[user_choice]}\nПатрик: {emoji_dict[bot_choice]}\n\nСтавка возвращена."
        else:
            text = f"😢 **Проигрыш!**\nТы: {emoji_dict[user_choice]}\nПатрик: {emoji_dict[bot_choice]}\n\nСтавка сгорела."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Еще раз", callback_data="game_rps")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


# --- СЮЖЕТНЫЙ КВЕСТ (Исправленный, без ложных сбросов) ---
@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT story_completed FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            completed = row[0] if row else 0

        if completed == 1:
            await callback.answer("⚠️ Квест уже пройден! Жди новые главы.", show_alert=True)
            return

    text = (
        "📖 **Глава 1: Пропажа на кухне**\n\n"
        "Ночью кто-то украл секретный экстракт из сейфа Мистера Крабса! С чего начнем расследование?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Обыскать кухню", callback_data="story_kitchen")],
        [InlineKeyboardButton(text="🕵️‍♂️ Допросить Губку Боба", callback_data="story_spongebob")],
        [InlineKeyboardButton(text="📹 Взломать камеры", callback_data="story_cameras")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "story_kitchen")
async def story_kitchen(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад к выбору", callback_data="story_start")]])
    await callback.message.edit_text("📖 Ты обыскал кухню, но нашел только старые банки из-под майонеза. Улик нет.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "story_spongebob")
async def story_spongebob(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад к выбору", callback_data="story_start")]])
    await callback.message.edit_text("📖 Губка Боб искренне плачет и клянется, что невиновен. Он спал всю ночь.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "story_cameras")
async def story_cameras(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="27", callback_data="story_win"), InlineKeyboardButton(text="25", callback_data="story_bad")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="story_start")]
    ])
    await callback.message.edit_text("📖 Ты зашел в кабинет и открыл камеры. Замок требует решить пример: **12 + 15 = ?**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "story_win")
async def story_win(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 500, story_completed = 1 WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text("🎉 **Победа!** Пароль подошел, ты поймал вора на камерах! Премия **+500 коинов** начислена в профиль!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "story_bad")
async def story_bad(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="story_cameras")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text("❌ Неверный пароль! Система заблокировалась. Попробуй еще раз.", reply_markup=kb, parse_mode="Markdown")


# Казино, Колесо, Бонусы, Топ, Рефералы
@dp.callback_query(F.data == "fortune_wheel")
async def fortune_wheel(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Крутить (100 коинов)", callback_data="spin_wheel")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])
    await callback.message.edit_text("🎡 **Колесо Фортуны**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "spin_wheel")
async def spin_wheel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))).fetchone()
        balance = row[0] if row else 500
        if balance < 100:
            await callback.answer("❌ Мало коинов!", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance - 100 WHERE user_id = ?", (user_id,))
        await db.commit()

    await callback.message.edit_text("🎡 Крутим...")
    await asyncio.sleep(2)
    win = random.choices([0, 50, 100, 200, 350], weights=[35, 30, 20, 10, 5], k=1)[0]
    
    async with aiosqlite.connect(DB_FILE) as db:
        if win > 0:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await db.commit()
            res = f"🎉 Выигрыш: **+{win} коинов**!"
        else:
            res = "😢 Пусто."
            
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]])
    await callback.message.edit_text(res, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "casino")
async def casino(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Бросить кости (50 коинов)", callback_data="play_dice")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])
    await callback.message.edit_text("🎰 **Казино**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "play_dice")
async def play_dice(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))).fetchone()
        balance = row[0] if row else 500
        if balance < 50:
            await callback.answer("❌ Мало коинов!", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance - 50 WHERE user_id = ?", (user_id,))
        await db.commit()

    await callback.message.delete()
    dice = await callback.message.answer_dice(emoji="🎲")
    val = dice.dice.value
    await asyncio.sleep(3)

    async with aiosqlite.connect(DB_FILE) as db:
        if val >= 4:
            await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
            await db.commit()
            res = f"🎉 Выпало {val}! Выигрыш: **+100 коинов**!"
        else:
            res = f"😢 Выпало {val}. Проигрыш."

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]])
    await callback.message.answer(res, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    now = asyncio.get_event_loop().time()
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,))).fetchone()
        last = row[0] if row else 0
        if now - last < 86400:
            await callback.answer("⏳ Бонус раз в 24 часа!", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance + 250, last_bonus = ? WHERE user_id = ?", (now, user_id))
        await db.commit()
    await callback.message.edit_text("🎁 Бонус получен: **+250 коинов**!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "referrals")
async def referrals(callback: types.CallbackQuery):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={callback.from_user.id}"
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (callback.from_user.id,))).fetchone()
        count = row[0] if row else 0
    await callback.message.edit_text(f"👥 **Рефералы**\n\nСсылка:\n`{link}`\n\nПриглашено: {count}", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "top")
async def top(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        rows = await (await db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 5")).fetchall()
    text = "🏆 **Топ-5 игроков:**\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. @{r[0]} — **{r[1]} коинов**\n"
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "back_menu")
async def back_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

async def main():
    await db_start()
    logging.basicConfig(level=logging.INFO)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
