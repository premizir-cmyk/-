import asyncio
import logging
import random
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = '8660698360:AAEGVQen4KR8MXC3bJXZqDkAijZRZdttMfs'
OWNER_ID = 7605961809  # Твой ID без КД

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
                story_status TEXT DEFAULT 'not_started',
                referred_by INTEGER
            )
        """)
        await db.commit()

# Безопасная регистрация
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
        "Бот полностью перезапущен и готов к работе. Ошибки обработки кнопок устранены!"
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done, story_status FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance, jobs, status = row if row else (500, 0, 'not_started')

    story_text = "✅ Завершен" if status == 'completed' else "⏳ В процессе / Доступен"
    text = (
        f"👤 **Твой профиль:**\n\n"
        f"💰 Баланс: **{balance} коинов**\n"
        f"🛠 Выполнено смен: **{jobs}**\n"
        f"📖 Сюжетный статус: **{story_text}**"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")


# --- МИНИ-ИГРА: ПОВАР (Без КД для тебя) ---
chef_active_orders = {}

@dp.callback_query(F.data == "chef_work")
async def chef_work_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    current_time = asyncio.get_event_loop().time()
    cooldown = 0 if user_id == OWNER_ID else 45

    if user_id != OWNER_ID:
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

    try:
        await message.edit_text(
            f"🍳 **Сборка бургера (Шаг {step + 1} из {len(recipe)}):**\n\n"
            f"Какой слой должен идти по памяти?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Error in show_chef_step: {e}")

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
        if user_id != OWNER_ID:
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (current_time, user_id))
                await db.commit()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🍔 На кухню", callback_data="chef_work")],
            [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
        ])
        await callback.message.edit_text("❌ **Ошибка в слоях!** Заказ испорчен.", reply_markup=kb, parse_mode="Markdown")
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


# МИНИ-ИГРЫ (Тир и КНБ)
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


# ==========================================
# --- МАСШТАБНЫЙ МНОГОЭТАПНЫЙ КВЕСТ ---
# ==========================================

@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET story_status = 'in_progress' WHERE user_id = ?", (user_id,))
        await db.commit()

    text = (
        "📖 **Глава 1: Ночной грабеж в «Красти Краб»**\n\n"
        "Сейф взломан, секретный экстракт украден! У тебя есть три глобальные ветки расследования. Выбирай свой путь:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Ветка 1: Внимательно изучить улики", callback_data="clues_start")],
        [InlineKeyboardButton(text="🕵️‍♂️ Ветка 2: Допрос Губки Боба", callback_data="bob_start")],
        [InlineKeyboardButton(text="📹 Ветка 3: Взломать систему камер", callback_data="cam_start")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


# ==========================================
# ВЕТКА 1: ИЗУЧЕНИЕ УЛИК
# ==========================================
@dp.callback_query(F.data == "clues_start")
async def clues_start(callback: types.CallbackQuery):
    text = (
        "📖 **Ветка 1: Улики на месте преступления**\n\n"
        "Ты тщательно осматриваешь сейф. Улики указывают сразу на троих подозреваемых: **Сквидвард** (его трость оставила царапину), "
        "**Планктон** (следы радиоактивной грязи) и **Патрик** (крошки водорослевого мороженого).\n\n"
        "Улик слишком мало, чтобы сразу назвать вора. Что предпримем?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗣 Распросить каждого подозреваемого лично", callback_data="clues_interrogate_all")],
        [InlineKeyboardButton(text="📹 Посмотреть видеокамеры архива", callback_data="cam_start")],
        [InlineKeyboardButton(text="🔬 Провести анализ улик в лаборатории", callback_data="clues_lab")],
        [InlineKeyboardButton(text="◀️ На главную развилку квеста", callback_data="story_start")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "clues_interrogate_all")
async def clues_interrogate_all(callback: types.CallbackQuery):
    text = (
        "📖 **Допрос подозреваемых:**\n\n"
        "• **Сквидвард** раздражен: *«Я спал дома!»*\n"
        "• **Планктон** смеется: *«Ха! Докажи!»*\n"
        "• **Патрик** жует мороженое: *«А что такое экстракт?»*\n\n"
        "Кто из них вызывает наибольшее подозрение?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👉 Обвинить Сквидварда", callback_data="clues_fail_squid")],
        [InlineKeyboardButton(text="👉 Обвинить Планктона", callback_data="clues_win_plankton")],
        [InlineKeyboardButton(text="👉 Обвинить Патрика", callback_data="clues_fail_pat")],
        [InlineKeyboardButton(text="🔙 Назад к уликам", callback_data="clues_start")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "clues_lab")
async def clues_lab(callback: types.CallbackQuery):
    text = (
        "📖 **Лабораторный анализ**\n\n"
        "В микроскоп ты видишь, что следы грязи содержат ДНК планктона и отпечатки Патрика. Как трактуем?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Это подстава Планктона против Патрика", callback_data="clues_win_plankton")],
        [InlineKeyboardButton(text="🧪 Патрик сообщник", callback_data="clues_fail_pat")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="clues_start")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "clues_fail_squid")
async def clues_fail_squid(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Сквидвард подал в суд за клевету. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "clues_fail_pat")
async def clues_fail_pat(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Патрик просто ел мороженое. Ошибка!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "clues_win_plankton")
async def clues_win_plankton(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 600, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text(
        "🏆 **ТРИУМФ! Гениальная дедукция!**\n\n"
        "Ты доказал вину Планктона! Экстракт возвращен.\n"
        "💰 Награда: **+600 коинов** зачислено!",
        reply_markup=get_back_keyboard(), parse_mode="Markdown"
    )


# ==========================================
# ВЕТКА 2: ДОПРОС ГУБКИ БОБА
# ==========================================
@dp.callback_query(F.data == "bob_start")
async def bob_start(callback: types.CallbackQuery):
    text = (
        "📖 **Ветка 2: Разговор с Губкой Бобом**\n\n"
        "Боб говорит, что слышал подозрительные звуки сзади ресторана ночью.\n"
        "Что сделаешь?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Обвинить Боба в краже", callback_data="bob_blame_direct")],
        [InlineKeyboardButton(text="🤝 Объединить с ним силы", callback_data="bob_team_up")],
        [InlineKeyboardButton(text="◀️ На главную развилку квеста", callback_data="story_start")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "bob_blame_direct")
async def bob_blame_direct(callback: types.CallbackQuery):
    text = "📖 Боб смертельно обиделся и плачет. Что предпримем?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍫 Извиниться и помириться", callback_data="bob_team_up")],
        [InlineKeyboardButton(text="🚶‍♂️ Работать в одиночку", callback_data="bob_solo")],
        [InlineKeyboardButton(text="🐙 Пойти к Сквидварду", callback_data="bob_ask_squid")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "bob_solo")
async def bob_solo(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Без повара ты запутался и упустил вора. Провал.", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "bob_ask_squid")
async def bob_ask_squid(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Сквидвард захлопнул дверь. Тупик.", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "bob_team_up")
async def bob_team_up(callback: types.CallbackQuery):
    text = "📖 Вы строите совместную ловушку с Бобом. Куда ставим капкан?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪤 У входа в «Красти Краб»", callback_data="bob_trap_fail")],
        [InlineKeyboardButton(text="🪤 У вентиляции «Чам Баккет»", callback_data="bob_win")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="bob_start")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "bob_trap_fail")
async def bob_trap_fail(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Вор обошел ловушку через крышу!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "bob_win")
async def bob_win(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 550, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text(
        "🎉 **Победа с Губкой Бобом!**\n\nПланктон пойман в вентиляции.\n💰 Награда: **+550 коинов** зачислено!",
        reply_markup=get_back_keyboard(), parse_mode="Markdown"
    )


# ==========================================
# ВЕТКА 3: ВЗЛОМ КАМЕР
# ==========================================
@dp.callback_query(F.data == "cam_start")
async def cam_start(callback: types.CallbackQuery):
    text = (
        "📖 **Ветка 3: Кабинет видеонаблюдения**\n\n"
        "**Этап 1:** Реши математический пример:\n🔢 `(50 - 14) / 4 = ?`"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="8", callback_data="cam_fail"), InlineKeyboardButton(text="9", callback_data="cam_stage2")],
        [InlineKeyboardButton(text="12", callback_data="cam_fail")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "cam_fail")
async def cam_fail(callback: types.CallbackQuery):
    await callback.message.edit_text("💥 Сработала сигнализация! Терминал заблокирован.", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "cam_stage2")
async def cam_stage2(callback: types.CallbackQuery):
    text = "📖 **Этап 2:** Какая деталь бросается в глаза на видео в 3:00 ночи?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💡 Зеленое свечение антенны", callback_data="cam_stage3")],
        [InlineKeyboardButton(text="💡 Желтая текстура", callback_data="cam_fail")],
        [InlineKeyboardButton(text="💡 Кларнет", callback_data="cam_fail")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "cam_stage3")
async def cam_stage3(callback: types.CallbackQuery):
    text = "📖 **Этап 3:** Кто взломал сейф по данным камер?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Шелдон Планктон", callback_data="cam_ultimate_win")],
        [InlineKeyboardButton(text="🔴 Сам Мистер Крабс", callback_data="cam_fail")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "cam_ultimate_win")
async def cam_ultimate_win(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 750, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text(
        "🏆 **АБСОЛЮТНЫЙ ТРИУМФ!**\n\nЗаписи камер восстановлены, вина Планктона неоспорима!\n💰 Главная премия: **+750 коинов** начислена!",
        reply_markup=get_back_keyboard(), parse_mode="Markdown"
    )


# Остальные игры и меню
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
        if user_id != OWNER_ID and now - last < 86400:
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
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
