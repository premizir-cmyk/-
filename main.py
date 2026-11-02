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

# Инициализация базы данных с защитой от сброса балансов
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
                story_status TEXT DEFAULT 'not_started',
                referred_by INTEGER
            )
        """)
        await db.commit()

# Безопасная регистрация (гарантирует, что существующие балансы не затрутся)
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
                    "INSERT INTO users (user_id, username, balance, jobs_done, last_work, last_bonus, story_status, referred_by) VALUES (?, ?, 500, 0, 0, 0, 'not_started', ?)",
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
        "Добро пожаловать в Бикини Боттон! Базы данных защищены от сбросов, квест полностью переписан в масштабную детективную историю с жесткими развилками!"
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done, story_status FROM users WHERE user_id = ?", (callback.from_user.id,)) as cursor:
            row = await cursor.fetchone()
            balance, jobs, status = row if row else (500, 0, 'not_started')

    if status == 'completed':
        story_text = "✅ Успешно пройден"
    elif status == 'failed':
        story_text = "❌ Провален"
    else:
        story_text = "⏳ В процессе / Не начат"

    text = (
        f"👤 **Твой профиль:**\n\n"
        f"💰 Баланс: **{balance} коинов**\n"
        f"🛠 Выполнено смен: **{jobs}**\n"
        f"📖 Сюжетный статус: **{story_text}**"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")


# --- МИНИ-ИГРА: ПОВАР (Запоминание ВСЕГО бургера) ---
chef_active_orders = {}

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

    layers_pool = [
        ("bun_top", "🍞 Верхняя булочка"),
        ("meat", "🥩 Котлета"),
        ("cheese", "🧀 Сыр"),
        ("salad", "🥬 Салат"),
        ("tomato", "🍅 Помидор"),
        ("sauce", " соус"),
        ("bun_bot", "🍞 Нижняя булочка")
    ]
    
    recipe = random.sample(layers_pool, 3)
    chef_active_orders[user_id] = {"recipe": recipe, "step": 0}

    recipe_text = " ➡️ ".join([item[1] for item in recipe])

    await callback.message.edit_text(
        f"🍔 **Смена: Повар в «Красти Краб»**\n\n"
        f"📜 **Запомни полный рецепт бургера:**\n"
        f"👉 <code>{recipe_text}</code>\n\n"
        f"⏱ У тебя есть **5 секунд** на запоминание...",
        parse_mode="HTML"
    )

    await asyncio.sleep(5)
    await show_chef_step(callback.message, user_id)

async def show_chef_step(message, user_id):
    order = chef_active_orders.get(user_id)
    if not order:
        return

    step = order["step"]
    recipe = order["recipe"]
    correct_key = recipe[step][0]

    options = [
        ("bun_top", "🍞 Верхняя булочка"),
        ("meat", "🥩 Котлета"),
        ("cheese", "🧀 Сыр"),
        ("salad", "🥬 Салат"),
        ("tomato", "🍅 Помидор"),
        ("sauce", " соус"),
        ("bun_bot", "🍞 Нижняя булочка")
    ]
    random.shuffle(options)

    kb_buttons = []
    for key, name in options:
        res_type = "next" if key == correct_key else "fail"
        kb_buttons.append([InlineKeyboardButton(text=name, callback_data=f"chefstep_{res_type}")])

    kb_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_menu")])

    await message.edit_text(
        f"🍳 **Сборка бургера (Шаг {step + 1} из {len(recipe)}):**\n\n"
        f"Какой слой должен идти по памяти?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("chefstep_"))
async def chef_step_handler(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()

    if user_id not in chef_active_orders:
        await callback.answer("❌ Заказ не найден.", show_alert=True)
        return

    order = chef_active_orders[user_id]

    if action == "fail":
        del chef_active_orders[user_id]
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (current_time, user_id))
            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🍔 На кухню", callback_data="chef_work")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
        ])
        await callback.message.edit_text("❌ **Ошибка в слоях!** Заказ испорчен, кулдаун 45 сек.", reply_markup=kb, parse_mode="Markdown")
        return

    order["step"] += 1
    if order["step"] < len(order["recipe"]):
        await show_chef_step(callback.message, user_id)
    else:
        del chef_active_orders[user_id]
        reward = random.randint(150, 250)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, last_work = ? WHERE user_id = ?",
                (reward, current_time, user_id)
            )
            await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🍔 Новый заказ", callback_data="chef_work")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
        ])
        await callback.message.edit_text(f"✅ **Шедевр!** Бургер собран идеально! Награда: **+{reward} коинов** 🍔", reply_markup=kb, parse_mode="Markdown")


# --- МИНИ-ИГРЫ (Тир и КНБ) ---
@dp.callback_query(F.data == "game_jelly")
async def game_jelly_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Стрелять! (60 коинов)", callback_data="shoot_jelly")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])
    await callback.message.edit_text("🎯 **Тир по медузам**\n\nЦена выстрела: 60 коинов.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "shoot_jelly")
async def shoot_jelly(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))).fetchone()
        balance = row[0] if row else 500
        if balance < 60:
            await callback.answer("❌ Мало коинов!", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance - 60 WHERE user_id = ?", (user_id,))
        await db.commit()

    await callback.message.edit_text("🎯 Выстрел...")
    await asyncio.sleep(2)
    outcome = random.choices(["miss", "normal", "golden"], weights=[40, 45, 15], k=1)[0]

    async with aiosqlite.connect(DB_FILE) as db:
        if outcome == "normal":
            await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
            await db.commit()
            res = "🎯 Поймана обычная медуза: **+100 коинов**!"
        elif outcome == "golden":
            await db.execute("UPDATE users SET balance = balance + 250 WHERE user_id = ?", (user_id,))
            await db.commit()
            res = "🌟 ДЖЕКПОТ! Золотая медуза: **+250 коинов**!"
        else:
            res = "💨 Промах, медуза уплыла."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Еще раз", callback_data="game_jelly")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(res, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "game_rps")
async def game_rps_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🪨 Камень", callback_data="rps_rock"),
            InlineKeyboardButton(text="✂️ Ножницы", callback_data="rps_scissors"),
            InlineKeyboardButton(text="📄 Бумага", callback_data="rps_paper")
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
    ])
    await callback.message.edit_text("✂️ **КНБ с Патриком** (Ставка: 50 коинов)", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("rps_"))
async def play_rps(callback: types.CallbackQuery):
    user_choice = callback.data.replace("rps_", "")
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))).fetchone()
        balance = row[0] if row else 500
        if balance < 50:
            await callback.answer("❌ Мало коинов!", show_alert=True)
            return
        await db.execute("UPDATE users SET balance = balance - 50 WHERE user_id = ?", (user_id,))
        await db.commit()

    bot_choice = random.choice(["rock", "scissors", "paper"])
    if user_choice == bot_choice:
        res = "draw"
    elif (user_choice == "rock" and bot_choice == "scissors") or (user_choice == "scissors" and bot_choice == "paper") or (user_choice == "paper" and bot_choice == "rock"):
        res = "win"
    else:
        res = "lose"

    async with aiosqlite.connect(DB_FILE) as db:
        if res == "win":
            await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
            await db.commit()
            text = "🎉 Победа! Выигрыш: **+100 коинов**!"
        elif res == "draw":
            await db.execute("UPDATE users SET balance = balance + 50 WHERE user_id = ?", (user_id,))
            await db.commit()
            text = "🤝 Ничья! Ставка возвращена."
        else:
            text = "😢 Проигрыш. Ставка сгорела."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Еще", callback_data="game_rps")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


# --- МАСШТАБНЫЙ МНОГОУРОВНЕВЫЙ КВЕСТ С ЖЕСТКИМИ РАЗВИЛКАМИ ---
@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        row = await (await db.execute("SELECT story_status FROM users WHERE user_id = ?", (user_id,))).fetchone()
        status = row[0] if row else 'not_started'

        if status == 'completed':
            await callback.answer("⚠️ Ты уже успешно завершил этот квест!", show_alert=True)
            return
        elif status == 'failed':
            await callback.answer("❌ Твое расследование было провалено. Перепройти эту главу пока нельзя!", show_alert=True)
            return

        # Фиксируем, что квест начат
        await db.execute("UPDATE users SET story_status = 'in_progress' WHERE user_id = ?", (user_id,))
        await db.commit()

    text = (
        "📖 **Глава 1: Ночной грабеж в «Красти Краб»**\n\n"
        "Утро выдалось кошмарным. Сейф взломан, секретный экстракт украден, а Мистер Крабс грозится выгнать всех на мороз. "
        "У тебя есть выбор, с чего начать расследование, но помни: **ошибка приведет к полному провалу дела без права переигрывания этой ветки!**\n\n"
        "Куда направишься первым делом?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Обыскать служебный мусорный бак", callback_data="branch_trash")],
        [InlineKeyboardButton(text="🕵️‍♂️ Провести допрос Губки Боба", callback_data="branch_bob")],
        [InlineKeyboardButton(text="📹 Взломать систему видеонаблюдения", callback_data="branch_cam")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- ВЕТКА 1: Мусорка (Тупиковая / Ложная) ---
@dp.callback_query(F.data == "branch_trash")
async def branch_trash(callback: types.CallbackQuery):
    text = (
        "📖 **Расследование у мусорных баков**\n\n"
        "Ты тщательно перебираешь отходы за рестораном. На дне бака ты находишь обертку от водорослей с пометкой «Чам Баккет».\n"
        "Ты решаешь, что вор — Планктон, и сразу бежишь штурмовать его ресторан без улик...\n\n"
        "⚠️ Охранная система «Чам Баккет» бьет тебя током, а Планктон вызывает полицию Бикини Боттон за клевету!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💀 Принять поражение", callback_data="story_fail")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- ВЕТКА 2: Губка Боб (Средняя / Разветвленная) ---
@dp.callback_query(F.data == "branch_bob")
async def branch_bob(callback: types.CallbackQuery):
    text = (
        "📖 **Допрос Губки Боба**\n\n"
        "Ты застаешь Боба за плачем. Он клянется, что спал, но вспоминает деталь: ночью кто-то стучал в кухонное окно металлическим предметом и пахло машинным маслом.\n"
        "Как поступишь с этой информацией?"
    )
    kb = InlineKeyboardMarkup(inline_keyword=[
        [InlineKeyboardButton(text="🗣 Наорать на Боба и обвинить в краже", callback_data="bob_fail")],
        [InlineKeyboardButton(text="🤝 Объединить силы и устроить засаду на складе", callback_data="bob_success")],
        [InlineKeyboardButton(text="◀️ Назад к выбору", callback_data="story_start")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "bob_fail")
async def bob_fail(callback: types.CallbackQuery):
    text = "📖 Ты несправедливо обвинил лучшего повара. Боб уволился в слезах, Мистер Крабс уволил и тебя за дебош.\n\n❌ **Квест провален!**"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]])
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET story_status = 'failed' WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "bob_success")
async def bob_success(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 400, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    text = (
        "🎉 **Победа по ветке Боба!**\n\n"
        "Вместе вы устроили засаду и поймали робота-шпиона Планктона! Экстракт спасен.\n"
        "🎁 Награда: **+400 коинов** и статус элитного сотрудника!"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

# --- ВЕТКА 3: Камеры (Глубокий детективный путь с этапами) ---
@dp.callback_query(F.data == "branch_cam")
async def branch_cam(callback: types.CallbackQuery):
    text = (
        "📖 **Кабинет Мистера Крабса: Взлом камер**\n\n"
        "Ты пробираешься в кабинет шефа. Монитор заблокирован трехступенчатым цифровым замком.\n"
        "**Этап 1 из 3:** Реши алгоритмический пример безопасности:\n"
        "🔢 `(25 + 15) / 2 = ?`"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="15", callback_data="cam_step1_wrong"), InlineKeyboardButton(text="20", callback_data="cam_step1_right")],
        [InlineKeyboardButton(text="25", callback_data="cam_step1_wrong")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "cam_step1_wrong")
async def cam_step1_wrong(callback: types.CallbackQuery):
    text = "❌ Неверный ответ! Терминал сжег материнскую плату и заблокировался навсегда.\n\n❌ **Квест провален!**"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET story_status = 'failed' WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "cam_step1_right")
async def cam_step1_right(callback: types.CallbackQuery):
    text = (
        "📖 **Взлом камер (Этап 2 из 3):**\n\n"
        "Первый барьер пройден! Но система запрашивает код доступа по цветам проводов на видеозаписи.\n"
        "В логах указано: <i>«Синий провод был перерезан на 4-й минуте, красный активен»</i>.\n"
        "Какой провод замыкает цепь?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Синий", callback_data="cam_step2_wrong"), InlineKeyboardButton(text="🔴 Красный", callback_data="cam_step2_right")],
        [InlineKeyboardButton(text="🟢 Зеленый", callback_data="cam_step2_wrong")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "cam_step2_wrong")
async def cam_step2_wrong(callback: types.CallbackQuery):
    text = "💥 Короткое замыкание! Данные стерты.\n\n❌ **Квест провален!**"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET story_status = 'failed' WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "cam_step2_right")
async def cam_step2_right(callback: types.CallbackQuery):
    text = (
        "📖 **Взлом камер (Финальный этап 3 из 3):**\n\n"
        "Последний шаг! Записи расшифрованы на 99%. На видео виден силуэт вора, но лицо закрыто тенью.\n"
        "Кто изображен на фрагменте по форме клешни?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Шелдон Планктон", callback_data="cam_final_win")],
        [InlineKeyboardButton(text="🟣 Обычный местный житель", callback_data="cam_final_wrong")],
        [InlineKeyboardButton(text="🟡 Сквидвард", callback_data="cam_final_wrong")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "cam_final_wrong")
async def cam_final_wrong(callback: types.CallbackQuery):
    text = "❌ Ты обвинил невиновного! Настоящий преступник скрылся.\n\n❌ **Квест провален!**"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET story_status = 'failed' WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "cam_final_win")
async def cam_final_win(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 700, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    text = (
        "🏆 **ТРИУМФ! ГЕННИАЛЬНОЕ РАССЛЕДОВАНИЕ!**\n\n"
        "Ты взломал систему, доказал вину Планктона и вернул секретный экстракт Мистеру Крабсу!\n"
        "💰 Главная награда за расследование: **+700 коинов** начислено на твой защищенный баланс!"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "story_fail")
async def story_fail(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET story_status = 'failed' WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text("❌ Расследование провалено.", reply_markup=get_back_keyboard(), parse_mode="Markdown")


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
            
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]])
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

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]])
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
    print("Бот запущен с защитой баланса и глубоким квестом!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
