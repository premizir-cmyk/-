import asyncio
import logging
import random
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = '8660698360:AAEGVQen4KR8MXC3bJXZqDkAijZRZdttMfs'
OWNER_ID = 7605961809

bot = Bot(token=TOKEN)
dp = Dispatcher()
DB_FILE = "database.db"

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

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍔 Повар в Красти Краб", callback_data="chef_work"),
            InlineKeyboardButton(text="📖 Эпичный квест", callback_data="story_start")
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
    
    await message.answer(
        f"Здорово, **{user_display_name}**! 👋\n\nБот запущен! Квест полностью расширен до огромного масштаба с кучей развилок и шагов.",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance, jobs_done, story_status FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance, jobs, status = row if row else (500, 0, 'not_started')

    story_text = "✅ Пройден" if status == 'completed' else "⏳ Доступен"
    text = f"👤 **Твой профиль:**\n\n💰 Баланс: **{balance} коинов**\n🛠 Смен: **{jobs}**\n📖 Квест: **{story_text}**"
    await callback.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="Markdown")

# --- Повар ---
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
                await callback.answer(f"⏳ Кухня занята! Подожди {left} сек.", show_alert=True)
                return

    layers_pool = [
        ("bun_top", "🍞 Верхняя булочка"),
        ("meat", "🥩 Котлета"),
        ("cheese", "🧀 Сыр"),
        ("salad", "🥬 Салат"),
        ("tomato", "🍅 Помидор"),
        ("bun_bot", "🍞 Нижняя булочка")
    ]
    
    recipe = random.sample(layers_pool, 3)
    chef_active_orders[user_id] = {"recipe": recipe, "step": 0}
    recipe_text = " ➡️ ".join([item[1] for item in recipe])

    await callback.message.edit_text(
        f"🍔 **Повар в «Красти Краб»**\n\n📜 **Запомни рецепт:**\n👉 <code>{recipe_text}</code>\n\n⏱ У тебя **5 секунд**...",
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
        ("bun_bot", "🍞 Нижняя булочка")
    ]
    random.shuffle(options)

    kb_buttons = [[InlineKeyboardButton(text=name, callback_data=f"chefstep_{'next' if key == correct_key else 'fail'}")] for key, name in options]
    kb_buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_menu")])

    try:
        await message.edit_text(
            f"🍳 **Сборка (Шаг {step + 1} из {len(recipe)}):**\nКакой слой следующий?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
            parse_mode="HTML"
        )
    except:
        pass

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
        await callback.message.edit_text("❌ Ошибка в слоях! Заказ испорчен.", reply_markup=get_back_keyboard(), parse_mode="Markdown")
        return

    order["step"] += 1
    if order["step"] < len(order["recipe"]):
        await show_chef_step(callback.message, user_id)
    else:
        del chef_active_orders[user_id]
        reward = random.randint(150, 250)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("UPDATE users SET balance = balance + ?, jobs_done = jobs_done + 1, last_work = ? WHERE user_id = ?", (reward, current_time, user_id))
            await db.commit()
        await callback.message.edit_text(f"✅ Идеально! Награда: **+{reward} коинов** 🍔", reply_markup=get_back_keyboard(), parse_mode="Markdown")

# ==========================================
# --- МАСШТАБНЫЙ КВЕСТ (БОЛЕЕ 20 КНОПОК / ШАГОВ) ---
# ==========================================

@dp.callback_query(F.data == "story_start")
async def story_start(callback: types.CallbackQuery):
    text = (
        "📖 **Глава 1: Ночной грабеж в «Красти Краб»**\n\n"
        "Сейф взломан дочиста! Экстракт пропал. Тебе предстоит масштабное расследование. Выбирай первую глобальную ветку:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Ветка 1: Глубокий анализ улик", callback_data="v1_step1")],
        [InlineKeyboardButton(text="🕵️‍♂️ Ветка 2: Расследование с Губкой Бобом", callback_data="v2_step1")],
        [InlineKeyboardButton(text="📹 Ветка 3: Хакерский взлом архива камер", callback_data="v3_step1")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="back_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- ВЕТКА 1: УЛИКИ (Шанс оступиться и много шагов) ---
@dp.callback_query(F.data == "v1_step1")
async def v1_step1(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Осмотреть замок на сейфе", callback_data="v1_step2_lock")],
        [InlineKeyboardButton(text="🔎 Осмотреть пол около кассы", callback_data="v1_step2_floor")],
        [InlineKeyboardButton(text="🔙 На главную развилку", callback_data="story_start")]
    ])
    await callback.message.edit_text("📖 **Ветка 1 [Шаг 1/6]:** Ты заходишь в кабинет. С чего начнешь осмотр места преступления?", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step2_lock")
async def v1_step2_lock(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔬 Взять соскоб метала на анализ", callback_data="v1_step3_lab")],
        [InlineKeyboardButton(text="🗣 Сравнить царапины с тростью Сквидварда", callback_data="v1_step3_squid")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v1_step1")]
    ])
    await callback.message.edit_text("📖 **Ветка 1 [Шаг 2/6]:** На замке обнаружены глубокие царапины от тупого тяжелого предмета.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step2_floor")
async def v1_step2_floor(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Проверить состав грязи химикатами", callback_data="v1_step3_lab")],
        [InlineKeyboardButton(text="🧁 Попробовать крошки на вкус", callback_data="v1_fail_floor")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v1_step1")]
    ])
    await callback.message.edit_text("📖 **Ветка 1 [Шаг 2/6]:** На полу валяются странные радиоактивные следы и крошки неизвестного происхождения.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v1_fail_floor")
async def v1_fail_floor(callback: types.CallbackQuery):
    await callback.message.edit_text("🤢 Ты отравился неизвестными крошками и попал в больницу. Расследование провалено!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step3_squid")
async def v1_step3_squid(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚪 Вломиться к нему домой без ордера", callback_data="v1_fail_squid_house")],
        [InlineKeyboardButton(text="📝 Вызвать его на допрос в кабинет", callback_data="v1_step4_interrogate")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v1_step2_lock")]
    ])
    await callback.message.edit_text("📖 **Ветка 1 [Шаг 3/6]:** Царапины похожи на трость Сквидварда, но диаметр не совпадает.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v1_fail_squid_house")
async def v1_fail_squid_house(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Сквидвард вызвал полицию за вторжение в частную собственность. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step3_lab")
async def v1_step3_lab(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Сравнить ДНК с базой Чам Баккет", callback_data="v1_step4_dna")],
        [InlineKeyboardButton(text="🗑 Выкинуть результаты и гадать дальше", callback_data="v1_step1")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v1_step2_lock")]
    ])
    await callback.message.edit_text("📖 **Ветка 1 [Шаг 3/6]:** Экспресс-анализ в лаборатории показал наличие следов водорослевого топлива.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step4_interrogate")
@dp.callback_query(F.data == "v1_step4_dna")
async def v1_step4_final(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Передать дело судье Наточнику", callback_data="v1_step5_judge")],
        [InlineKeyboardButton(text="🪤 Устроить личную засаду на подозреваемого", callback_data="v1_step5_trap")]
    ])
    await callback.message.edit_text("📖 **Ветка 1 [Шаг 4/6]:** Улики указывают прямо на подземную лабораторию Планктона под Чам Баккет. Твой финальный ход?", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step5_judge")
async def v1_step5_judge(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Пока судья оформлял бумажки, Планктон сбежал из города. Улика потеряна. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v1_step5_trap")
async def v1_step5_trap(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 700, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text("🏆 **Ветка 1 [ФИНАЛ]:** Твоя засада сработала идеально! Планктон пойман с поличным.\n💰 Награда: **+700 коинов** зачислена!", reply_markup=get_back_keyboard(), parse_mode="Markdown")


# --- ВЕТКА 2: ГУБКА БОБ (Ссора, примирение, развилки) ---
@dp.callback_query(F.data == "v2_step1")
async def v2_step1(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Жестко обвинить Боба в соучастии", callback_data="v2_step2_blame")],
        [InlineKeyboardButton(text="🤝 Обнять его и предложить помощь", callback_data="v2_step2_hug")],
        [InlineKeyboardButton(text="🔙 На главную развилку", callback_data="story_start")]
    ])
    await callback.message.edit_text("📖 **Ветка 2 [Шаг 1/6]:** Губка Боб горько плачет на кухне и говорит, что слышал подозрительный шум ночью.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v2_step2_blame")
async def v2_step2_blame(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍫 Извиниться перед ним за грубость", callback_data="v2_step2_hug")],
        [InlineKeyboardButton(text="🚶‍♂️ Уйти и хлопок дверью", callback_data="v2_fail_sulk")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v2_step1")]
    ])
    await callback.message.edit_text("📖 **Ветка 2 [Шаг 2/6]:** Боб обиделся до глубины души, закрылся в кладовой и отказывается говорить.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v2_fail_sulk")
async def v2_fail_sulk(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Без помощи повара ресторан закрылся из-за антисанитарии. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v2_step2_hug")
async def v2_step2_hug(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪤 Построить секретную ловушку из лопаток", callback_data="v2_step3_trap")],
        [InlineKeyboardButton(text="🔦 Идти патрулировать темный переулок", callback_data="v2_step3_alley")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v2_step1")]
    ])
    await callback.message.edit_text("📖 **Ветка 2 [Шаг 2/6]:** Вы помирились! Боб вспомнил, что у вора была металлическая нога.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v2_step3_alley")
async def v2_step3_alley(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 В темном переулке на вас напала медуза-мутант. Вы сбежали в страхе. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v2_step3_trap")
async def v2_step3_trap(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Залить в ловушку порцию горчицы", callback_data="v2_step4_ mustard")],
        [InlineKeyboardButton(text="⚡ Подключить ток от генератора", callback_data="v2_fail_shock")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v2_step2_hug")]
    ])
    await callback.message.edit_text("📖 **Ветка 2 [Шаг 3/6]:** Ловушка из лопаток готова у входа. Какой сюрприз подготовим внутри?", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v2_fail_shock")
async def v2_fail_shock(callback: types.CallbackQuery):
    await callback.message.edit_text("⚡ Вы случайно замкнули проводку и сожгли весь Красти Краб. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v2_step4_ mustard")
async def v2_step4_mustard(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Ждать в засаде до утра", callback_data="v2_win")],
        [InlineKeyboardButton(text="💤 Уйти спать и проверить утром", callback_data="v2_fail_sleep")]
    ])
    await callback.message.edit_text("📖 **Ветка 2 [Шаг 4/6]:** Горчичная ловушка замаскирована безупречно. Осталось дождаться вора.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v2_fail_sleep")
async def v2_fail_sleep(callback: types.CallbackQuery):
    await callback.message.edit_text("📖 Утром ловушка была пуста, а экстракт исчез окончательно. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v2_win")
async def v2_win(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 650, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text("🏆 **Ветка 2 [ФИНАЛ]:** Ночью вор попался в горчичную ловушку — это был Планктон! Вы с Бобом герои.\n💰 Награда: **+650 коинов** зачислена!", reply_markup=get_back_keyboard(), parse_mode="Markdown")


# --- ВЕТКА 3: КАМЕРЫ (Многоэтапный хакерский взлом) ---
@dp.callback_query(F.data == "v3_step1")
async def v3_step1(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Решить пример: (50 - 14) / 4", callback_data="v3_step2_math")],
        [InlineKeyboardButton(text="🔨 Постучать по системному блоку молотком", callback_data="v3_fail_hammer")],
        [InlineKeyboardButton(text="🔙 На главную развилку", callback_data="story_start")]
    ])
    await callback.message.edit_text("📖 **Ветка 3 [Шаг 1/6]:** Терминал видеонаблюдения заблокирован сложным криптографическим паролем.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v3_fail_hammer")
async def v3_fail_hammer(callback: types.CallbackQuery):
    await callback.message.edit_text("🔨 От удара молотком монитор взорвался осколками тебе в лицо. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v3_step2_math")
async def v3_step2_math(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Перерезать красный провод", callback_data="v3_fail_wire")],
        [InlineKeyboardButton(text="🔵 Перерезать синий провод", callback_data="v3_step3_wire")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v3_step1")]
    ])
    await callback.message.edit_text("📖 **Ветка 3 [Шаг 2/6]:** Доступ к файлам открыт, но срабатывает система защиты с таймером и цветными проводами.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v3_fail_wire")
async def v3_fail_wire(callback: types.CallbackQuery):
    await callback.message.edit_text("💥 Не тот провод! Произошел мощный взрыв платы. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v3_step3_wire")
async def v3_step3_wire(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Включить видео на х16 скорости", callback_data="v3_step4_fast")],
        [InlineKeyboardButton(text="🐢 Смотреть покадрово с 3:00 ночи", callback_data="v3_step4_slow")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v3_step2_math")]
    ])
    await callback.message.edit_text("📖 **Ветка 3 [Шаг 3/6]:** Защита снята! Архив видеозаписей за ночь загружается на экран.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v3_step4_fast")
async def v3_step4_fast(callback: types.CallbackQuery):
    await callback.message.edit_text("⏩ На быстрой перемотке ты пропустил момент кражи и ничего не увидел. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v3_step4_slow")
async def v3_step4_slow(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Увеличить отражение в глазу вора", callback_data="v3_step5_face")],
        [InlineKeyboardButton(text="🔊 Усилить звук шагов", callback_data="v3_fail_audio")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v3_step3_wire")]
    ])
    await callback.message.edit_text("📖 **Ветка 3 [Шаг 4/6]:** Покопавшись в покадровой записи, ты замечаешь силуэт человека в маске.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v3_fail_audio")
async def v3_fail_audio(callback: types.CallbackQuery):
    await callback.message.edit_text("🔊 Динамики выдали ужасный ультразвуковой писк, оглушивший тебя. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v3_step5_face")
async def v3_step5_face(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Подтвердить: это Шелдон Планктон", callback_data="v3_win")],
        [InlineKeyboardButton(text="🟡 Подтвердить: это Сквидвард в маске", callback_data="v3_fail_wrong_face")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="v3_step4_slow")]
    ])
    await callback.message.edit_text("📖 **Ветка 3 [Шаг 5/6]:** ИИ завершает сканирование лица. На экране четко прорисовывается знакомая зеленая антенна.", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "v3_fail_wrong_face")
async def v3_fail_wrong_face(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Ошибка идентификации! Ты обвинил не того, настоящий хакер стер логи. Провал!", reply_markup=get_back_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "v3_win")
async def v3_win(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance + 800, story_status = 'completed' WHERE user_id = ?", (user_id,))
        await db.commit()
    await callback.message.edit_text("🏆 **Ветка 3 [ФИНАЛ]:** Железные доказательства собраны! Планктон изобличен благодаря хакерскому взлому.\n💰 Награда: **+800 коинов** зачислена!", reply_markup=get_back_keyboard(), parse_mode="Markdown")


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

    win = random.choices([0, 50, 100, 200, 350], weights=[35, 30, 20, 10, 5], k=1)[0]
    async with aiosqlite.connect(DB_FILE) as db:
        if win > 0:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await db.commit()
            res = f"🎉 Выигрыш: **+{win} коинов**!"
        else:
            res = "😢 Пусто."
    await callback.message.edit_text(res, reply_markup=get_back_keyboard(), parse_mode="Markdown")

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
    await asyncio.sleep(3)
    val = dice.dice.value

    async with aiosqlite.connect(DB_FILE) as db:
        if val >= 4:
            await db.execute("UPDATE users SET balance = balance + 100 WHERE user_id = ?", (user_id,))
            await db.commit()
            res = f"🎉 Выпало {val}! Выигрыш: **+100 коинов**!"
        else:
            res = f"😢 Выпало {val}. Проигрыш."
    await callback.message.answer(res, reply_markup=get_back_keyboard(), parse_mode="Markdown")

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
    print("Бот успешно запущен со сверхмасштабным квестом!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
