# -*- coding: utf-8 -*-
import time
import asyncio

from telethon import events
from telethon import TelegramClient
from telethon.tl.functions.channels import JoinChannelRequest
import re
import os

try:
    os.remove('SPB_checker.session-journal')
except Exception:
    pass

api_id = 24485067
api_hash = 'a9548b05b3a2ac1c43ef157c90a83b54'
session_name = 'SPB_checker'
client = TelegramClient(session_name, api_id, api_hash)

MY_CHANNEL = 3599072741  # username или id канала, а не ссылка
MY_CHANNEL_LINK = 'https://t.me/+ZWkdPBgwo7RkYmQy'
REQUESTS_FILE = 'requests.txt'
USERS_FILE = 'users.txt'
REMINDED_FILE = 'reminded.txt'
REPLIED_FILE = 'replied.txt'
PROCESSED_FILE = 'processed.txt'

black_list = [6893586741]

@client.on(events.NewMessage(incoming=True, chats=None))
async def handler(event):
    if event.is_private:
        text = event.raw_text
        user_id = str(event.sender_id)
        # Сохраняем user_id в users.txt, если его там нет
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                users = set(line.strip() for line in f if line.strip())
        except FileNotFoundError:
            users = set()
        if user_id not in users:
            with open(USERS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{user_id}\n")
        # Проверяем, есть ли ссылка на канал
        match = re.search(r'(https?://t\.me/\S+)', text)
        # Загружаем списки
        try:
            with open(REPLIED_FILE, "r", encoding="utf-8") as f:
                replied = set(line.strip() for line in f if line.strip())
        except FileNotFoundError:
            replied = set()
        try:
            with open(REMINDED_FILE, "r", encoding="utf-8") as f:
                reminded = set(line.strip() for line in f if line.strip())
        except FileNotFoundError:
            reminded = set()
        if user_id not in black_list:
            if match:
                channel_link = match.group(1)
                # Обновляем или добавляем ссылку для user_id в requests.txt
                try:
                    with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                except FileNotFoundError:
                    lines = []
                found = False
                with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
                    for line in lines:
                        if line.startswith(f"{user_id}:"):
                            f.write(f"{user_id}:{channel_link}\n")
                            found = True
                        else:
                            f.write(line)
                    if not found:
                        f.write(f"{user_id}:{channel_link}\n")
                # Отправляем ссылку на ваш канал, если еще не отправляли
                if user_id not in replied:
                    with open(REPLIED_FILE, "a", encoding="utf-8") as f:
                        f.write(f"{user_id}\n")
                    with open(REMINDED_FILE, "a", encoding="utf-8") as f:
                        f.write(f"{user_id}\n")
                    await asyncio.sleep(5)
                    await event.reply(f"Подай заявку в мой канал {MY_CHANNEL_LINK} я в ответ подпишусь")
                print(f"Получена ссылка от {user_id}: {channel_link}")
                with open('human_logs.txt', "a", encoding="utf-8") as f:
                    f.write(f"<{time.ctime()}> Получена ссылка от {user_id}: {channel_link}")
            else:
                # Отправляем напоминание только если это первое сообщение пользователя (и он еще не получал напоминание и ссылку)
                if user_id not in reminded and user_id not in replied:
                    with open(REMINDED_FILE, "a", encoding="utf-8") as f:
                        f.write(f"{user_id}\n")
                    await asyncio.sleep(5)
                    await event.reply(f"{MY_CHANNEL_LINK} Подай заявку на канал и пришли ссылку на твой канал\nВЗАИМНАЯ ПОДПИСКА ТОЛЬКО НА ТГ!\nНа ботов не перехожу! И в голосованиях не участвую!")


# ====== ФУНКЦИЯ ДЛЯ ЧЕКА ЗАЯВОК (pending join requests) ======
async def process_requests():
    # Загружаем уже обработанные user_id
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            processed = set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        processed = set()
    while True:
        try:
            # Читаем pending user_id из файла, который пишет бот на Bot API
            try:
                with open("pending_join_requests.txt", "r", encoding="utf-8") as f:
                    pending_ids = set(line.strip() for line in f if line.strip())
            except FileNotFoundError:
                pending_ids = set()
            # Читаем ваши заявки (user_id:channel_link)
            try:
                with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
                    req_lines = [line.strip() for line in f if line.strip()]
            except FileNotFoundError:
                req_lines = []
            req_dict = dict(line.split(":", 1) for line in req_lines if ":" in line)
            for user_id in pending_ids:
                if user_id in processed:
                    continue
                if user_id in req_dict:
                    channel_link = req_dict[user_id]
                    try:
                        # ИСПРАВЛЕНИЕ ЗДЕСЬ: используем get_entity для обработки инвайт-ссылки
                        entity = await client.get_entity(channel_link)
                        
                        await client(JoinChannelRequest(entity))
                        print(f"Подписался на {channel_link} для пользователя {user_id}")
                        with open('human_logs.txt', "a", encoding="utf-8") as f:
                            f.write(f"<{time.ctime()}> Подписался на {channel_link} для пользователя {user_id}\n")
                        # Отправляем сообщение пользователю
                        try:
                            await asyncio.sleep(5)
                            await client.send_message(int(user_id), "Я тоже подписался")
                        except Exception as e:
                            print(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
                        # Сохраняем user_id как обработанный
                        with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
                            f.write(f"{user_id}\n")
                        processed.add(user_id)
                    except Exception as e:
                        await client.send_message(1841056548, f"<{time.ctime()}> Ошибка при подписке на {channel_link}: {e}")
                        print(f"Ошибка при подписке на {channel_link}: {e}")

        except Exception as e:
            print(f"Ошибка в process_requests: {e}")
        print(f'<{time.ctime()}> Cycle end')
        await asyncio.sleep(15)

async def main():
    # Запускаем клиента и обработчик
    await client.start()
    print("Клиент запущен и слушает сообщения...")
    
    # Запускаем обе задачи параллельно
    await asyncio.gather(
        process_requests(),
        client.run_until_disconnected()
    )

if __name__ == "__main__":
    # Используем asyncio.run() с созданием event loop
    asyncio.run(main())