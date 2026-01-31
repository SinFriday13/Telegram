# -*- coding: utf-8 -*-
import time
from telegram.ext import ApplicationBuilder, ChatJoinRequestHandler

BOT_TOKEN = '8560025573:AAH9Y3Yz2psWSCU1XoYaIz_KC3pLS1s6iRc'
CHANNEL_ID = 2873921635  # spb

# Обработчик заявок
async def handle_join_request(update, context):
    user = update.chat_join_request.from_user
    user_id = user.id
    print(f"<{time.ctime()}> Заявка: {user_id} {user.username} {user.first_name} {user.last_name}")
    with open("pending_join_requests.txt", "a", encoding="utf-8") as f:
        f.write(f"{user_id}\n")

if __name__ == '__main__':
    # Создаем приложение
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Добавляем обработчик
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    
    print("Бот запущен, слушает заявки...")
    
    # Запускаем бота
    app.run_polling()