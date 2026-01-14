import asyncio
import logging
from typing import Optional, Dict, Any, List  # Добавьте List
from datetime import datetime
import json
import os
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import signal
import sys

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Для отладки можно временно включить DEBUG
logging.getLogger(__name__).setLevel(logging.DEBUG)


def signal_handler(signum, frame):
    """Обработчик сигналов для корректного завершения"""
    logger.info(f"Получен сигнал {signum}, завершение...")
    sys.exit(0)

# Регистрация обработчиков сигналов
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Глобальные переменные для доступа из обработчиков
_reposter_instance = None
_ai_service_instance = None

def get_reposter_instance():
    """Получить экземпляр TelegramReposter (для обработчиков)"""
    global _reposter_instance
    return _reposter_instance

def get_ai_service_instance(config=None):
    """Получить экземпляр AIService (для обработчиков)"""
    global _ai_service_instance
    if _ai_service_instance is None and config:
        _ai_service_instance = AIService(config)
    return _ai_service_instance

class Config:
    """Класс для хранения конфигурации"""
    def __init__(self):
        # Telegram API
        self.user_api_id = int(os.getenv('USER_API_ID', '38450983'))
        self.user_api_hash = os.getenv('USER_API_HASH', 'ae38cd298bfe81d26249057e3545b77c')
        self.bot_token = os.getenv('BOT_TOKEN', '8578681433:AAEzABMtQliuaXQ1G7WpXmCFMEletPCMi1U')
        
        # Админ
        self.admin_id = int(os.getenv('ADMIN_ID', '682841109'))
        
        # API ключи
        self.openai_api_key = os.getenv('OPENAI_API_KEY', 'io-v2-eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lciI6IjU4ZjBiNDgyLTA1MmEtNDgyYy1iZTY4LWY3NWZjYmYzNWRmMiIsImV4cCI6NDkyMDk3NDYyOH0.TS3CzQD4hqv-yedPoIwCWl19MYbJ8BIuwsDTfLKYxEGATyievv4QZmi8mzPwId9i52oBp9cb-tC8MMklwlVHdA')
        self.ai_provider = os.getenv('AI_PROVIDER', 'openai')
        
        # Чтение конфигурации каналов из файла или переменных окружения
        self.channel_pairs = self._load_channel_pairs()
        
        # Файл для хранения обработанных постов
        self.processed_posts_file = "processed_posts.json"

        # Настройки таймаутов для медиа
        self.download_timeout = int(os.getenv('DOWNLOAD_TIMEOUT', 120))  # 60 секунд на скачивание
        self.upload_timeout = int(os.getenv('UPLOAD_TIMEOUT', 150))    # 120 секунд на отправку
        self.media_group_timeout = int(os.getenv('MEDIA_GROUP_TIMEOUT', 180))  # 180 секунд на медиагруппу
        
        # Максимальный размер файла для обработки (в байтах)
        self.max_file_size = int(os.getenv('MAX_FILE_SIZE', 150 * 1024 * 1024))  # 50 МБ по умолчанию
        
    def _load_channel_pairs(self) -> List[Dict[str, str]]:
        """Загрузка пар каналов (источник -> целевой)"""
        pairs = []
        
        config_file = "channel_config.json"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    for pair in config_data.get('channel_pairs', []):
                        source = pair['source']
                        target = pair['target']
                        
                        if source.startswith('-100'):
                            source = int(source)
                        if target.startswith('-100'):
                            target = int(target)
                            
                        pairs.append({
                            'source': source,
                            'target': target,
                            'name': pair.get('name', f"{pair['source']} -> {pair['target']}"),
                            'username': pair.get('username', None)  # Добавляем username
                        })
                logger.info(f"Загружено {len(pairs)} пар каналов из {config_file}")
                return pairs
            except Exception as e:
                logger.error(f"Ошибка загрузки конфигурации каналов: {e}")
    
    def get_channel_username(self, source_channel: str) -> Optional[str]:
        """Получить username канала из конфигурации"""
        for pair in self.channel_pairs:
            if pair['source'] == source_channel:
                return pair.get('username')
        return None
    
    def get_source_channels(self) -> List[str]:
        """Получить список исходных каналов"""
        return [pair['source'] for pair in self.channel_pairs]
    
    def get_target_channel(self, source_channel: str) -> Optional[str]:
        """Получить целевой канал для исходного канала"""
        for pair in self.channel_pairs:
            if pair['source'] == source_channel:
                return pair['target']
        return None
    
    def get_pair_name(self, source_channel: str) -> Optional[str]:
        """Получить имя пары каналов"""
        for pair in self.channel_pairs:
            if pair['source'] == source_channel:
                return pair['name']
        return None

class TelegramClientManager:
    """Управление Telegram клиентами"""
    
    def __init__(self, config: Config):
        self.config = config
        self.user_client = None
        self.bot_client = None
        self.pending_posts: Dict[str, Dict[str, Any]] = {}  # Хранилище для постов ожидающих одобрения
        self.parse_mode = ParseMode.HTML
        
    async def init_user_client(self):
        """Инициализация пользовательского клиента для парсинга"""
        try:
            from telethon import TelegramClient, events
            from telethon.sessions import StringSession
            
            session_string = os.getenv('USER_SESSION_STRING', '1ApWapzMBu1abjQ-NfWuHgQap5f4_1dDH5rRuuyKF-Xk75NCOkSAOszHjwBktSruevPQgx8ORx-TdXgCt-wgvbMdzsEZp4d4lC_uFV44TpC0X9LwjjucC7eHxH9JWbuN3j3nI6-6U62_dKWsCRXMZetOPoM_DDuYU-jdIOejIxpyNlKdh586YdlCZlqbD-pjqzckd8B7UTfbqsh8zTEJKp4y1Xq1cmO8O8uoTgk--t6qtbB7RDXihIC-IYFClJDou5r6GOdlML86M5jOOsCJleeZ_E4WbB2BU5Zqt4SDGhk4x1PRQwaUs9qr5qt5upiEHvUxBel1Z_0rXhqbtYiUONe5KZ0G_R_E=')
            if not session_string:
                # Создание новой сессии при первом запуске
                session = StringSession()
                self.user_client = TelegramClient(
                    session,
                    self.config.user_api_id,
                    self.config.user_api_hash
                )
                
                await self.user_client.start()
                session_string = self.user_client.session.save()
                logger.info(f"Новая сессия создана. Сохраните в .env: USER_SESSION_STRING={session_string}")
                logger.info("Пожалуйста, перезапустите скрипт с сохраненной сессией")
                exit(0)
            else:
                session = StringSession(session_string)
                self.user_client = TelegramClient(
                    session,
                    self.config.user_api_id,
                    self.config.user_api_hash
                )
                await self.user_client.start()
                logger.info("Пользовательский клиент инициализирован")
                
            return self.user_client
            
        except ImportError:
            logger.error("Установите telethon: pip install telethon")
            exit(1)
        except Exception as e:
            logger.error(f"Ошибка инициализации пользовательского клиента: {e}")
            raise
            
    async def init_bot_client(self):
        """Инициализация бота"""
        try:
            from aiogram import Bot, Dispatcher, Router, F
            from aiogram.types import (
                InlineKeyboardMarkup, 
                InlineKeyboardButton,
                Message,
                CallbackQuery,
                InputFile  # Убедитесь, что InputFile импортирован
            )
            from aiogram.enums import ParseMode
            from aiogram.client.default import DefaultBotProperties
            from aiogram.filters import Command
            # ... остальной код без изменений
            
            # Инициализация бота с новым синтаксисом aiogram 3.7+
            self.bot = Bot(
                token=self.config.bot_token, 
                default=DefaultBotProperties(parse_mode=ParseMode.HTML)
            )
            
            # Создаем роутер и диспетчер
            self.router = Router()
            self.dp = Dispatcher()
            self.dp.include_router(self.router)
            
            # Хранилище для постов ожидающих одобрения
            self.pending_posts: Dict[str, Dict[str, Any]] = {}
            
            # Настройка обработчиков кнопок с использованием фильтров
            @self.router.callback_query(F.data.startswith("approve_"))
            async def handle_approve(callback_query: CallbackQuery):
                try:
                    data = callback_query.data
                    # Разбираем callback_id: "source_channel_message_id"
                    parts = data.split('_')
                    if len(parts) < 3:
                        await callback_query.answer("Неверный формат callback", show_alert=True)
                        return
                    
                    source_channel = '_'.join(parts[1:-1])  # Восстанавливаем название канала
                    post_id = parts[-1]
                    full_callback_id = f"{source_channel}_{post_id}"
                    
                    logger.info(f"Обработка одобрения для поста ID: {full_callback_id}")
                    
                    if full_callback_id not in self.pending_posts:
                        logger.warning(f"Пост {full_callback_id} не найден в pending_posts")
                        await callback_query.answer("Пост уже обработан или устарел", show_alert=True)
                        try:
                            await callback_query.message.edit_reply_markup(reply_markup=None)
                        except:
                            pass
                        return
                    
                    post_data = self.pending_posts[full_callback_id]
                    
                    # Определяем тип поста для правильного сообщения
                    is_album = post_data.get('is_album', False)
                    album_count = post_data.get('album_count', 1)
                    target_channel = post_data.get('target_channel', 'целевой канал')
                    
                    # Отправка поста в целевой канал
                    await self.send_to_channel(post_data)
                    
                    # Формируем сообщение в зависимости от типа поста
                    if is_album:
                        await callback_query.answer(f"✅ Альбом ({album_count} медиа) одобрен и отправлен!", show_alert=True)
                        status_text = f"✅ Альбом ({album_count} медиа) одобрен и отправлен в {target_channel}"
                    else:
                        await callback_query.answer("✅ Пост одобрен и отправлен!", show_alert=True)
                        status_text = f"✅ Пост одобрен и отправлен в {target_channel}"
                    
                    # Редактируем сообщение в зависимости от типа поста и типа сообщения
                    has_media = post_data.get('has_media', False)
                    
                    # Проверяем, является ли это сообщение с медиагруппой (альбом)
                    # Для альбомов мы не можем редактировать сообщение, т.к. это медиагруппа
                    # Проверяем, есть ли у нас сообщение с кнопками (отправляется отдельно для альбомов)
                    try:
                        if has_media and callback_query.message.photo:
                            # Для одиночных фото
                            await callback_query.message.edit_caption(
                                caption=status_text,
                                reply_markup=None
                            )
                        elif has_media and (callback_query.message.video or callback_query.message.document):
                            # Для одиночных видео/документов
                            await callback_query.message.edit_caption(
                                caption=status_text,
                                reply_markup=None
                            )
                        elif callback_query.message.text and "Управление альбомом" in callback_query.message.text:
                            # Для сообщения управления альбомом
                            await callback_query.message.edit_text(
                                status_text,
                                reply_markup=None,
                                disable_web_page_preview=True
                            )
                        else:
                            # Для текстовых сообщений
                            await callback_query.message.edit_text(
                                status_text,
                                reply_markup=None,
                                disable_web_page_preview=True
                            )
                    except Exception as edit_error:
                        logger.warning(f"Не удалось отредактировать сообщение: {edit_error}")
                        # Пробуем отправить новое сообщение
                        try:
                            await callback_query.message.reply(status_text)
                        except:
                            pass
                    
                    # Удаление из ожидающих
                    del self.pending_posts[full_callback_id]
                    logger.info(f"Пост {full_callback_id} одобрен и удален из ожидающих")
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки одобрения: {e}")
                    await callback_query.answer("Произошла ошибка при обработке", show_alert=True)
            
            @self.router.callback_query(F.data.startswith("reject_"))
            async def handle_reject(callback_query: CallbackQuery):
                try:
                    data = callback_query.data
                    # Разбираем callback_id: "source_channel_message_id"
                    parts = data.split('_')
                    if len(parts) < 3:
                        await callback_query.answer("Неверный формат callback", show_alert=True)
                        return
                    
                    source_channel = '_'.join(parts[1:-1])  # Восстанавливаем название канала
                    post_id = parts[-1]
                    full_callback_id = f"{source_channel}_{post_id}"
                    
                    if full_callback_id not in self.pending_posts:
                        logger.warning(f"Пост {full_callback_id} не найден в pending_posts при отклонении")
                        await callback_query.answer("Пост уже обработан", show_alert=True)
                        try:
                            await callback_query.message.edit_reply_markup(reply_markup=None)
                        except:
                            pass
                        return
                    
                    post_data = self.pending_posts[full_callback_id]
                    
                    # Определяем тип поста для правильного сообщения
                    is_album = post_data.get('is_album', False)
                    album_count = post_data.get('album_count', 1)
                    
                    # Формируем сообщение в зависимости от типа поста
                    if is_album:
                        await callback_query.answer(f"❌ Альбом ({album_count} медиа) отклонен", show_alert=True)
                        status_text = "❌ Альбом отклонен"
                    else:
                        await callback_query.answer("❌ Пост отклонен", show_alert=True)
                        status_text = "❌ Пост отклонен"
                    
                    # Пробуем редактировать сообщение
                    try:
                        if callback_query.message.photo:
                            await callback_query.message.edit_caption(
                                caption=status_text,
                                reply_markup=None
                            )
                        elif callback_query.message.video or callback_query.message.document:
                            await callback_query.message.edit_caption(
                                caption=status_text,
                                reply_markup=None
                            )
                        elif callback_query.message.text and "Управление альбомом" in callback_query.message.text:
                            await callback_query.message.edit_text(
                                status_text,
                                reply_markup=None,
                                disable_web_page_preview=True
                            )
                        else:
                            await callback_query.message.edit_text(
                                status_text,
                                reply_markup=None,
                                disable_web_page_preview=True
                            )
                    except Exception as edit_error:
                        logger.warning(f"Не удалось отредактировать сообщение: {edit_error}")
                        try:
                            await callback_query.message.reply(status_text)
                        except:
                            pass
                    
                    # Удаление из ожидающих
                    del self.pending_posts[full_callback_id]
                    logger.info(f"Пост {full_callback_id} отклонен и удален из ожидающих")
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки отклонения: {e}")
                    await callback_query.answer("Произошла ошибка при обработке", show_alert=True)
            
            @self.router.callback_query(F.data.startswith("regenerate_"))
            async def handle_regenerate(callback_query: CallbackQuery):
                try:
                    data = callback_query.data
                    # Разбираем callback_id: "source_channel_message_id"
                    parts = data.split('_')
                    if len(parts) < 3:
                        await callback_query.answer("Неверный формат callback", show_alert=True)
                        return
                    
                    source_channel = '_'.join(parts[1:-1])  # Восстанавливаем название канала
                    post_id = parts[-1]
                    full_callback_id = f"{source_channel}_{post_id}"
                    
                    logger.info(f"Запрос на перегенерацию поста ID: {full_callback_id}")
                    
                    if full_callback_id not in self.pending_posts:
                        await callback_query.answer("Пост не найден или уже обработан", show_alert=True)
                        try:
                            await callback_query.message.edit_reply_markup(reply_markup=None)
                        except:
                            pass
                        return
                    
                    post_data = self.pending_posts[full_callback_id]
                    
                    # Определяем тип поста для правильного сообщения
                    is_album = post_data.get('is_album', False)
                    
                    # Формируем сообщение в зависимости от типа поста
                    if is_album:
                        await callback_query.answer("🔄 Перегенерация текста для альбома...", show_alert=False)
                    else:
                        await callback_query.answer("🔄 Перегенерация текста...", show_alert=False)
                    
                    # Получаем доступ к экземпляру TelegramReposter через глобальную переменную
                    from main import get_reposter_instance
                    reposter = get_reposter_instance()
                    
                    if not reposter or not reposter.post_parser:
                        await callback_query.answer("Ошибка: не найден парсер", show_alert=True)
                        return
                    
                    # Перегенерируем текст
                    regenerated_post = await reposter.post_parser.regenerate_post(post_data)
                    
                    # Обновляем пост в pending_posts
                    self.pending_posts[full_callback_id] = regenerated_post
                    
                    # Обновляем сообщение админу
                    await self.update_admin_message(callback_query, regenerated_post, full_callback_id)
                    
                    # Формируем ответ в зависимости от типа поста
                    if is_album:
                        await callback_query.answer("✅ Текст альбома успешно перегенерирован!", show_alert=True)
                    else:
                        await callback_query.answer("✅ Текст успешно перегенерирован!", show_alert=True)
                    
                    logger.info(f"Пост {full_callback_id} успешно перегенерирован")
                    
                except Exception as e:
                    logger.error(f"Ошибка перегенерации: {e}", exc_info=True)
                    await callback_query.answer("❌ Ошибка при перегенерации", show_alert=True)
            
            # Обновите метод update_admin_message, чтобы он был методом класса
            async def update_admin_message(self, callback_query, post_data: Dict[str, Any], post_id: str):
                """Обновление сообщения админу с новым текстом"""
                try:
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    from aiogram.enums import ParseMode
                    
                    text = post_data['modified_text']
                    original_link = post_data.get('original_link', '')
                    
                    # Определяем тип поста
                    is_album = post_data.get('is_album', False)
                    album_count = post_data.get('album_count', 1)
                    
                    # Добавляем информацию о перегенерациях
                    regen_count = post_data.get('regeneration_count', 0)
                    regen_info = f"\n🔄 <i>Перегенераций:</i> {regen_count}" if regen_count > 0 else ""
                    
                    pair_name = post_data.get('pair_name', 'Неизвестный канал')
                    source_channel = post_data.get('source_channel', 'Неизвестный источник')
                    target_channel = post_data.get('target_channel', 'Неизвестный получатель')
                    
                    # Формируем заголовок в зависимости от типа поста
                    if is_album:
                        header = f"📸 <b>Новый АЛЬБОМ для публикации</b> ({album_count} медиа)\n"
                    else:
                        header = f"📝 <b>Новый пост для публикации</b>\n"
                    
                    caption = (
                        f"{header}"
                        f"<i>Пара каналов:</i> {pair_name}\n"
                        f"<i>Источник:</i> {source_channel}\n"
                        f"<i>Целевой канал:</i> {target_channel}\n\n"
                        f"<i>Оригинал:</i> <a href=\"{original_link}\">{original_link}</a>\n\n"
                        f"{text}\n\n"
                        f"<i>Длина текста:</i> {len(text)} символов"
                        f"{regen_info}"
                    )
                    
                    # Обновляем клавиатуру
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✅ Одобрить" + (" альбом" if is_album else ""),
                                    callback_data=f"approve_{post_id}"
                                ),
                                InlineKeyboardButton(
                                    text="❌ Отклонить" + (" альбом" if is_album else ""),
                                    callback_data=f"reject_{post_id}"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="🔄 Перегенерировать" + (" текст" if is_album else ""),
                                    callback_data=f"regenerate_{post_id}"
                                )
                            ]
                        ]
                    )
                    
                    # Проверяем тип сообщения для обновления
                    if callback_query.message.photo:
                        # Одиночное фото
                        await callback_query.message.edit_caption(
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard
                        )
                    elif callback_query.message.video or callback_query.message.document:
                        # Одиночное видео/документ
                        await callback_query.message.edit_caption(
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard
                        )
                    elif callback_query.message.text and "Управление альбомом" in callback_query.message.text:
                        # Сообщение управления альбомом
                        await callback_query.message.edit_text(
                            text=f"📸 <b>Управление альбомом</b>\n\n{caption}",
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard,
                            disable_web_page_preview=True
                        )
                    else:
                        # Текстовое сообщение
                        await callback_query.message.edit_text(
                            text=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard,
                            disable_web_page_preview=True
                        )
                        
                except Exception as e:
                    logger.error(f"Ошибка обновления сообщения админу: {e}")
                    # В случае ошибки, просто уведомляем пользователя
                    await callback_query.answer("⚠️ Текст обновлен, но не удалось обновить сообщение", show_alert=True)

            logger.info("Бот инициализирован")
            return self.bot
            
        except ImportError:
            logger.error("Установите aiogram: pip install aiogram==3.10.0")
            exit(1)
        except Exception as e:
            logger.error(f"Ошибка инициализации бота: {e}")
            raise
    
    async def send_with_proper_formatting(self, chat_id: int, text: str, **kwargs):
        """Отправка сообщения с правильным форматированием"""
        try:
            from aiogram.enums import ParseMode

            disable_web_page_preview = kwargs.pop('disable_web_page_preview', None)

            # Проверяем, есть ли HTML теги в тексте
            has_html_tags = any(tag in text for tag in ['<b>', '<i>', '<u>', '<code>', '<pre>', '<a href='])

            if has_html_tags:
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=disable_web_page_preview,
                    **kwargs
                )
            else:
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_web_page_preview=disable_web_page_preview,
                    **kwargs
                )
        except Exception as e:
            logger.error(f"Ошибка отправки с форматированием: {e}")
            # Fallback: отправляем без форматирования
            return await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_web_page_preview=disable_web_page_preview,
                **kwargs
            )

    async def send_message_with_formatting(self, chat_id: int, text: str, **kwargs):
        """Отправка сообщения с сохранением форматирования"""
        try:
            from aiogram.enums import ParseMode
            
            # Убираем disable_web_page_preview из kwargs если есть
            disable_web_page_preview = kwargs.pop('disable_web_page_preview', None)
            
            # Всегда конвертируем в HTML для единообразия
            html_text = self._markdown_to_html(text)
            
            # Проверяем, есть ли HTML теги в тексте
            has_html_tags = any(tag in html_text for tag in ['<b>', '<i>', '<u>', '<code>', '<pre>', '<a href='])
            
            if has_html_tags:
                try:
                    return await self.bot.send_message(
                        chat_id=chat_id,
                        text=html_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=disable_web_page_preview,
                        **kwargs
                    )
                except Exception as html_error:
                    logger.warning(f"HTML отправка не удалась, отправляем как обычный текст: {html_error}")
                    # Если HTML не сработал, убираем теги
                    plain_text = self._strip_html_tags(html_text)
                    return await self.bot.send_message(
                        chat_id=chat_id,
                        text=plain_text,
                        disable_web_page_preview=disable_web_page_preview,
                        **kwargs
                    )
            else:
                # Если нет разметки, отправляем как есть
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=html_text,
                    disable_web_page_preview=disable_web_page_preview,
                    **kwargs
                )
                
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения с форматированием: {e}")
            raise

    def _markdown_to_html_simple(self, text: str) -> str:
        """Простая конвертация Markdown в HTML"""
        import re
        
        # Сначала экранируем HTML
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # **жирный** -> <b>жирный</b>
        # Используем нежадный квантификатор и обрабатываем в цикле
        while '**' in text:
            parts = text.split('**', 2)
            if len(parts) == 3:
                text = parts[0] + '<b>' + parts[1] + '</b>' + parts[2]
            else:
                break
        
        # *курсив* -> <i>курсив</i>
        # Убедимся, что это не часть жирного
        while '*' in text and '<b>' not in text:
            parts = text.split('*', 2)
            if len(parts) == 3:
                text = parts[0] + '<i>' + parts[1] + '</i>' + parts[2]
            else:
                break
        
        return text
    
    def _strip_markdown(self, text: str) -> str:
        """Удаление Markdown разметки"""
        import re
        
        # Удаляем **
        text = re.sub(r'\*\*', '', text)
        
        # Удаляем * (кроме тех, что могут быть частью смайликов или других символов)
        text = re.sub(r'(?<!\w)\*(?!\w)', '', text)
        
        # Удаляем __
        text = re.sub(r'__', '', text)
        
        # Удаляем `
        text = re.sub(r'`', '', text)
        
        return text
    
    def _convert_markdown_to_html(self, text: str) -> str:
        """Конвертация Markdown в HTML"""
        import re
        
        # Экранируем HTML символы
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # **жирный** -> <b>жирный</b>
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
        
        # *курсив* -> <i>курсив</i>
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text, flags=re.DOTALL)
        
        # __подчеркивание__ -> <u>подчеркивание</u>
        text = re.sub(r'__(.+?)__', r'<u>\1</u>', text, flags=re.DOTALL)
        
        # `код` -> <code>код</code>
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text, flags=re.DOTALL)
        
        return text
    
    def _strip_html_tags(self, text: str) -> str:
        """Удаление HTML тегов"""
        import re
        
        # Удаляем все HTML теги
        text = re.sub(r'<[^>]+>', '', text)
        
        # Восстанавливаем специальные символы
        text = (
            text.replace('&amp;', '&')
                .replace('&lt;', '<')
                .replace('&gt;', '>')
        )
        
        return text
    
    def _markdown_to_html(self, text: str) -> str:
        """Конвертация Markdown в HTML для Telegram"""
        if not text:
            return text
        
        import re
        
        # Временная замена для экранирования
        text = (
            text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
        )
        
        # **жирный текст** -> <b>жирный текст</b>
        # Используем жадное сопоставление
        text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
        
        # *курсив* -> <i>курсив</i>
        # Убедимся, что это не просто звездочка или не начало другого формата
        text = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<i>\1</i>', text)
        
        # __подчеркнутый__ -> <u>подчеркнутый</u>
        text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)
        
        # `код` -> <code>код</code>
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
        
        # [ссылка](текст) -> <a href="ссылка">текст</a>
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
        
        return text
    
    def _strip_markdown(self, text: str) -> str:
        """Удаление Markdown разметки"""
        import re
        
        # Удаляем ** **
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        
        # Удаляем * * (курсив)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        
        # Удаляем __ __
        text = re.sub(r'__(.*?)__', r'\1', text)
        
        # Удаляем ` `
        text = re.sub(r'`(.*?)`', r'\1', text)
        
        # Удаляем [текст](ссылка) -> текст
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        return text

    async def send_media_to_admin(self, post_data: Dict[str, Any]):
        """Отправка поста с медиа админу на одобрение"""
        try:
            from aiogram.types import (
                InlineKeyboardMarkup, 
                InlineKeyboardButton
            )
            from aiogram.enums import ParseMode
            
            text = post_data['modified_text']
            original_link = post_data.get('original_link', '')
            message = post_data.get('message')
            
            pair_name = post_data.get('pair_name', 'Неизвестный канал')
            source_channel = post_data.get('source_channel', 'Неизвестный источник')
            target_channel = post_data.get('target_channel', 'Неизвестный получатель')
            
            # Используем уникальный ID поста с идентификатором канала
            callback_post_id = str(post_data['message_id'])
            full_callback_id = f"{source_channel}_{callback_post_id}"
            
            # Добавляем информацию о перегенерациях
            regen_count = post_data.get('regeneration_count', 0)
            regen_info = f"\n🔄 <i>Перегенераций:</i> {regen_count}" if regen_count > 0 else ""
            
            caption = (
                f"📝 <b>Новый пост для публикации</b>\n"
                f"<i>Пара каналов:</i> {pair_name}\n"
                f"<i>Источник:</i> {source_channel}\n"
                f"<i>Целевой канал:</i> {target_channel}\n\n"
                f"<i>Оригинал:</i> <a href=\"{original_link}\">{original_link}</a>\n\n"
                f"{text}\n\n"
                f"<i>Длина:</i> {len(text)} символов"
                f"{regen_info}"
            )
            
            # Создаем клавиатуру с кнопками Одобрить/Отклонить/Перегенерировать
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Одобрить",
                            callback_data=f"approve_{full_callback_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"reject_{full_callback_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔄 Перегенерировать",
                            callback_data=f"regenerate_{full_callback_id}"
                        )
                    ]
                ]
            )
            
            if not post_data['has_media']:
                # Если нет медиа, отправляем просто текст
                await self.bot.send_message(
                    chat_id=self.config.admin_id,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True
                )
            else:
                # Обрабатываем медиа
                await self._handle_media_send(
                    message=message,
                    chat_id=self.config.admin_id,
                    caption=caption,
                    reply_markup=keyboard
                )
            
            # Сохраняем с тем же ID, который используется в callback
            self.pending_posts[full_callback_id] = post_data
            
            logger.info(f"Пост с медиа из {source_channel} отправлен админу на одобрение (ID: {full_callback_id})")
            
        except Exception as e:
            logger.error(f"Ошибка отправки медиа админу: {e}")
            raise
    
    async def _handle_media_send(self, message, chat_id: int, caption: str, reply_markup=None):
        """Обработка отправки одиночного медиа"""
        try:
            from aiogram.types import FSInputFile
            from aiogram.enums import ParseMode
            import tempfile
            import os
            
            media = message.media
            
            if not media:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
                return
            
            # Создаем временный файл
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                file_path = tmp_file.name
            
            try:
                # Скачиваем медиа в файл
                await message.download_media(file_path)
                
                if os.path.getsize(file_path) == 0:
                    raise Exception("Файл пустой")
                
                # Создаем FSInputFile
                fs_file = FSInputFile(file_path)
                
                # Определяем тип медиа
                from telethon.tl.types import (
                    MessageMediaPhoto,
                    MessageMediaDocument,
                    DocumentAttributeVideo,
                    DocumentAttributeAnimated
                )
                
                if isinstance(media, MessageMediaPhoto):
                    # Фото
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=fs_file,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                    
                elif isinstance(media, MessageMediaDocument):
                    # Документ - проверяем, видео ли это или GIF
                    is_video = False
                    is_animated = False
                    
                    if hasattr(media, 'document') and hasattr(media.document, 'attributes'):
                        for attr in media.document.attributes:
                            if isinstance(attr, DocumentAttributeVideo):
                                is_video = True
                            elif isinstance(attr, DocumentAttributeAnimated):
                                is_animated = True
                    
                    if is_video:
                        # Видео
                        await self.bot.send_video(
                            chat_id=chat_id,
                            video=fs_file,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=reply_markup
                        )
                    elif is_animated:
                        # Анимированный файл (GIF)
                        await self.bot.send_animation(
                            chat_id=chat_id,
                            animation=fs_file,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=reply_markup
                        )
                    else:
                        # Обычный документ
                        await self.bot.send_document(
                            chat_id=chat_id,
                            document=fs_file,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=reply_markup
                        )
                else:
                    # Неизвестный тип - отправляем как документ
                    await self.bot.send_document(
                        chat_id=chat_id,
                        document=fs_file,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                        
            finally:
                # Удаляем временный файл
                try:
                    os.unlink(file_path)
                except:
                    pass
                
        except Exception as e:
            logger.error(f"Ошибка обработки медиа: {e}", exc_info=True)
            # Fallback: отправляем только текст
            await self.bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )

    async def send_text_to_admin(self, post_data: Dict[str, Any]):
        """Отправка текстового поста админу на одобрение"""
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from aiogram.enums import ParseMode
            
            text = post_data['modified_text']
            original_link = post_data.get('original_link', '')
            
            pair_name = post_data.get('pair_name', 'Неизвестный канал')
            source_channel = post_data.get('source_channel', 'Неизвестный источник')
            target_channel = post_data.get('target_channel', 'Неизвестный получатель')
            
            callback_post_id = str(post_data['message_id'])
            full_callback_id = f"{source_channel}_{callback_post_id}"
            
            # Добавляем информацию о перегенерациях
            regen_count = post_data.get('regeneration_count', 0)
            regen_info = f"\n🔄 <i>Перегенераций:</i> {regen_count}" if regen_count > 0 else ""
            
            message_text = (
                f"📝 <b>Новый текстовый пост для публикации</b>\n"
                f"<i>Пара каналов:</i> {pair_name}\n"
                f"<i>Источник:</i> {source_channel}\n"
                f"<i>Целевой канал:</i> {target_channel}\n\n"
                f"<i>Оригинал:</i> <a href=\"{original_link}\">{original_link}</a>\n\n"
                f"{text}\n\n"
                f"<i>Длина:</i> {len(text)} символов"
                f"{regen_info}"
            )
            
            # Создаем клавиатуру с кнопками
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Одобрить",
                            callback_data=f"approve_{full_callback_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"reject_{full_callback_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔄 Перегенерировать",
                            callback_data=f"regenerate_{full_callback_id}"
                        )
                    ]
                ]
            )
            
            await self.bot.send_message(
                chat_id=self.config.admin_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
            
            self.pending_posts[full_callback_id] = post_data
            logger.info(f"Текстовый пост из {source_channel} отправлен админу (ID: {full_callback_id})")
            
        except Exception as e:
            logger.error(f"Ошибка отправки текстового поста админу: {e}")
            raise

    async def send_text_to_channel(self, post_data: Dict[str, Any]):
        """Отправка текстового поста в целевой канал"""
        try:
            source_channel = post_data.get('source_channel', 'Неизвестный источник')
            target_channel = post_data.get('target_channel', 'Неизвестный получатель')
            
            logger.info(f"Отправка текстового поста из {source_channel} в {target_channel}")
            
            text = post_data['modified_text']
            
            # Конвертируем Markdown в HTML
            html_text = self._markdown_to_html(text)
            
            # Преобразуем target_channel в int, если это числовой ID
            if isinstance(target_channel, str) and target_channel.startswith('-100'):
                chat_id = int(target_channel)
            elif isinstance(target_channel, int):
                chat_id = target_channel
            else:
                chat_id = target_channel
            
            await self.send_with_proper_formatting(
                chat_id=chat_id,
                text=html_text,
                disable_web_page_preview=True
            )
            
            logger.info(f"Текстовый пост отправлен из {source_channel} в {target_channel}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки текстового поста в канал {post_data.get('target_channel')}: {e}")
            raise

    async def send_to_admin(self, post_data: Dict[str, Any]):
        """Отправка поста админу на одобрение (главный метод-роутер)"""
        try:
            # Определяем тип поста и отправляем соответствующему методу
            if post_data.get('is_album', False):
                # Это альбом (несколько медиа)
                logger.info(f"Отправка альбома админу: {post_data.get('album_count', 1)} медиа")
                await self.send_album_to_admin(post_data)
            elif post_data.get('has_media', False):
                # Это одиночный пост с медиа
                logger.info(f"Отправка одиночного поста с медиа админу")
                await self.send_media_to_admin(post_data)
            else:
                # Это текстовый пост без медиа
                logger.info(f"Отправка текстового поста админу")
                await self.send_text_to_admin(post_data)
                
        except Exception as e:
            logger.error(f"Ошибка отправки админу: {e}")
            # В случае ошибки пробуем отправить хотя бы текстовую версию
            try:
                fallback_text = (
                    f"❌ <b>Ошибка отправки поста</b>\n\n"
                    f"<i>Источник:</i> {post_data.get('source_channel', 'Неизвестно')}\n"
                    f"<i>Текст:</i> {post_data.get('modified_text', '')[:200]}...\n\n"
                    f"<i>Ошибка:</i> {str(e)[:100]}"
                )
                await self.bot.send_message(
                    chat_id=self.config.admin_id,
                    text=fallback_text,
                    parse_mode="HTML"
                )
            except:
                pass
            raise

    def _markdown_to_html_for_admin(self, text: str) -> str:
        """Специальная конвертация для админа"""
        import re
        
        # Экранируем HTML
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        # Заменяем ** на <b> (простой способ)
        # Разбиваем текст по **
        parts = text.split('**')
        result_parts = []
        
        for i, part in enumerate(parts):
            if i % 2 == 0:
                # Четные части - обычный текст
                # Обрабатываем курсив внутри
                part_parts = part.split('*')
                sub_result = []
                for j, sub_part in enumerate(part_parts):
                    if j % 2 == 0:
                        sub_result.append(sub_part)
                    else:
                        sub_result.append(f'<i>{sub_part}</i>')
                result_parts.append(''.join(sub_result))
            else:
                # Нечетные части - жирный текст
                result_parts.append(f'<b>{part}</b>')
        
        return ''.join(result_parts)

    async def send_media_to_channel(self, post_data: Dict[str, Any]):
        """Отправка поста с медиа в целевой канал"""
        try:
            text = post_data['modified_text']
            message = post_data.get('message')
            target_channel = post_data.get('target_channel', '')
            
            if not target_channel:
                logger.error("Не указан целевой канал")
                return
            
            # Конвертируем Markdown в HTML для подписи
            caption = self._markdown_to_html(text) if text else None
    
            if not post_data['has_media']:
                # Если нет медиа, отправляем просто текст
                await self.send_with_proper_formatting(
                    chat_id=target_channel,
                    text=caption,
                    disable_web_page_preview=True
                )
            else:
                # Отправляем медиа в канал
                await self._send_media_to_channel_internal(message, caption, target_channel)
                
            logger.info(f"Пост с медиа отправлен в канал {target_channel}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки медиа в канал {post_data.get('target_channel')}: {e}")
            raise
        
    async def send_album_simple_reliable(self, post_data: Dict[str, Any], is_to_admin: bool = True):
        """Простой и надежный способ отправки альбома"""
        try:
            from aiogram.types import (
                InlineKeyboardMarkup, 
                InlineKeyboardButton,
                FSInputFile,
                InputMediaPhoto,
                InputMediaVideo,
                InputMediaDocument
            )
            from aiogram.enums import ParseMode
            
            text = post_data['modified_text']
            original_link = post_data.get('original_link', '')
            album_messages = post_data.get('messages', [])
            album_count = post_data.get('album_count', 1)
            
            pair_name = post_data.get('pair_name', 'Неизвестный канал')
            source_channel = post_data.get('source_channel', 'Неизвестный источник')
            target_channel = post_data.get('target_channel', 'Неизвестный получатель')
            
            callback_post_id = str(post_data['message_id'])
            full_callback_id = f"{source_channel}_{callback_post_id}"
            
            # Определяем куда отправляем
            chat_id = self.config.admin_id if is_to_admin else target_channel
            
            regen_count = post_data.get('regeneration_count', 0)
            regen_info = f"\n🔄 <i>Перегенераций:</i> {regen_count}" if regen_count > 0 else ""
            
            # Формируем заголовок
            if is_to_admin:
                header = (
                    f"📸 <b>Новый АЛЬБОМ для публикации</b> ({album_count} медиа)\n"
                    f"<i>Пара каналов:</i> {pair_name}\n"
                    f"<i>Источник:</i> {source_channel}\n"
                    f"<i>Целевой канал:</i> {target_channel}\n\n"
                    f"<i>Оригинал:</i> <a href=\"{original_link}\">{original_link}</a>\n\n"
                    f"{text}\n\n"
                    f"<i>Длина текста:</i> {len(text)} символов"
                    f"{regen_info}"
                )
            else:
                # ДЛЯ ОТПРАВКИ В КАНАЛ - конвертируем Markdown в HTML
                header = self._markdown_to_html(text)
            
            # Создаем клавиатуру (только для админа)
            keyboard = None
            if is_to_admin:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Одобрить альбом",
                                callback_data=f"approve_{full_callback_id}"
                            ),
                            InlineKeyboardButton(
                                text="❌ Отклонить альбом",
                                callback_data=f"reject_{full_callback_id}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔄 Перегенерировать текст",
                                callback_data=f"regenerate_{full_callback_id}"
                            )
                        ]
                    ]
                )
            
            # Создаем медиагруппу
            media_group = []
            temp_files = []
            
            try:
                for i, message in enumerate(album_messages):
                    import tempfile
                    import os
                    
                    # Создаем временный файл
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.media') as tmp_file:
                        file_path = tmp_file.name
                        temp_files.append(file_path)
                    
                    # Скачиваем медиа
                    await message.download_media(file_path)
                    
                    if os.path.getsize(file_path) == 0:
                        continue
                    
                    # Создаем FSInputFile
                    fs_file = FSInputFile(file_path)
                    
                    # Определяем тип медиа
                    from telethon.tl.types import (
                        MessageMediaPhoto,
                        MessageMediaDocument,
                        DocumentAttributeVideo,
                        DocumentAttributeAnimated
                    )
                    
                    media = message.media
                    
                    # Текст только для первого медиа
                    media_caption = header if i == 0 else None
                    
                    if isinstance(media, MessageMediaPhoto):
                        # Фото
                        if i == 0 and media_caption:
                            media_group.append(
                                InputMediaPhoto(
                                    media=fs_file,
                                    caption=media_caption,
                                    parse_mode="HTML" if not is_to_admin or is_to_admin else "HTML"
                                )
                            )
                        else:
                            media_group.append(InputMediaPhoto(media=fs_file))
                    
                    elif isinstance(media, MessageMediaDocument):
                        # Проверяем тип
                        is_video = False
                        is_animated = False
                        
                        if hasattr(media, 'document') and hasattr(media.document, 'attributes'):
                            for attr in media.document.attributes:
                                if isinstance(attr, DocumentAttributeVideo):
                                    is_video = True
                                elif isinstance(attr, DocumentAttributeAnimated):
                                    is_animated = True
                        
                        if is_video:
                            # Видео
                            if i == 0 and media_caption:
                                media_group.append(
                                    InputMediaVideo(
                                        media=fs_file,
                                        caption=media_caption,
                                        parse_mode="HTML" if not is_to_admin or is_to_admin else "HTML"
                                    )
                                )
                            else:
                                media_group.append(InputMediaVideo(media=fs_file))
                        else:
                            # Документ или GIF
                            if i == 0 and media_caption:
                                media_group.append(
                                    InputMediaDocument(
                                        media=fs_file,
                                        caption=media_caption,
                                        parse_mode="HTML" if not is_to_admin or is_to_admin else "HTML"
                                    )
                                )
                            else:
                                media_group.append(InputMediaDocument(media=fs_file))
                
                # Отправляем медиагруппу
                if media_group:
                    await self.bot.send_media_group(
                        chat_id=chat_id,
                        media=media_group
                    )
                    
                    # Для админа отправляем кнопки отдельным сообщением
                    if is_to_admin and keyboard:
                        control_message = (
                            f"📸 <b>Управление альбомом</b> ({len(media_group)} медиа)\n\n"
                            f"Для работы с альбомом используйте кнопки ниже:"
                        )
                        
                        await self.bot.send_message(
                            chat_id=chat_id,
                            text=control_message,
                            parse_mode="HTML",
                            reply_markup=keyboard
                        )
                        
                        # Сохраняем пост
                        self.pending_posts[full_callback_id] = post_data
                        logger.info(f"Альбом из {source_channel} ({len(media_group)} медиа) отправлен админу")
            
            finally:
                # Удаляем временные файлы
                for file_path in temp_files:
                    try:
                        if os.path.exists(file_path):
                            os.unlink(file_path)
                    except:
                        pass
                    
        except Exception as e:
            logger.error(f"Ошибка отправки альбома: {e}")
            # Fallback: отправляем только текст
            if is_to_admin:
                await self.send_text_to_admin(post_data)
            else:
                await self.send_text_to_channel(post_data)

    async def _send_media_group_smart(self, messages: List, chat_id: int, caption: str, reply_markup=None):
        """Умная отправка медиагруппы с правильным использованием InputFile"""
        try:
            from aiogram.types import (
                InputMediaPhoto,
                InputMediaVideo,
                InputMediaDocument,
                FSInputFile  # Добавьте этот импорт
            )
            import tempfile
            import os
            import asyncio
            
            # Шаг 1: Загружаем все файлы и получаем их file_id
            uploaded_files = []
            temp_files = []
            
            try:
                for i, message in enumerate(messages):
                    logger.info(f"Обработка медиа {i+1}/{len(messages)}...")
                    
                    # Создаем временный файл
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                        file_path = tmp_file.name
                        temp_files.append(file_path)
                    
                    try:
                        # Скачиваем медиа с таймаутом
                        download_task = asyncio.create_task(message.download_media(file_path))
                        try:
                            await asyncio.wait_for(download_task, timeout=self.config.download_timeout)
                        except asyncio.TimeoutError:
                            logger.warning(f"Таймаут при скачивании медиа {i+1}, пропускаем")
                            continue
                        
                        file_size = os.path.getsize(file_path)
                        if file_size == 0:
                            logger.warning(f"Медиа {i+1} пустое, пропускаем")
                            continue
                        
                        logger.info(f"Медиа {i+1} скачано, размер: {file_size/1024/1024:.1f} МБ")
                        
                        # Определяем тип медиа
                        from telethon.tl.types import (
                            MessageMediaPhoto,
                            MessageMediaDocument,
                            DocumentAttributeVideo,
                            DocumentAttributeAnimated
                        )
                        
                        media = message.media
                        
                        # Используем FSInputFile для отправки
                        fs_file = FSInputFile(file_path)
                        
                        if isinstance(media, MessageMediaPhoto):
                            # Загружаем фото с таймаутом, используя FSInputFile
                            upload_task = asyncio.create_task(
                                self.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=fs_file,  # Используем FSInputFile
                                    disable_notification=True
                                )
                            )
                            try:
                                photo_msg = await asyncio.wait_for(upload_task, timeout=self.config.upload_timeout)
                                file_id = photo_msg.photo[-1].file_id
                                file_type = 'photo'
                                
                                # Сохраняем информацию о сообщении для удаления
                                uploaded_files.append({
                                    'file_id': file_id,
                                    'file_type': file_type,
                                    'message_id': photo_msg.message_id,
                                    'index': i
                                })
                                
                            except asyncio.TimeoutError:
                                logger.warning(f"Таймаут при загрузке фото {i+1}, пропускаем")
                                continue
                            
                        elif isinstance(media, MessageMediaDocument):
                            # Проверяем тип
                            is_video = False
                            is_animated = False
                            
                            if hasattr(media, 'document') and hasattr(media.document, 'attributes'):
                                for attr in media.document.attributes:
                                    if isinstance(attr, DocumentAttributeVideo):
                                        is_video = True
                                    elif isinstance(attr, DocumentAttributeAnimated):
                                        is_animated = True
                            
                            if is_video:
                                # Загружаем видео с таймаутом, используя FSInputFile
                                upload_task = asyncio.create_task(
                                    self.bot.send_video(
                                        chat_id=chat_id,
                                        video=fs_file,  # Используем FSInputFile
                                        disable_notification=True
                                    )
                                )
                                try:
                                    video_msg = await asyncio.wait_for(upload_task, timeout=self.config.upload_timeout)
                                    file_id = video_msg.video.file_id
                                    file_type = 'video'
                                    
                                    uploaded_files.append({
                                        'file_id': file_id,
                                        'file_type': file_type,
                                        'message_id': video_msg.message_id,
                                        'index': i
                                    })
                                    
                                except asyncio.TimeoutError:
                                    logger.warning(f"Таймаут при загрузке видео {i+1}, пропускаем")
                                    continue
                                
                            elif is_animated:
                                # Загружаем анимацию с таймаутом, используя FSInputFile
                                upload_task = asyncio.create_task(
                                    self.bot.send_animation(
                                        chat_id=chat_id,
                                        animation=fs_file,  # Используем FSInputFile
                                        disable_notification=True
                                    )
                                )
                                try:
                                    animation_msg = await asyncio.wait_for(upload_task, timeout=self.config.upload_timeout)
                                    file_id = animation_msg.animation.file_id
                                    file_type = 'animation'
                                    
                                    uploaded_files.append({
                                        'file_id': file_id,
                                        'file_type': file_type,
                                        'message_id': animation_msg.message_id,
                                        'index': i
                                    })
                                    
                                except asyncio.TimeoutError:
                                    logger.warning(f"Таймаут при загрузке анимации {i+1}, пропускаем")
                                    continue
                                
                            else:
                                # Загружаем документ с таймаутом, используя FSInputFile
                                upload_task = asyncio.create_task(
                                    self.bot.send_document(
                                        chat_id=chat_id,
                                        document=fs_file,  # Используем FSInputFile
                                        disable_notification=True
                                    )
                                )
                                try:
                                    doc_msg = await asyncio.wait_for(upload_task, timeout=self.config.upload_timeout)
                                    file_id = doc_msg.document.file_id
                                    file_type = 'document'
                                    
                                    uploaded_files.append({
                                        'file_id': file_id,
                                        'file_type': file_type,
                                        'message_id': doc_msg.message_id,
                                        'index': i
                                    })
                                    
                                except asyncio.TimeoutError:
                                    logger.warning(f"Таймаут при загрузке документа {i+1}, пропускаем")
                                    continue
                    
                    except Exception as e:
                        logger.error(f"Ошибка при обработке медиа {i+1}: {e}")
                        continue
                
                # Шаг 2: Создаем медиагруппу из file_id
                if not uploaded_files:
                    logger.warning("Не удалось загрузить ни одного медиа")
                    return
                
                media_group = []
                uploaded_files.sort(key=lambda x: x['index'])  # Сортируем по порядку
                
                for i, uploaded_file in enumerate(uploaded_files):
                    media_caption = caption if i == 0 else None
                    
                    if uploaded_file['file_type'] == 'photo':
                        media_group.append(
                            InputMediaPhoto(
                                media=uploaded_file['file_id'],
                                caption=media_caption,
                                parse_mode="HTML" if media_caption else None
                            )
                        )
                    elif uploaded_file['file_type'] == 'video':
                        media_group.append(
                            InputMediaVideo(
                                media=uploaded_file['file_id'],
                                caption=media_caption,
                                parse_mode="HTML" if media_caption else None
                            )
                        )
                    elif uploaded_file['file_type'] == 'animation':
                        media_group.append(
                            InputMediaDocument(
                                media=uploaded_file['file_id'],
                                caption=media_caption,
                                parse_mode="HTML" if media_caption else None
                            )
                        )
                    else:
                        media_group.append(
                            InputMediaDocument(
                                media=uploaded_file['file_id'],
                                caption=media_caption,
                                parse_mode="HTML" if media_caption else None
                            )
                        )
                
                # Шаг 3: Отправляем медиагруппу с увеличенным таймаутом
                if media_group:
                    logger.info(f"Отправка медиагруппы из {len(media_group)} файлов...")
                    
                    send_task = asyncio.create_task(
                        self.bot.send_media_group(
                            chat_id=chat_id,
                            media=media_group
                        )
                    )
                    
                    try:
                        await asyncio.wait_for(send_task, timeout=self.config.media_group_timeout)
                        logger.info("Медиагруппа успешно отправлена")
                        
                    except asyncio.TimeoutError:
                        logger.warning("Таймаут при отправке медиагруппы")
                        # Пробуем отправить по одному как fallback
                        await self._send_media_fallback_from_file_ids(uploaded_files, chat_id, caption)
                    
                    # Удаляем временные сообщения
                    await self._cleanup_temp_messages(uploaded_files, chat_id)
                    
                    # Добавляем кнопки управления если нужно
                    if reply_markup:
                        album_count = len(uploaded_files)
                        control_message = (
                            f"📸 <b>Управление альбомом</b> ({album_count} медиа)\n\n"
                            f"Для работы с альбомом используйте кнопки ниже:"
                        )
                        
                        send_msg_task = asyncio.create_task(
                            self.bot.send_message(
                                chat_id=chat_id,
                                text=control_message,
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                        )
                        
                        try:
                            await asyncio.wait_for(send_msg_task, timeout=30)
                        except asyncio.TimeoutError:
                            logger.warning("Таймаут при отправке сообщения с кнопками")
            
            finally:
                # Удаляем временные файлы
                await self._cleanup_temp_files(temp_files)
                
        except Exception as e:
            logger.error(f"Ошибка умной отправки медиагруппы: {e}", exc_info=True)
            raise
    
    async def _cleanup_temp_messages(self, uploaded_files: List[Dict], chat_id: int):
        """Очистка временных сообщений"""
        for uploaded_file in uploaded_files:
            if 'message_id' in uploaded_file:
                try:
                    await self.bot.delete_message(chat_id, uploaded_file['message_id'])
                except Exception as e:
                    logger.debug(f"Не удалось удалить временное сообщение: {e}")
    
    async def _cleanup_temp_files(self, temp_files: List[str]):
        """Очистка временных файлов"""
        for file_path in temp_files:
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
            except Exception as e:
                logger.debug(f"Не удалось удалить временный файл {file_path}: {e}")
    
    async def _send_media_fallback_from_file_ids(self, uploaded_files: List[Dict], chat_id: int, caption: str):
        """Fallback отправка из file_id по одному"""
        try:
            for i, uploaded_file in enumerate(uploaded_files):
                media_caption = caption if i == 0 else None
                
                if uploaded_file['file_type'] == 'photo':
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=uploaded_file['file_id'],
                        caption=media_caption,
                        parse_mode="HTML" if media_caption else None
                    )
                elif uploaded_file['file_type'] == 'video':
                    await self.bot.send_video(
                        chat_id=chat_id,
                        video=uploaded_file['file_id'],
                        caption=media_caption,
                        parse_mode="HTML" if media_caption else None
                    )
                elif uploaded_file['file_type'] == 'animation':
                    await self.bot.send_animation(
                        chat_id=chat_id,
                        animation=uploaded_file['file_id'],
                        caption=media_caption,
                        parse_mode="HTML" if media_caption else None
                    )
                else:
                    await self.bot.send_document(
                        chat_id=chat_id,
                        document=uploaded_file['file_id'],
                        caption=media_caption,
                        parse_mode="HTML" if media_caption else None
                    )
                
                # Пауза между отправками
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Ошибка fallback отправки: {e}")

    async def _send_media_group_progressive(self, messages: List, chat_id: int, caption: str, reply_markup=None):
        """Прогрессивная отправка медиагруппы с индикацией прогресса"""
        try:
            from aiogram.types import (
                InputMediaPhoto,
                InputMediaVideo,
                InputMediaDocument
            )
            import tempfile
            import os
            import asyncio
            
            # Отправляем сообщение о начале обработки
            status_msg = None
            try:
                if len(messages) > 3:  # Только для больших альбомов
                    status_msg = await self.bot.send_message(
                        chat_id=chat_id,
                        text=f"⏳ Начинаю обработку альбома из {len(messages)} медиа...",
                        parse_mode="HTML"
                    )
            except:
                pass
            
            uploaded_files = []
            temp_files = []
            total_size = 0
            
            try:
                for i, message in enumerate(messages):
                    # Обновляем статус
                    if status_msg and i % 3 == 0:  # Обновляем каждые 3 файла
                        try:
                            await status_msg.edit_text(
                                f"⏳ Обрабатываю медиа {i+1}/{len(messages)}..."
                            )
                        except:
                            pass
                    
                    logger.info(f"[{i+1}/{len(messages)}] Обработка медиа...")
                    
                    # Создаем временный файл
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                        file_path = tmp_file.name
                        temp_files.append(file_path)
                    
                    try:
                        # Скачиваем с прогрессивным таймаутом
                        start_time = asyncio.get_event_loop().time()
                        await message.download_media(file_path)
                        download_time = asyncio.get_event_loop().time() - start_time
                        
                        file_size = os.path.getsize(file_path)
                        if file_size == 0:
                            continue
                        
                        total_size += file_size
                        logger.info(f"[{i+1}/{len(messages)}] Скачано: {file_size/1024/1024:.1f} МБ за {download_time:.1f} сек")
                        
                        # ... остальная логика загрузки как в _send_media_group_smart ...
                        # (копируйте код из метода выше)
                        
                    except asyncio.TimeoutError:
                        logger.warning(f"Таймаут при обработке медиа {i+1}, пропускаем")
                        continue
                    except Exception as e:
                        logger.error(f"Ошибка при обработке медиа {i+1}: {e}")
                        continue
                
                # Обновляем финальный статус
                if status_msg:
                    try:
                        await status_msg.edit_text(
                            f"✅ Обработано {len(uploaded_files)}/{len(messages)} медиа\n"
                            f"📊 Общий размер: {total_size/1024/1024:.1f} МБ\n"
                            f"⏳ Отправляю альбом..."
                        )
                    except:
                        pass
                
                # Отправляем медиагруппу
                # ... код отправки медиагруппы ...
                
                # Удаляем статус сообщение
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                    
            except Exception as e:
                logger.error(f"Ошибка при прогрессивной отправке: {e}")
                if status_msg:
                    try:
                        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")
                    except:
                        pass
                raise
                
            finally:
                await self._cleanup_temp_files(temp_files)
                
        except Exception as e:
            logger.error(f"Ошибка прогрессивной отправки медиагруппы: {e}")
            raise

    async def _send_media_to_channel_internal(self, message, caption: str, target_channel: str):
        """Отправка медиа в канал"""
        try:
            from aiogram.types import FSInputFile
            from aiogram.enums import ParseMode  # Добавьте этот импорт
            import tempfile
            import os
            
            media = message.media
            
            if not media:
                await self.send_with_proper_formatting(
                    chat_id=target_channel,
                    text=caption,
                    disable_web_page_preview=True
                )
                return
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                file_path = tmp_file.name
            
            try:
                await message.download_media(file_path)
                
                if os.path.getsize(file_path) == 0:
                    raise Exception("Файл пустой")
                
                from telethon.tl.types import (
                    MessageMediaPhoto,
                    MessageMediaDocument,
                    DocumentAttributeVideo,
                    DocumentAttributeAnimated
                )
                
                fs_file = FSInputFile(file_path)
                
                # Убедитесь, что caption уже в HTML формате
                html_caption = caption if caption else None
                
                if isinstance(media, MessageMediaPhoto):
                    await self.bot.send_photo(
                        chat_id=target_channel,
                        photo=fs_file,
                        caption=html_caption,
                        parse_mode=ParseMode.HTML if html_caption else None
                    )
                    
                elif isinstance(media, MessageMediaDocument):
                    is_video = False
                    is_animated = False
                    
                    if hasattr(media, 'document') and hasattr(media.document, 'attributes'):
                        for attr in media.document.attributes:
                            if isinstance(attr, DocumentAttributeVideo):
                                is_video = True
                            elif isinstance(attr, DocumentAttributeAnimated):
                                is_animated = True
                    
                    if is_video:
                        await self.bot.send_video(
                            chat_id=target_channel,
                            video=fs_file,
                            caption=html_caption,
                            parse_mode=ParseMode.HTML if html_caption else None
                        )
                    elif is_animated:
                        await self.bot.send_animation(
                            chat_id=target_channel,
                            animation=fs_file,
                            caption=html_caption,
                            parse_mode=ParseMode.HTML if html_caption else None
                        )
                    else:
                        await self.bot.send_document(
                            chat_id=target_channel,
                            document=fs_file,
                            caption=html_caption,
                            parse_mode=ParseMode.HTML if html_caption else None
                        )
                else:
                    await self.bot.send_document(
                        chat_id=target_channel,
                        document=fs_file,
                        caption=html_caption,
                        parse_mode=ParseMode.HTML if html_caption else None
                    )
                        
            finally:
                try:
                    os.unlink(file_path)
                except:
                    pass
                
        except Exception as e:
            logger.error(f"Ошибка отправки медиа в канал: {e}", exc_info=True)
            # Fallback: отправляем только текст
            await self.send_with_proper_formatting(
                chat_id=target_channel,
                text=caption,
                disable_web_page_preview=True
            )

    async def send_to_channel(self, post_data: Dict[str, Any]):
        """Отправка поста в целевой канал (главный метод-роутер)"""
        try:
            source_channel = post_data.get('source_channel', 'Неизвестный источник')
            target_channel = post_data.get('target_channel', 'Неизвестный получатель')
            
            logger.info(f"Отправка поста из {source_channel} в {target_channel}")
            
            # Определяем тип поста и отправляем соответствующему методу
            if post_data.get('is_album', False):
                # Это альбом (несколько медиа)
                album_count = post_data.get('album_count', 1)
                logger.info(f"Отправка альбома ({album_count} медиа) в канал {target_channel}")
                await self.send_album_to_channel(post_data)
            elif post_data.get('has_media', False):
                # Это одиночный пост с медиа
                logger.info(f"Отправка одиночного поста с медиа в канал {target_channel}")
                await self.send_media_to_channel(post_data)
            else:
                # Это текстовый пост без медиа
                logger.info(f"Отправка текстового поста в канал {target_channel}")
                await self.send_text_to_channel(post_data)
            
        except Exception as e:
            logger.error(f"Ошибка отправки в канал {post_data.get('target_channel')}: {e}")
            # В случае ошибки пробуем отправить хотя бы текстовую версию
            try:
                text = post_data.get('modified_text', '')
                if text:
                    await self.bot.send_message(
                        chat_id=post_data.get('target_channel'),
                        text=text[:4000],  # Ограничение Telegram
                        disable_web_page_preview=True
                    )
            except:
                pass
            raise

    async def send_album_to_admin(self, post_data: Dict[str, Any]):
        """Отправка альбома админу на одобрение"""
        try:
            album_count = post_data.get('album_count', 1)
            logger.info(f"Отправка альбома админу: {album_count} медиа")
            
            # Используем простой надежный метод
            await self.send_album_simple_reliable(post_data, is_to_admin=True)
            
        except Exception as e:
            logger.error(f"Ошибка отправки альбома админу: {e}")
            raise

    async def _send_with_retry(self, coro, max_retries=3, timeout=30):
        """Отправка с повторными попытками"""
        for attempt in range(max_retries):
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except (asyncio.TimeoutError, Exception) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Экспоненциальная задержка
                    logger.warning(f"Попытка {attempt + 1} не удалась, жду {wait_time} сек: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    raise

    async def _send_media_group(self, messages: List, chat_id: int, caption: str, reply_markup=None):
        """Отправка группы медиа (альбома) с использованием InputFile"""
        try:
            from aiogram.types import (
                InputMediaPhoto,
                InputMediaVideo,
                InputMediaDocument,
                FSInputFile
            )
            import tempfile
            import os
            
            media_group = []
            temp_files = []
            
            try:
                for i, message in enumerate(messages):
                    # Создаем временный файл
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                        file_path = tmp_file.name
                        temp_files.append(file_path)
                    
                    # Скачиваем медиа
                    await message.download_media(file_path)
                    
                    if os.path.getsize(file_path) == 0:
                        logger.warning(f"Медиа {i+1} пустое, пропускаем")
                        continue
                    
                    # Создаем FSInputFile
                    fs_file = FSInputFile(file_path)
                    
                    # Определяем тип медиа
                    from telethon.tl.types import (
                        MessageMediaPhoto,
                        MessageMediaDocument,
                        DocumentAttributeVideo,
                        DocumentAttributeAnimated
                    )
                    
                    media = message.media
                    
                    # Текст только для первого медиа
                    media_caption = caption if i == 0 else None
                    
                    if isinstance(media, MessageMediaPhoto):
                        # Фото - используем FSInputFile
                        if i == 0 and media_caption:
                            media_group.append(
                                InputMediaPhoto(
                                    media=fs_file,
                                    caption=media_caption,
                                    parse_mode="HTML"
                                )
                            )
                        else:
                            media_group.append(
                                InputMediaPhoto(media=fs_file)
                            )
                    
                    elif isinstance(media, MessageMediaDocument):
                        # Проверяем, видео ли это или GIF
                        is_video = False
                        is_animated = False
                        
                        if hasattr(media, 'document') and hasattr(media.document, 'attributes'):
                            for attr in media.document.attributes:
                                if isinstance(attr, DocumentAttributeVideo):
                                    is_video = True
                                elif isinstance(attr, DocumentAttributeAnimated):
                                    is_animated = True
                        
                        if is_video:
                            # Видео
                            if i == 0 and media_caption:
                                media_group.append(
                                    InputMediaVideo(
                                        media=fs_file,
                                        caption=media_caption,
                                        parse_mode="HTML"
                                    )
                                )
                            else:
                                media_group.append(InputMediaVideo(media=fs_file))
                        else:
                            # Документ или GIF
                            if i == 0 and media_caption:
                                media_group.append(
                                    InputMediaDocument(
                                        media=fs_file,
                                        caption=media_caption,
                                        parse_mode="HTML"
                                    )
                                )
                            else:
                                media_group.append(InputMediaDocument(media=fs_file))
                
                # Отправляем медиагруппу
                if media_group:
                    await self.bot.send_media_group(
                        chat_id=chat_id,
                        media=media_group
                    )
                    
                    # Если есть reply_markup, отправляем отдельное сообщение с кнопками
                    if reply_markup:
                        album_count = len(messages)
                        control_message = (
                            f"📸 <b>Управление альбомом</b> ({album_count} медиа)\n\n"
                            f"Для работы с альбомом используйте кнопки ниже:"
                        )
                        
                        await self.bot.send_message(
                            chat_id=chat_id,
                            text=control_message,
                            parse_mode="HTML",
                            reply_markup=reply_markup
                        )
            
            finally:
                # Удаляем временные файлы
                for file_path in temp_files:
                    try:
                        os.unlink(file_path)
                    except:
                        pass
                
        except Exception as e:
            logger.error(f"Ошибка отправки медиагруппы: {e}", exc_info=True)
            raise
    
    async def send_album_to_channel(self, post_data: Dict[str, Any]):
        """Отправка альбома в целевой канал"""
        try:
            album_count = post_data.get('album_count', 1)
            logger.info(f"Отправка альбома в канал: {album_count} медиа")
            
            # Используем простой надежный метод
            await self.send_album_simple_reliable(post_data, is_to_admin=False)
            
        except Exception as e:
            logger.error(f"Ошибка отправки альбома в канал: {e}")
            raise
    
    async def _send_media_group_to_channel(self, messages: List, caption: str, target_channel: str):
        """Отправка медиагруппы в канал"""
        try:
            from aiogram.types import (
                InputMediaPhoto,
                InputMediaVideo,
                InputMediaDocument,
                FSInputFile
            )
            import tempfile
            import os
            
            media_group = []
            temp_files = []
            
            try:
                for i, message in enumerate(messages):
                    # Создаем временный файл
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                        file_path = tmp_file.name
                        temp_files.append(file_path)
                    
                    # Скачиваем медиа
                    await message.download_media(file_path)
                    
                    if os.path.getsize(file_path) == 0:
                        logger.warning(f"Медиа {i+1} пустое, пропускаем")
                        continue
                    
                    # Создаем FSInputFile
                    fs_file = FSInputFile(file_path)
                    
                    # Определяем тип медиа
                    from telethon.tl.types import (
                        MessageMediaPhoto,
                        MessageMediaDocument,
                        DocumentAttributeVideo,
                        DocumentAttributeAnimated
                    )
                    
                    media = message.media
                    
                    # Текст только для первого медиа
                    media_caption = caption if i == 0 else None
                    
                    if isinstance(media, MessageMediaPhoto):
                        # Фото
                        if i == 0 and media_caption:
                            media_group.append(
                                InputMediaPhoto(
                                    media=fs_file,
                                    caption=media_caption,
                                    parse_mode="HTML"
                                )
                            )
                        else:
                            media_group.append(
                                InputMediaPhoto(media=fs_file)
                            )
                    
                    elif isinstance(media, MessageMediaDocument):
                        # Проверяем, видео ли это или GIF
                        is_video = False
                        is_animated = False
                        
                        if hasattr(media, 'document') and hasattr(media.document, 'attributes'):
                            for attr in media.document.attributes:
                                if isinstance(attr, DocumentAttributeVideo):
                                    is_video = True
                                elif isinstance(attr, DocumentAttributeAnimated):
                                    is_animated = True
                        
                        if is_video:
                            # Видео
                            if i == 0 and media_caption:
                                media_group.append(
                                    InputMediaVideo(
                                        media=fs_file,
                                        caption=media_caption,
                                        parse_mode="HTML"
                                    )
                                )
                            else:
                                media_group.append(InputMediaVideo(media=fs_file))
                        else:
                            # Документ или GIF
                            if i == 0 and media_caption:
                                media_group.append(
                                    InputMediaDocument(
                                        media=fs_file,
                                        caption=media_caption,
                                        parse_mode="HTML"
                                    )
                                )
                            else:
                                media_group.append(InputMediaDocument(media=fs_file))
                
                # Отправляем медиагруппу
                if media_group:
                    await self.bot.send_media_group(
                        chat_id=target_channel,
                        media=media_group
                    )
            
            finally:
                # Удаляем временные файлы
                for file_path in temp_files:
                    try:
                        os.unlink(file_path)
                    except:
                        pass
                
        except Exception as e:
            logger.error(f"Ошибка отправки медиагруппы в канал: {e}", exc_info=True)
            # Fallback: отправляем только текст
            await self.bot.send_message(
                chat_id=target_channel,
                text=caption,
                disable_web_page_preview=True
            )

class AIService:
    """Сервис для работы с нейросетями"""
    
    def __init__(self, config: Config):
        self.config = config
    
    def _validate_ai_response(self, text: str, original_text: str) -> tuple[bool, str]:
        """Проверка ответа от нейросети"""
        try:
            # Проверка на пустой ответ
            if not text or text.strip() == "":
                return False, "Нейросеть вернула пустой ответ"
            
            # Если оригинальный текст был пустым (только медиа), пропускаем проверку длины
            if not original_text or original_text == "[Сообщение содержит медиа-контент]":
                return True, "OK"  # Для постов только с медиа всегда OK
            
            # Проверка длины (не должен быть слишком коротким или слишком длинным)
            original_length = len(original_text)
            response_length = len(text)
            
            # Проверяем, что ответ не слишком короткий (менее 60% от оригинала)
            if response_length < original_length * 0.6:
                return False, f"Ответ слишком короткий: {response_length} символов (оригинал: {original_length})"
            
            # Проверяем, что ответ не слишком длинный (более 140% от оригинала)
            if response_length > original_length * 1.4:
                return False, f"Ответ слишком длинный: {response_length} символов (оригинал: {original_length})"
            
            # Проверка на наличие специфических сообщений об ошибках
            error_patterns = [
                "как искусственный интеллект",
                "как AI",
                "не могу",
                "не способен",
                "ошибка",
                "error",
                "извините",
                "sorry",
                "apologize",
                "я не могу",
                "i cannot",
                "i'm unable"
            ]
            
            text_lower = text.lower()
            for pattern in error_patterns:
                if pattern in text_lower:
                    return False, f"В ответе обнаружена ошибка нейросети: '{pattern}'"
            
            # Проверка на повторение исходного текста
            if text == original_text:
                return False, "Ответ совпадает с исходным текстом"
            
            # Проверка на минимальное изменение
            import difflib
            similarity = difflib.SequenceMatcher(None, text, original_text).ratio()
            if similarity > 0.9:  # Если более 90% похожести
                return False, f"Ответ слишком похож на оригинал (сходство: {similarity:.2f})"
            
            return True, "OK"
            
        except Exception as e:
            logger.error(f"Ошибка при валидации ответа нейросети: {e}")
            return False, f"Ошибка валидации: {str(e)}"
    
    async def rewrite_text(self, text: str) -> str:
        """Переписывание текста с сохранением смысла"""
        try:
            # Если текст пустой или это заглушка для медиа
            if not text or text == "[Сообщение содержит медиа-контент]":
                return text
            
            if self.config.ai_provider == 'openai':
                return await self._rewrite_with_openai(text)
            else:
                return await self._simple_rewrite(text)
                
        except Exception as e:
            logger.error(f"Ошибка переписывания текста: {e}")
            return text
    
    async def _rewrite_with_openai(self, text: str) -> str:
        """Использование OpenAI API"""
        try:
            import openai

            client = openai.OpenAI(
                api_key=self.config.openai_api_key,
                base_url="https://api.intelligence.io.solutions/api/v1/",
            )
            
            # Счетчик попыток
            max_attempts = 3
            attempt = 0
            
            while attempt < max_attempts:
                attempt += 1
                logger.info(f"Попытка переписать текст #{attempt}")
                
                response = client.chat.completions.create(
                    model="meta-llama/Llama-3.3-70B-Instruct",
                    messages=[
                        {"role": "system", "content": "Ты - профессиональный редактор."},
                        {"role": "user", "content": "Перепиши текст, сохраняя основной смысл, "
                                        "но меняя формулировки, структуру предложений и стиль. "
                                        "Сделай текст более уникальным и интересным. "
                                        "Все ключевые факты исходного текста должны присутствовать."
                                        "Обьем текста не должен быть больше чем на 140% от оригинального текста."
                                        "Обьем текста не должен быть меньше 60% оригинального текста."
                                        "Не добавляй комментарии, просто верни переписанный текст."
                                        "Используй Markdown разметку для форматирования: **жирный текст**, *курсив*."
                                        "Также ты должен удалять рекламу из исходного текста например: \"[👉 Топор Live. Подписаться](https://t.me/+n0B2XbLYjbMwMDEy)\"."
                                        "Текст:"
                                        f"{text}"},
                    ],
                    temperature=0.7 + (attempt * 0.1),  # Увеличиваем температуру с каждой попыткой
                    stream=False,
                    max_completion_tokens=500
                )
                
                result_text = response.choices[0].message.content
                
                # Валидируем ответ
                is_valid, message = self._validate_ai_response(result_text, text)
                
                if is_valid:
                    logger.info(f"Текст успешно переписан за {attempt} попытку")
                    return result_text
                else:
                    logger.warning(f"Некорректный ответ от нейросети (попытка {attempt}): {message}")
                    if attempt < max_attempts:
                        logger.info("Пробую снова...")
                        continue
                    else:
                        logger.error(f"Не удалось получить корректный ответ после {max_attempts} попыток")
                        return text  # Возвращаем оригинал в случае неудачи
            
            return text
            
        except Exception as e:
            logger.error(f"Ошибка OpenAI API: {e}")
            return text
    
    async def _simple_rewrite(self, text: str) -> str:
        """Простое переписывание без API"""
        # Простая замена некоторых слов (можно расширить)
        replacements = {
            'новость': 'информация',
            'сообщает': 'информирует',
            'сказал': 'отметил',
            'заявил': 'сообщил',
            'объявил': 'проинформировал',
            'очень': 'достаточно',
            'большой': 'крупный',
            'маленький': 'небольшой',
            'хороший': 'качественный',
            'плохой': 'неудовлетворительный',
        }
        
        for word, replacement in replacements.items():
            text = text.replace(word, replacement)
        
        return text

class PostParser:
    """Парсер постов из каналов"""
    
    def __init__(self, user_client, config: Config):
        self.client = user_client
        self.config = config
        self.ai_service = AIService(config)
        self.processed_posts = self._load_processed_posts()
        self.regeneration_history = {}
        self.channel_cache = {}  # Кэш информации о каналах
        
        # Очищаем дубликаты при инициализации
        self.cleanup_duplicate_entries()
        
    async def init_channel_cache(self):
        """Инициализация кэша информации о каналах"""
        logger.info("Инициализация кэша информации о каналах...")
        
        for pair in self.config.channel_pairs:
            source_channel = pair['source']
            try:
                if isinstance(source_channel, int) or (isinstance(source_channel, str) and source_channel.startswith('-100')):
                    # Получаем информацию о канале
                    entity = await self.client.get_entity(source_channel)
                    self.channel_cache[str(source_channel)] = entity
                    logger.info(f"Загружена информация о канале {source_channel}: username={getattr(entity, 'username', 'нет')}")
            except Exception as e:
                logger.warning(f"Не удалось получить информацию о канале {source_channel}: {e}")
        
    def _load_processed_posts(self) -> Dict[str, set]:
        """Загрузка обработанных постов из файла"""
        try:
            if os.path.exists(self.config.processed_posts_file):
                with open(self.config.processed_posts_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    # Загружаем историю перегенераций, если есть
                    if 'regeneration_history' in data:
                        self.regeneration_history = data['regeneration_history']

                    # Загружаем обработанные посты
                    processed_data = {}
                    for channel, posts in data.get('processed_posts', {}).items():
                        # ДЕЛАЕМ ПРЕОБРАЗОВАНИЕ В МНОЖЕСТВО
                        if isinstance(posts, list):
                            processed_data[str(channel)] = set(posts)
                        elif isinstance(posts, str):
                            # Если это строка (старый формат), создаем множество с одним элементом
                            processed_data[str(channel)] = {posts}
                        else:
                            processed_data[str(channel)] = set()
                    return processed_data
        except Exception as e:
            logger.error(f"Ошибка загрузки обработанных постов: {e}")
        return {}
    
    def _save_processed_posts(self):
        """Сохранение обработанных постов в файл"""
        try:
            # Преобразуем множества в списки для сохранения в JSON
            processed_to_save = {}
            for channel, posts_set in self.processed_posts.items():
                # Фильтруем None и пустые значения
                if posts_set:
                    processed_to_save[str(channel)] = list(posts_set)

            data = {
                'processed_posts': processed_to_save,
                'regeneration_history': self.regeneration_history,
                'last_updated': datetime.now().isoformat()
            }

            with open(self.config.processed_posts_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug(f"Сохранено {sum(len(posts) for posts in processed_to_save.values())} обработанных постов")
        except Exception as e:
            logger.error(f"Ошибка сохранения обработанных постов: {e}")
    
    def _mark_as_processed(self, post_data: Dict[str, Any]):
        """Пометить пост как обработанный"""
        source_channel = str(post_data['source_channel'])

        # Для альбомов сохраняем ID первого сообщения
        if post_data.get('is_album', False) and 'album_ids' in post_data:
            post_id = f"{source_channel}_{post_data['album_ids'][0]}"  # ID первого медиа
        else:
            post_id = f"{source_channel}_{post_data['message_id']}"

        # Инициализируем множество для канала, если его нет
        if source_channel not in self.processed_posts:
            self.processed_posts[source_channel] = set()

        # Добавляем пост ID в множество
        self.processed_posts[source_channel].add(post_id)

        # Сохраняем только уникальные записи
        self._save_processed_posts()
    
    def _is_processed(self, post_data: Dict[str, Any]) -> bool:
        """Проверка, был ли пост уже обработан"""
        source_channel = str(post_data['source_channel'])

        # Для альбомов проверяем по ID первого сообщения
        if post_data.get('is_album', False) and 'album_ids' in post_data:
            post_id = f"{source_channel}_{post_data['album_ids'][0]}"
        else:
            post_id = f"{source_channel}_{post_data['message_id']}"

        if source_channel not in self.processed_posts:
            return False

        # Проверяем наличие в множестве
        is_processed = post_id in self.processed_posts[source_channel]
        if is_processed:
            logger.debug(f"Пост {post_id} уже был обработан ранее")
        return is_processed
    
    async def get_latest_post(self, source_channel: str) -> Optional[Dict[str, Any]]:
        """Получение последнего поста из указанного канала"""
        try:
            target_channel = self.config.get_target_channel(source_channel)
            if not target_channel:
                logger.error(f"Не найден целевой канал для {source_channel}")
                return None
            
            # Преобразуем источник в правильный формат
            if isinstance(source_channel, str):
                # Это username (начинается с @) или уже число в строке
                if source_channel.startswith('-100'):
                    source_entity = int(source_channel)
                else:
                    source_entity = source_channel
            else:
                # Это уже число (int)
                source_entity = source_channel
            
            # Получаем несколько последних сообщений для поиска альбомов
            messages = await self.client.get_messages(
                source_entity, 
                limit=10  # Берем 10, чтобы найти начало альбома
            )
            
            if not messages:
                logger.warning(f"Нет сообщений в канале {source_channel}")
                return None
            
            # Начинаем с самого последнего сообщения
            latest_message = messages[0]
            logger.debug(f"Проверка последнего сообщения из {source_channel}: ID={latest_message.id}")
            
            # Проверяем, является ли это сообщение частью альбома
            album_messages = await self._check_for_album(latest_message, messages)
            
            if album_messages:
                # Ограничиваем количество медиа в альбоме
                MAX_MEDIA_IN_ALBUM = 10  # Максимум 10 медиа в одном альбоме
                if len(album_messages) > MAX_MEDIA_IN_ALBUM:
                    logger.warning(f"Альбом слишком большой ({len(album_messages)} медиа), "
                                  f"ограничиваем до {MAX_MEDIA_IN_ALBUM}")
                    album_messages = album_messages[:MAX_MEDIA_IN_ALBUM]
                
                # Это альбом из нескольких медиа
                return await self._process_album_post(album_messages, source_channel, target_channel)
            else:
                # Одиночный пост
                return await self._process_single_post(latest_message, source_channel, target_channel)
            
        except Exception as e:
            logger.error(f"Ошибка получения поста из {source_channel}: {e}", exc_info=True)
            return None
    
    async def _check_for_album(self, latest_message, all_messages) -> List:
        """Проверяем, является ли сообщение частью альбома"""
        try:
            # Если у сообщения есть grouped_id, это часть альбома
            if hasattr(latest_message, 'grouped_id') and latest_message.grouped_id:
                logger.info(f"Найден альбом (grouped_id: {latest_message.grouped_id})")
                
                # Собираем все сообщения с тем же grouped_id
                album_messages = []
                for msg in all_messages:
                    if hasattr(msg, 'grouped_id') and msg.grouped_id == latest_message.grouped_id:
                        album_messages.append(msg)
                
                # Сортируем по ID (в порядке отправки)
                album_messages.sort(key=lambda x: x.id)
                
                logger.info(f"В альбоме найдено {len(album_messages)} медиа")
                return album_messages
            
            return []
            
        except Exception as e:
            logger.error(f"Ошибка проверки альбома: {e}")
            return []
    
    async def _process_album_post(self, album_messages: List, source_channel: str, target_channel: str) -> Optional[Dict[str, Any]]:
        """Обработка поста-альбома (несколько медиа)"""
        try:
            # Берем первое сообщение альбома (там обычно текст)
            first_message = album_messages[0]

            # Получаем текст из первого сообщения
            message_text = ""
            if first_message.text:
                message_text = first_message.text
            elif first_message.photo and hasattr(first_message, 'caption') and first_message.caption:
                message_text = first_message.caption
            elif first_message.video and hasattr(first_message, 'caption') and first_message.caption:
                message_text = first_message.caption
            elif first_message.document and hasattr(first_message, 'caption') and first_message.caption:
                message_text = first_message.caption

            # Пропускаем альбомы без текста
            if not message_text.strip():
                logger.debug(f"Альбом {first_message.id} пропущен: нет текста")
                return None

            # ФОРМИРОВАНИЕ ПРАВИЛЬНОЙ ССЫЛКИ ДЛЯ АЛЬБОМА
            original_link = await self._get_telegram_link(source_channel, first_message.id)

            logger.info(f"Найден альбом в {source_channel}: {len(album_messages)} медиа, текст: {message_text[:100]}")

            post_data = {
                'id': f"{source_channel}_{first_message.id}_{int(datetime.now().timestamp())}",
                'original_text': message_text,
                'original_link': original_link,
                'date': first_message.date,
                'message_id': first_message.id,
                'album_ids': [msg.id for msg in album_messages],
                'source_channel': source_channel,
                'target_channel': target_channel,
                'pair_name': self.config.get_pair_name(source_channel) or f"{source_channel} -> {target_channel}",
                'media': album_messages,
                'has_media': True,
                'is_album': True,
                'album_count': len(album_messages),
                'messages': album_messages,
                'text_source': 'text' if first_message.text else 'caption'
            }

            if self._is_processed(post_data):
                logger.info(f"Альбом {post_data['message_id']} в {source_channel} уже был обработан ранее")
                return None

            logger.info(f"Альбом {post_data['message_id']} из {source_channel} выбран для обработки")
            return post_data

        except Exception as e:
            logger.error(f"Ошибка обработки альбома: {e}")
            return None
    
    async def _process_single_post(self, message, source_channel: str, target_channel: str) -> Optional[Dict[str, Any]]:
        """Обработка одиночного поста"""
        try:
            # Получаем текст
            message_text = ""
            if message.text:
                message_text = message.text
            elif message.photo and hasattr(message, 'caption') and message.caption:
                message_text = message.caption
            elif message.video and hasattr(message, 'caption') and message.caption:
                message_text = message.caption
            elif message.document and hasattr(message, 'caption') and message.caption:
                message_text = message.caption

            # Пропускаем сообщения без текста и медиа
            if not message_text and not message.media:
                logger.debug(f"Сообщение {message.id} пропущено: нет текста и медиа")
                return None

            # Для постов только с медиа без текста - пропускаем
            if not message_text.strip():
                logger.debug(f"Сообщение {message.id} пропущено: нет текста (только медиа)")
                return None

            # ФОРМИРОВАНИЕ ПРАВИЛЬНОЙ ССЫЛКИ
            original_link = await self._get_telegram_link(source_channel, message.id)

            logger.info(f"Найден одиночный пост в {source_channel}: ID={message.id}, текст: {message_text[:100]}, ссылка: {original_link}")

            post_data = {
                'id': f"{source_channel}_{message.id}_{int(datetime.now().timestamp())}",
                'original_text': message_text,
                'original_link': original_link,
                'date': message.date,
                'message_id': message.id,
                'source_channel': source_channel,
                'target_channel': target_channel,
                'pair_name': self.config.get_pair_name(source_channel) or f"{source_channel} -> {target_channel}",
                'media': message.media,
                'has_media': message.media is not None,
                'is_album': False,
                'album_count': 1,
                'message': message,
                'messages': [message],  # Для единообразия тоже список
                'text_source': 'text' if message.text else 'caption'
            }

            if self._is_processed(post_data):
                logger.info(f"Пост {post_data['message_id']} в {source_channel} уже был обработан ранее")
                return None

            logger.info(f"Пост {post_data['message_id']} из {source_channel} выбран для обработки")
            return post_data

        except Exception as e:
            logger.error(f"Ошибка обработки одиночного поста: {e}")
            return None
    
    async def _get_telegram_link(self, channel_id, message_id: int) -> str:
        """Получение ссылки на сообщение в Telegram"""
        try:
            channel_id_str = str(channel_id)

            # 1. Проверяем, есть ли username в конфигурации
            username_from_config = self.config.get_channel_username(channel_id)
            if username_from_config:
                logger.info(f"Используем username из конфигурации для канала {channel_id}: @{username_from_config}")
                return f"https://t.me/{username_from_config}/{message_id}"

            # 2. Если это числовой ID (начинается с -100)
            if channel_id_str.startswith('-100'):
                try:
                    # Пробуем получить информацию о канале
                    entity = await self.client.get_entity(int(channel_id))

                    # Проверяем несколько возможных атрибутов для username
                    username = None

                    if hasattr(entity, 'username') and entity.username:
                        username = entity.username
                    elif hasattr(entity, 'usernames') and entity.usernames:
                        # Иногда username хранится в списке
                        for un in entity.usernames:
                            if hasattr(un, 'username') and un.username:
                                username = un.username
                                break
                            
                    if username:
                        logger.info(f"Найден username канала {channel_id}: @{username}")
                        return f"https://t.me/{username}/{message_id}"
                    else:
                        # Если username не найден, используем формат c/
                        clean_id = channel_id_str.replace('-100', '')
                        logger.warning(f"Username не найден для канала {channel_id}, используем c/ формат")
                        return f"https://t.me/c/{clean_id}/{message_id}"

                except Exception as e:
                    logger.warning(f"Не удалось получить информацию о канале {channel_id}: {e}")
                    clean_id = channel_id_str.replace('-100', '')
                    return f"https://t.me/c/{clean_id}/{message_id}"

            # 3. Если это username (начинается с @ или содержит буквы)
            elif '@' in channel_id_str or any(c.isalpha() for c in channel_id_str):
                username = channel_id_str.lstrip('@')
                return f"https://t.me/{username}/{message_id}"

            # 4. Для других случаев
            else:
                return f"https://t.me/c/{channel_id_str}/{message_id}"

        except Exception as e:
            logger.error(f"Ошибка формирования ссылки для {channel_id}: {e}")
            return f"Сообщение #{message_id} из канала ID: {channel_id}"
    
    async def process_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Обработка и переписывание поста"""
        try:
            original_text = post_data['original_text']
            source_channel = post_data['source_channel']
            has_only_media = post_data.get('has_only_media', False)
            
            if not original_text or original_text.strip() == "":
                logger.warning(f"Пост из {source_channel} не содержит текста, пропускаем")
                return None
        
            # Если пост только с медиа без текста
            if has_only_media:
                logger.info(f"Пост из {source_channel} содержит только медиа без текста")
                
                # Создаем описание для медиа
                media_types = {
                    'photo': 'фото',
                    'video': 'видео',
                    'document': 'документ',
                    'animation': 'анимацию'
                }
                
                media_type = 'медиа'
                if hasattr(post_data['media'], '__class__'):
                    media_class = post_data['media'].__class__.__name__
                    if 'Photo' in media_class:
                        media_type = 'фото'
                    elif 'Video' in media_class:
                        media_type = 'видео'
                    elif 'Document' in media_class:
                        media_type = 'документ'
                    elif 'Animation' in media_class:
                        media_type = 'анимацию'
                
                # Создаем текст для медиа
                modified_text = f"**{media_type.capitalize()} из {source_channel}**\n\nИнтересный визуальный контент для публикации."
                
                post_data['modified_text'] = modified_text
                post_data['processed_at'] = datetime.now().isoformat()
                post_data['regeneration_count'] = 0
                post_data['original_hash'] = hash("media_only")
                post_data['is_media_only'] = True
                
            else:
                # Обычная обработка текста
                logger.info(f"Переписывание текста из {source_channel} длиной {len(original_text)} символов...")
                
                # Переписывание текста
                modified_text = await self.ai_service.rewrite_text(original_text)
                
                # Проверяем, отличается ли текст от оригинала
                if modified_text == original_text:
                    logger.warning("Текст не был изменен нейросетью")
                
                post_data['modified_text'] = modified_text
                post_data['processed_at'] = datetime.now().isoformat()
                post_data['regeneration_count'] = 0
                post_data['original_hash'] = hash(original_text)
                post_data['is_media_only'] = False
            
            # Помечаем пост как обработанный
            self._mark_as_processed(post_data)
            
            logger.info(f"Пост обработан. Новый текст: {len(post_data['modified_text'])} символов")
            
            return post_data
            
        except Exception as e:
            logger.error(f"Ошибка обработки поста: {e}")
            return post_data
    
    async def regenerate_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Перегенерация текста поста"""
        try:
            # Лимит на перегенерации
            MAX_REGENERATIONS = 5
            current_count = post_data.get('regeneration_count', 0)
            
            if current_count >= MAX_REGENERATIONS:
                raise Exception(f"Достигнут лимит перегенераций ({MAX_REGENERATIONS})")
            
            original_text = post_data['original_text']
            source_channel = post_data['source_channel']
            regeneration_count = current_count + 1
            
            logger.info(f"Перегенерация текста из {source_channel} (попытка #{regeneration_count}/{MAX_REGENERATIONS})...")
            
            # Переписывание текста снова
            modified_text = await self.ai_service.rewrite_text(original_text)
            
            post_data['modified_text'] = modified_text
            post_data['regeneration_count'] = regeneration_count
            post_data['last_regenerated'] = datetime.now().isoformat()
            
            # Сохраняем историю перегенераций
            post_key = f"{post_data['source_channel']}_{post_data['message_id']}"
            if post_key not in self.regeneration_history:
                self.regeneration_history[post_key] = []
            
            self.regeneration_history[post_key].append({
                'timestamp': post_data['last_regenerated'],
                'text': modified_text,
                'attempt': regeneration_count
            })
            
            self._save_processed_posts()
            
            logger.info(f"Текст перегенерирован. Попытка #{regeneration_count}")
            
            return post_data
            
        except Exception as e:
            logger.error(f"Ошибка перегенерации поста: {e}")
            raise

    def cleanup_duplicate_entries(self):
        """Очистка дублирующихся записей в processed_posts"""
        try:
            # Удаляем дубликаты в каждом множестве
            for channel in list(self.processed_posts.keys()):
                if self.processed_posts[channel]:
                    # Преобразуем в список, удаляем дубликаты, затем обратно в множество
                    unique_items = set(self.processed_posts[channel])
                    self.processed_posts[channel] = unique_items

            # Удаляем пустые записи
            empty_channels = [channel for channel, posts in self.processed_posts.items() if not posts]
            for channel in empty_channels:
                del self.processed_posts[channel]

            self._save_processed_posts()
            logger.info(f"Очищены дублирующиеся записи. Осталось {len(self.processed_posts)} каналов")
        except Exception as e:
            logger.error(f"Ошибка очистки дублирующихся записей: {e}")

class TelegramReposter:
    """Основной класс приложения"""
    
    def __init__(self):
        self.config = Config()
        self.client_manager = None
        self.post_parser = None
        self.is_running = False
        
    async def init(self):
        """Инициализация приложения"""
        logger.info("Инициализация приложения...")
        
        # Инициализация менеджера клиентов
        self.client_manager = TelegramClientManager(self.config)
        
        # Инициализация пользовательского клиента
        user_client = await self.client_manager.init_user_client()
        
        # Инициализация бота
        await self.client_manager.init_bot_client()
        
        # Инициализация парсера
        self.post_parser = PostParser(user_client, self.config)
        
        # Инициализация кэша каналов
        await self.post_parser.init_channel_cache()
        
        # Проверяем загрузку каналов
        source_channels = self.config.get_source_channels()
        if not source_channels:
            logger.error("Нет каналов для мониторинга! Проверьте файл channel_config.json")
            logger.error("Убедитесь, что файл channel_config.json находится в той же директории")
            logger.error("Или задайте каналы через переменные окружения")
            raise Exception("Не настроены каналы для мониторинга")
        
        logger.info(f"Приложение инициализировано с {len(self.config.channel_pairs)} парами каналов")
        for i, pair in enumerate(self.config.channel_pairs, 1):
            logger.info(f"{i}. {pair['name']}: {str(pair['source'])} -> {str(pair['target'])}")
        
    async def process_channel(self, source_channel: str) -> bool:
        """Обработка одного канала"""
        try:
            logger.info(f"Проверка последнего поста в канале {source_channel}...")

            # Получение последнего поста из канала
            post_data = await self.post_parser.get_latest_post(source_channel)
            if not post_data:
                logger.info(f"В канале {source_channel} нет подходящих постов для обработки")
                return False

            logger.info(f"Найден новый последний пост в {source_channel}: ID={post_data['message_id']}")

            # Проверяем, не обработан ли уже этот пост
            if self.post_parser._is_processed(post_data):
                logger.info(f"Пост {post_data['message_id']} из {source_channel} уже был обработан ранее")
                return False

            # Обработка поста
            processed_post = await self.post_parser.process_post(post_data)
            if not processed_post:
                logger.warning(f"Не удалось обработать пост из {source_channel}")
                return False

            # Отправка админу на одобрение
            await self.client_manager.send_to_admin(processed_post)

            logger.info(f"Последний пост из {source_channel} отправлен на одобрение")
            return True

        except Exception as e:
            logger.error(f"Ошибка в обработке канала {source_channel}: {e}")
            return False
    
    async def run_once(self):
        """Один цикл работы: проверка последних постов во всех каналах"""
        try:
            source_channels = self.config.get_source_channels()
            logger.info(f"⏰ Начало нового цикла проверки {len(source_channels)} каналов...")

            # ИСПРАВЛЕНИЕ: Преобразуем все элементы в строки
            source_channels_str = [str(channel) for channel in source_channels]
            logger.info(f"📡 Каналы для мониторинга: {', '.join(source_channels_str)}")

            processed_count = 0
            skipped_channels = []

            # Обрабатываем каждый канал
            for source_channel in source_channels:
                try:
                    logger.info(f"➡️ Проверка канала: {source_channel}")
                    result = await self.process_channel(source_channel)
                    if result:
                        processed_count += 1
                        logger.info(f"✅ Найден новый пост в {source_channel}")
                    else:
                        skipped_channels.append(source_channel)
                        logger.info(f"⏭️ В {source_channel} нет новых постов")

                    # Небольшая пауза между каналами
                    await asyncio.sleep(1)

                except Exception as channel_error:
                    logger.error(f"❌ Ошибка при проверке канала {source_channel}: {channel_error}")
                    skipped_channels.append(source_channel)
                    continue
                
            # Логирование результатов
            if processed_count > 0:
                logger.info(f"✅ Цикл завершен. Найдено новых постов: {processed_count}")
            else:
                if skipped_channels:
                    # ИСПРАВЛЕНИЕ: Тоже преобразуем в строки
                    skipped_channels_str = [str(channel) for channel in skipped_channels]
                    logger.info(f"📭 В этом цикле не найдено новых постов в каналах: {', '.join(skipped_channels_str)}")
                else:
                    logger.info(f"📭 В этом цикле не найдено новых постов")

            return processed_count > 0

        except Exception as e:
            logger.error(f"❌ Ошибка в цикле обработки: {e}")
            return False
    
    async def run_periodically(self, interval_seconds: int = 300):
        """Запуск периодической обработки"""
        self.is_running = True
        logger.info(f"🔄 Запуск периодической обработки каждые {interval_seconds} секунд (≈{interval_seconds//60} минут)")
        
        # Счетчик циклов
        cycle_count = 0
        
        # Очищаем старые посты из pending_posts каждые 24 часа
        last_cleanup = datetime.now()
        
        while self.is_running:
            try:
                cycle_count += 1
                current_time = datetime.now().strftime("%H:%M:%S")
                logger.info(f"\n{'='*60}")
                logger.info(f"🔄 ЦИКЛ #{cycle_count} | Время: {current_time}")
                logger.info(f"{'='*60}")
                
                # Очистка старых постов (старше 24 часов)
                if (datetime.now() - last_cleanup).seconds > 86400:  # 24 часа
                    logger.info("🧹 Очистка старых ожидающих постов...")
                    await self.cleanup_old_pending_posts()
                    last_cleanup = datetime.now()
                
                # Выполняем обработку
                has_new_posts = await self.run_once()
                
                if has_new_posts:
                    logger.info(f"⏳ Ожидание следующего цикла ({interval_seconds} секунд)...")
                else:
                    logger.info(f"⏳ Нет новых постов. Ожидание следующего цикла ({interval_seconds} секунд)...")
                
                await asyncio.sleep(interval_seconds)
                
            except KeyboardInterrupt:
                logger.info("🛑 Остановка по запросу пользователя")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в основном цикле: {e}")
                logger.info(f"⏳ Повторная попытка через {interval_seconds} секунд...")
                await asyncio.sleep(interval_seconds)
        
        logger.info("👋 Периодическая обработка остановлена")
    
    async def cleanup_old_pending_posts(self):
        """Очистка старых ожидающих постов"""
        try:
            current_time = datetime.now()
            to_remove = []
            
            for post_id, post_data in self.client_manager.pending_posts.items():
                # Проверяем время создания поста (предполагаем, что id содержит timestamp)
                try:
                    parts = post_id.split('_')
                    if len(parts) > 1:
                        post_timestamp = int(parts[-1])
                        post_time = datetime.fromtimestamp(post_timestamp)
                        
                        # Удаляем посты старше 24 часов
                        if (current_time - post_time).seconds > 86400:
                            to_remove.append(post_id)
                except:
                    continue
            
            for post_id in to_remove:
                del self.client_manager.pending_posts[post_id]
                logger.info(f"Удален старый ожидающий пост: {post_id}")
                
        except Exception as e:
            logger.error(f"Ошибка очистки старых постов: {e}")
    
    async def shutdown(self):
        """Корректное завершение работы"""
        self.is_running = False
        logger.info("Завершение работы...")
        
        if hasattr(self.client_manager, 'user_client') and self.client_manager.user_client:
            await self.client_manager.user_client.disconnect()
        
        if hasattr(self.client_manager, 'bot') and self.client_manager.bot:
            await self.client_manager.bot.session.close()


_reposter_instance = None

def get_reposter_instance():
    """Получить экземпляр TelegramReposter (для обработчиков)"""
    global _reposter_instance
    return _reposter_instance

async def main():
    """Основная функция"""
    global _reposter_instance
    
    reposter = TelegramReposter()
    _reposter_instance = reposter  # Сохраняем ссылку
    
    # Инициализируем AI сервис глобально
    get_ai_service_instance(reposter.config)
    
    try:
        # Инициализация
        await reposter.init()
        
        # Запускаем бота и парсинг одновременно
        import threading
        
        # Функция для запуска бота
        async def run_bot():
            logger.info("Запуск бота...")
            await reposter.client_manager.dp.start_polling(
                reposter.client_manager.bot,
                allowed_updates=reposter.client_manager.dp.resolve_used_update_types()
            )
        
        # Запускаем бота в отдельной задаче
        bot_task = asyncio.create_task(run_bot())
        
        # Ждем немного, чтобы бот успел запуститься
        await asyncio.sleep(2)
        
        # Запускаем периодический парсинг
        logger.info("Запуск периодического парсинга...")
        await reposter.run_periodically(interval_seconds=300)  # 5 минут = 300 секунд
        
        # Ждем завершения задач
        await bot_task
        
    except KeyboardInterrupt:
        logger.info("Приложение остановлено")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await reposter.shutdown()


if __name__ == "__main__":
    # Проверка зависимостей
    requirements = [
        "telethon",
        "aiogram==3.0.0",
        "openai",  # опционально
        "groq",    # опционально
        "aiohttp"  # для Yandex GPT
    ]
    
    print("\n" + "="*50)
    print("Telegram Reposter Bot")
    print("="*50)
    print("\nПеред запуском:")
    print("1. Создайте файл .env и добавьте ваши ключи API")
    print("2. Установите зависимости: pip install telethon aiogram==3.0.0")
    print("3. Выберите провайдера AI в .env (groq рекомендуется)")
    print("\nПример .env файла:")
    print("""
USER_API_ID=
USER_API_HASH=
BOT_TOKEN=
SOURCE_CHANNEL=
TARGET_CHANNEL=
ADMIN_ID=
AI_PROVIDER=
OPENAI_API_KEY=
    """)
    print("="*50 + "\n")

    # Запуск приложения
    asyncio.run(main())