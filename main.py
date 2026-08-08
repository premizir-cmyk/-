import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatMemberStatus

TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_ID = -1004456753608
TARGET_CHAT_ID = -1004377702164
CHANNEL_URL = "https://t.me/otzovzaden" 

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Ловим вообще любое сообщение, которое приходит боту в поле зрения
@dp.message()
async def catch_all_messages(message: Message):
    # Выведем в лог хостинга информацию о каждом сообщении
    print(f"ПОЙМАНО СООБЩЕНИЕ! Чат ID: {message.chat.id}, Текст: {message.text}")
    
    if message.chat.id != TARGET_CHAT_ID:
        return
    
    if message.from_user.id == (await bot.get_me()).id:
        return
    
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=message.from_user.id)
        
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
            await message.delete()
            
            user_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)]
                ]
            )
            
            warning_msg = await message.answer(
                f"Привет, {user_mention}!\n❌ Чтобы писать сообщения в этом чате, необходимо подписаться на наш Telegram-канал.",
                reply_markup=keyboard
            )
            
            await asyncio.sleep(10)
            await warning_msg.delete()
            
    except Exception as e:
        print(f"ОШИБКА: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    print("Отладка запущена!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
