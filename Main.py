import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatMemberStatus

# Токен берется из переменных окружения хостинга
TOKEN = os.getenv("BOT_TOKEN")

# ID вашего канала и чата
CHANNEL_ID = -1004456753608
TARGET_CHAT_ID = -1004377702164

# Ссылка на канал для кнопки
CHANNEL_URL = "https://t.me/otzovzaden" 

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Убрали фильтр по типу контента, теперь ловит любые сообщения в нужном чате
@dp.message(F.chat.id == TARGET_CHAT_ID)
async def check_subscription(message: Message):
    # Игнорируем сообщения от самого бота
    if message.from_user.id == (await bot.get_me()).id:
        return
    
    try:
        # Проверяем статус пользователя в канале
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=message.from_user.id)
        
        # Если пользователь не подписан (статусы: left, kicked)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
            # Удаляем сообщение пользователя
            await message.delete()
            
            # Формируем упоминание пользователя
            user_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            # Создаем инлайн-кнопку с подпиской
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)]
                ]
            )
            
            # Отправляем предупреждение
            warning_msg = await message.answer(
                f"Привет, {user_mention}!\n❌ Чтобы писать сообщения в этом чате, необходимо подписаться на наш Telegram-канал.",
                reply_markup=keyboard
            )
            
            # Удаляем сообщение с предупреждением через 10 секунд
            await asyncio.sleep(10)
            await warning_msg.delete()
            
    except Exception as e:
        # Если возникнет ошибка, она покажется в логах хостинга (например, если бот не админ в канале)
        print(f"ОШИБКА В КАНАЛЕ/ЧАТЕ: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен и следит за чатом!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
