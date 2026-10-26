import asyncio
import random
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Твой токен бота
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
        self.night_actions = {}  # {"Мафия": target_id, "Доктор": target_id, "Комиссар": target_id}
        self.day_votes = {}  # {voter_id: target_id}
        self.bot_username = ""

game = GameState()


# Распределение ролей (1 мафия для стабильности в небольших группах, доктор, комиссар, остальные мирные)
def assign_roles(players_dict):
    user_ids = list(players_dict.keys())
    total = len(user_ids)
    random.shuffle(user_ids)

    mafia_count = 1
    roles_pool = ['Мафия'] * mafia_count + ['Доктор', 'Комиссар']
    
    while len(roles_pool) < total:
        roles_pool.append('Мирный житель')

    random.shuffle(roles_pool)

    for uid, role in zip(user_ids, roles_pool):
        players_dict[uid]['role'] = role
        players_dict[uid]['is_alive'] = True


# Проверка условий победы
async def check_win_conditions():
    alive_mafia = sum(1 for p in game.players.values() if p['is_alive'] and p['role'] == 'Мафия')
    alive_peaceful = sum(1 for p in game.players.values() if p['is_alive'] and p['role'] != 'Мафия')

    if alive_mafia == 0:
        await bot.send_message(game.chat_id, "🎉 **Мирные жители победили!** Вся мафия уничтожена.", parse_mode="Markdown")
        game.is_game_active = False
        return True
    elif alive_mafia >= alive_peaceful:
        await bot.send_message(game.chat_id, "🦹‍♂️ **Победа Мафии!** Их стало столько же или больше, чем мирных жителей.", parse_mode="Markdown")
        game.is_game_active = False
        return True
    return False


# --- Шаг 1: Запуск набора в чате ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type == "private":
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

    # Запуск набора в группе
    if not game.is_registration and not game.is_game_active:
        game.is_registration = True
        game.chat_id = message.chat.id
        game.players.clear()

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

    # Старт игры (второй /start в группе)
    if game.is_registration:
        if len(game.players) < 3:
            await message.answer("⚠️ Маловато игроков! Нужно минимум 3 человека для старта.")
            return

        game.is_registration = False
        game.is_game_active = True

        assign_roles(game.players)

        await message.answer("🎲 **Игра началась!** Роли распределены. Рассылаю роли в личные сообщения...")

        for uid, pdata in game.players.items():
            try:
                await bot.send_message(
                    uid,
                    f"🤫 Ваша роль в игре: **{pdata['role']}**\n\nНикому не показывайте это сообщение!",
                    parse_mode="Markdown"
                )
            except Exception:
                await message.answer(f"⚠️ Не смог отправить роль игроку **{pdata['name']}**. Пусть он сначала напишет боту в ЛС /start!")

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

    for uid, pdata in game.players.items():
        if not pdata['is_alive']:
            continue

        role = pdata['role']
        if role in ['Мафия', 'Доктор', 'Комиссар']:
            builder = InlineKeyboardBuilder()
            for target_id, target_name in alive_players.items():
                if role == 'Мафия':
                    btn_text = f"🔪 Убить {target_name}"
                elif role == 'Доктор':
                    btn_text = f"💊 Лечить {target_name}"
                elif role == 'Комиссар':
                    btn_text = f"🔫 Проверить {target_name}"

                builder.button(text=btn_text, callback_data=f"night:{role}:{target_id}")

            builder.adjust(1)
            
            try:
                await bot.send_message(
                    uid,
                    f"🌙 Ночь на дворе. Ваша роль: **{role}**.\nВыберите цель:",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
            except Exception:
                pass


# --- Обработка ночных выборов в ЛС ---
@dp.callback_query(F.data.startswith("night:"))
async def cb_night_action(callback: types.CallbackQuery):
    if not game.is_game_active:
        await callback.answer("Игра не активна!", show_alert=True)
        return

    _, role, target_id = callback.data.split(":")
    target_id = int(target_id)
    
    if target_id not in game.players or not game.players[target_id]['is_alive']:
        await callback.answer("Этот игрок уже мертв!", show_alert=True)
        return

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

    active_roles_alive = {p['role'] for p in game.players.values() if p['is_alive'] and p['role'] in ['Мафия', 'Доктор', 'Комиссар']}
    if set(game.night_actions.keys()) >= active_roles_alive:
        await process_night_results()


# --- Итоги ночи и переход к дню ---
async def process_night_results():
    shot_target = game.night_actions.get('Мафия')
    healed_target = game.night_actions.get('Доктор')

    await bot.send_message(game.chat_id, "☀️ **Наступает утро!** Город просыпается...", parse_mode="Markdown")

    if shot_target and shot_target != healed_target:
        game.players[shot_target]['is_alive'] = False
        killed_name = game.players[shot_target]['name']
        await bot.send_message(game.chat_id, f"💀 Этим утром город не досчитался игрока **{killed_name}**.", parse_mode="Markdown")
    else:
        await bot.send_message(game.chat_id, "🛡️ Ночь прошла успешно для горожан! Никто не погиб (либо доктор вовремя вылечил раненого).", parse_mode="Markdown")

    if await check_win_conditions():
        return

    # Запуск дневного голосования
    await start_day_voting()


# --- Фаза Дневного голосования (Линч) ---
async def start_day_voting():
    game.day_votes.clear()
    alive_players = {uid: p['name'] for uid, p in game.players.items() if p['is_alive']}

    builder = InlineKeyboardBuilder()
    for uid, name in alive_players.items():
        builder.button(text=f"⚖️ Изгнать {name}", callback_data=f"vote:{uid}")
    builder.adjust(1)

    await bot.send_message(
        game.chat_id,
        "💬 **Время обсуждений!** Город должен решить, кого изгнать на дневном суде. Сделайте свой выбор кнопкой ниже:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("vote:"))
async def cb_day_vote(callback: types.CallbackQuery):
    if not game.is_game_active:
        await callback.answer("Игра не активна!", show_alert=True)
        return

    voter_id = callback.from_user.id
    if voter_id not in game.players or not game.players[voter_id]['is_alive']:
        await callback.answer("Вы мертвы или не участвуете в игре!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    game.day_votes[voter_id] = target_id
    target_name = game.players[target_id]['name']

    await callback.answer(f"Вы проголосовали против: {target_name}")

    alive_count = sum(1 for p in game.players.values() if p['is_alive'])

    # Если все живые проголосовали — подводим итоги дня
    if len(game.day_votes) >= alive_count:
        await process_day_results()


async def process_day_results():
    if not game.day_votes:
        await bot.send_message(game.chat_id, "⚖️ Никто не проголосовал. Никто не изгнан.")
    else:
        vote_counts = {}
        for target_id in game.day_votes.values():
            vote_counts[target_id] = vote_counts.get(target_id, 0) + 1

        max_votes = max(vote_counts.values())
        candidates = [uid for uid, count in vote_counts.items() if count == max_votes]

        if len(candidates) == 1:
            lynched_id = candidates[0]
            game.players[lynched_id]['is_alive'] = False
            lynched_name = game.players[lynched_id]['name']
            lynched_role = game.players[lynched_id]['role']
            await bot.send_message(
                game.chat_id,
                f"⚖️ **Город вынес приговор!** Большинством голосов изгнан **{lynched_name}**.\nЕго роль была: **{lynched_role}**.",
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(game.chat_id, "⚖️ Голоса разделились поровну, сегодня никто не изгнан.")

    if await check_win_conditions():
        return

    # Кнопка для перехода к следующей ночи
    builder = InlineKeyboardBuilder()
    builder.button(text="🌙 Наступить ночь", callback_data="next_night")
    
    await bot.send_message(
        game.chat_id, 
        "🌇 Солнце садится... Нажмите кнопку, чтобы наступила ночь:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data == "next_night")
async def cb_next_night(callback: types.CallbackQuery):
    if not game.is_game_active:
        await callback.answer("Игра завершена!", show_alert=True)
        return
    await callback.message.delete()
    await start_night_phase()


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
