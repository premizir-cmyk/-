import asyncio
import random
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Твой токен, который ты скинул
BOT_TOKEN = "8501498168:AAFHfwrHuUlshb725hDrrFAXMBIHzMdSIrE"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилище игры
class GameState:
    def __init__(self):
        self.is_registration = False
        self.is_game_active = False
        self.players = {}  # {user_id: {"name": str, "role": str, "is_alive": bool}}
        self.chat_id = None
        self.night_actions = {}  # {"mafia": target_id, "doctor": target_id, "commissar": target_id}
        self.bot_username = ""

game = GameState()


# Распределение ролей под любое число игроков
def assign_roles(players_dict):
    user_ids = list(players_dict.keys())
    total = len(user_ids)
    random.shuffle(user_ids)

    # Пропорции: ~25% мафия, 1 доктор, 1 комиссар, остальные — мирные
    mafia_count = max(1, total // 4)
    roles_pool = ['Мафия'] * mafia_count + ['Доктор', 'Комиссар']
    
    while len(roles_pool) < total:
        roles_pool.append('Мирный житель')

    random.shuffle(roles_pool)

    for uid, role in zip(user_ids, roles_pool):
        players_dict[uid]['role'] = role
        players_dict[uid]['is_alive'] = True


# --- Шаг 1: Запуск набора в чате ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Если это ЛС (личные сообщения с ботом)
    if message.chat.type == "private":
        # Проверяем, перешел ли игрок по кнопке регистрации из группы (?start=join)
        if message.text and "join" in message.text:
            if not game.is_registration:
                await message.answer("❌ Набор в игру сейчас не идет или уже завершен!")
                return
            
            user_id = message.from_user.id
            user_name = message.from_user.first_name

            if user_id in game.players:
                await message.answer("⚠️ Вы уже зарегистрированы в игре! Возвращайтесь в группу.")
                return

            game.players[user_id] = {"name": user_name, "role": None, "is_alive": True}
            await message.answer("✅ **Вы успешно зарегистрированы в Мафию!**\n\nОжидайте старта игры в группе.", parse_mode="Markdown")
            
            if game.chat_id:
                await bot.send_message(
                    game.chat_id, 
                    f"➕ К игре присоединился **{user_name}**! (Всего: {len(game.players)})",
                    parse_mode="Markdown"
                )
            return
        
        await message.answer("Привет! Я бот для игры в Мафию. Добавь меня в группу и напиши там `/start` для запуска набора.")
        return

    # Если это ГРУППА (первый /start запускает набор)
    if not game.is_registration and not game.is_game_active:
        game.is_registration = True
        game.chat_id = message.chat.id
        game.players.clear()

        # Узнаем username бота для красивой ссылки-кнопки
        if not game.bot_username:
            me = await bot.get_me()
            game.bot_username = me.username

        builder = InlineKeyboardBuilder()
        builder.button(text="🎮 Присоединиться к игре", url=f"https://t.me/{game.bot_username}?start=join")

        await message.answer(
            "🎭 **Набор в игру 'Мафия' открыт!**\n\n"
            "Чтобы участвовать, нажмите на кнопку ниже (нужно будет нажать 'Запустить' в ЛС с ботом).\n\n"
            "📌 Как наберете игроков, напишите второй раз **/start** в этот чат для старта игры!",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        return

    # ВТОРОЙ /start в группе — старт игры (когда игроки набрались)
    if game.is_registration:
        if len(game.players) < 3:
            await message.answer("⚠️ Маловато игроков! Нужно минимум 3 человека для старта.")
            return

        game.is_registration = False
        game.is_game_active = True

        assign_roles(game.players)

        await message.answer("🎲 **Игра началась!** Роли распределены, разносите друг друга. Рассылаю роли в личные сообщения...")

        # Рассылка ролей в ЛС
        for uid, pdata in game.players.items():
            try:
                await bot.send_message(
                    uid,
                    f"🤫 Ваша роль в игре: **{pdata['role']}**\n\nНикому не показывайте это сообщение!",
                    parse_mode="Markdown"
                )
            except Exception:
                await message.answer(f"⚠️ Не смог отправить роль игроку **{pdata['name']}**. Пусть он сначала напишет боту в ЛС /start!")

        # Запуск первой ночи
        await start_night_phase()
        return

    if game.is_game_active:
        await message.answer("⚠️ Игра уже идет полным ходом!")


# --- Фаза Ночи ---
async def start_night_phase():
    game.night_actions.clear()
    await bot.send_message(
        game.chat_id, 
        "🌃 **Наступает ночь.** Город засыпает...\nПросыпаются мафия, комиссар и доктор. Свои ходы они делают в личных сообщениях с ботом.",
        parse_mode="Markdown"
    )

    alive_players = {uid: p['name'] for uid, p in game.players.items() if p['is_alive']}

    # Рассылка клавиатур для ночных ходов в ЛС
    for uid, pdata in game.players.items():
        if not pdata['is_alive']:
            continue

        role = pdata['role']
        if role in ['Мафия', 'Доктор', 'Комиссар']:
            builder = InlineKeyboardBuilder()
            for target_id, target_name in alive_players.items():
                # Мафия не может стрелять в своих подельников
                if role == 'Мафия' and game.players[target_id]['role'] == 'Мафия':
                    continue
                
                # Текст для кнопок
                btn_text = target_name
                if role == 'Мафия':
                    btn_text = f"🔪 Убить {target_name}"
                elif role == 'Доктор':
                    btn_text = f"💊 Лечить {target_name}"
                elif role == 'Комиссар':
                    btn_text = f"🔫 Проверить/Стрелять {target_name}"

                builder.button(text=btn_text, callback_data=f"night:{role}:{target_id}")

            builder.adjust(1)
            
            await bot.send_message(
                uid,
                f"🌙 Ночь на дворе. Ваша роль: **{role}**.\nВыберите цель:",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )


# --- Обработка ночных выборов в ЛС ---
@dp.callback_query(F.data.startswith("night:"))
async def cb_night_action(callback: types.CallbackQuery):
    _, role, target_id = callback.data.split(":")
    target_id = int(target_id)
    target_name = game.players[target_id]['name']

    game.night_actions[role] = target_id

    if role == 'Комиссар':
        target_role = game.players[target_id]['role']
        status = "Мафия 😈" if target_role == "Мафия" else "Мирный / Честный 😇"
        await callback.message.edit_text(f"🔍 Вы проверили игрока **{target_name}**. Он: {status}", parse_mode="Markdown")
    elif role == 'Мафия':
        await callback.message.edit_text(f"🔪 Вы выбрали цель для убийства: **{target_name}**.", parse_mode="Markdown")
    elif role == 'Доктор':
        await callback.message.edit_text(f"💊 Вы решили лечить игрока: **{target_name}**.", parse_mode="Markdown")

    await callback.answer("Ход принят!")

    # Проверяем, все ли активные живые роли сделали свой выбор
    active_roles_alive = {p['role'] for p in game.players.values() if p['is_alive'] and p['role'] in ['Мафия', 'Доктор', 'Комиссар']}
    if set(game.night_actions.keys()) >= active_roles_alive:
        await process_night_results()


# --- Итоги ночи и переход к дню ---
async def process_night_results():
    shot_target = game.night_actions.get('Мафия')
    healed_target = game.night_actions.get('Доктор')
    commissar_target = game.night_actions.get('Комиссар')

    await bot.send_message(game.chat_id, "☀️ **Наступает утро!** Город просыпается...", parse_mode="Markdown")

    killed_name = None

    # Если мафия кого-то выбрала и доктор его не спас
    if shot_target and shot_target != healed_target:
        game.players[shot_target]['is_alive'] = False
        killed_name = game.players[shot_target]['name']
        await bot.send_message(game.chat_id, f"💀 Этим утром город не досчитался игрока **{killed_name}**. Мафия сделал свое дело ночи!")
    else:
        await bot.send_message(game.chat_id, "🛡️ Ночь прошла успешно для горожан! Никто не погиб (либо доктор вовремя вылечил раненого).")

    # Проверяем условие победы (пример упрощенный)
    alive_mafia = sum(1 for p in game.players.values() if p['is_alive'] and p['role'] == 'Мафия')
    alive_peaceful = sum(1 for p in game.players.values() if p['is_alive'] and p['role'] != 'Мафия')

    if alive_mafia == 0:
        await bot.send_message(game.chat_id, "🎉 **Мирные жители победили!** Вся мафия уничтожена.", parse_mode="Markdown")
        game.is_game_active = False
        return
    elif alive_mafia >= alive_peaceful:
        await bot.send_message(game.chat_id, "🦹‍♂️ **Победа Мафии!** Их стало столько же или больше, чем мирных жителей.", parse_mode="Markdown")
        game.is_game_active = False
        return

    # Кнопка для запуска следующей ночи или дневного обсуждения
    builder = InlineKeyboardBuilder()
    builder.button(text="🌙 Перейти к следующей ночи", callback_data="next_night")
    
    await bot.send_message(
        game.chat_id, 
        "💬 Обсуждайте, кого подозревать! Когда будете готовы снова засыпать, нажмите кнопку:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "next_night")
async def cb_next_night(callback: types.CallbackQuery):
    await callback.message.delete()
    await start_night_phase()


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
