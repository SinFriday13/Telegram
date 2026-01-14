# -*- coding: utf-8 -*-
import asyncio
import random
import time

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import DialogFilter
import os

try:
    os.remove('SPB_razoslat.session-journal')
except Exception:
    pass

api_id = 24485067  # Ваш api_id
api_hash = 'a9548b05b3a2ac1c43ef157c90a83b54'  # Ваш api_hash
session_name = 'SPB_razoslat'
client = TelegramClient(session_name, api_id, api_hash)

message_text = "ВЗ подписка на тг. У меня бан, пишите в лс!\nНа ботов не перехожу! И в голосованиях не участвую!"

# Дополнительный вариант с обработкой разных типов диалогов:

async def send_to_vp_chats():
    result = await client(GetDialogFiltersRequest())
    folder = next((f for f in result.filters if isinstance(f, DialogFilter) and f.title == "ВП"), None)
    
    vp_chats = []
    
    if folder and hasattr(folder, 'include_peers'):
        # Получаем чаты из папки
        for peer in folder.include_peers:
            try:
                entity = await client.get_entity(peer)
                vp_chats.append(entity)
            except Exception as e:
                print(f"Не удалось получить сущность: {e}")
    else:
        # Если нет папки, ищем группы по названию или ключевым словам
        dialogs = [d async for d in client.iter_dialogs()]
        keywords = ['вп', 'взаимный', 'подписк', 'реклам', 'раскрут']
        
        for dialog in dialogs:
            if dialog.is_group or dialog.is_channel:
                name_lower = dialog.name.lower() if dialog.name else ''
                if any(keyword in name_lower for keyword in keywords):
                    vp_chats.append(dialog)
    
    print(f"Найдено {len(vp_chats)} чатов для рассылки")
    
    # Убираем дубликаты
    unique_chats = []
    seen_ids = set()
    for chat in vp_chats:
        if hasattr(chat, 'id'):
            if chat.id not in seen_ids:
                seen_ids.add(chat.id)
                unique_chats.append(chat)
    
    for chat in unique_chats:
        try:
            await client.send_message(chat, message_text)
            print(f'<{time.ctime()}> Отправлено в {getattr(chat, "title", getattr(chat, "name", "Unknown"))}')
        except Exception as e:
            chat_name = getattr(chat, 'title', getattr(chat, 'name', 'Unknown'))
            print(f'<{time.ctime()}> Ошибка для {chat_name}: {e}')
        await asyncio.sleep(random.randint(10, 15))
    
    return len(unique_chats)

async def main():
    await client.start()
    print("Клиент запущен. Начинаем рассылку...")
    
    for i in range(1, 1001):
        sent_count = await send_to_vp_chats()
        with open('cycle_logs.txt', "a", encoding="utf-8") as f:
            f.write(f"<{time.ctime()}> {i} Цикл, отправлено {sent_count} сообщений\n")
        print(f'<{time.ctime()}> {i} Цикл рассылки завершен ({sent_count} сообщений)')
        
        if i < 1000:  # Не ждать после последнего цикла
            sleep_time = random.randint(300, 420)
            print(f"Ожидание {sleep_time} секунд до следующего цикла...")
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    # Используем asyncio.run() для Python 3.14
    asyncio.run(main())