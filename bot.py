import os
import pytz
import logging
import asyncio
from datetime import datetime, time, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
)
import openpyxl
from openpyxl import Workbook
import re

# ✅ Устанавливаем часовой пояс
TIMEZONE = pytz.timezone('Europe/Moscow')

def get_current_datetime():
    return datetime.now(TIMEZONE)

def get_current_time():
    return get_current_datetime().time()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для состояний разговора
WAITING_TIME, WAITING_DESCRIPTION, WAITING_REMINDER_TIME = range(3)

# Импорт конфигурации
from config import BOT_TOKEN, EXCEL_FILE, DEFAULT_REMINDER_HOUR, DEFAULT_REMINDER_MINUTE, USER_SETTINGS, WELCOMED_USERS

class ExcelManager:
    def __init__(self, filename: str):
        self.filename = filename
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Создает файл если не существует"""
        try:
            # ✅ Создаем папку если не существует
            directory = os.path.dirname(self.filename)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                print(f"✅ Создана папка: {directory}")

            # ✅ Проверяем, существует ли файл и не поврежден ли он
            if os.path.exists(self.filename):
                try:
                    # Пробуем открыть файл чтобы проверить не поврежден ли он
                    wb = openpyxl.load_workbook(self.filename)
                    print(f"✅ Excel файл существует и не поврежден: {self.filename}")
                    return
                except Exception as e:
                    print(f"⚠️ Файл поврежден, создаем новый: {e}")
                    # Создаем backup поврежденного файла
                    backup_name = f"{self.filename}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    try:
                        os.rename(self.filename, backup_name)
                        print(f"✅ Создан backup поврежденного файла: {backup_name}")
                    except:
                        pass
            
            # Создаем новый файл
            print(f"📁 Создаем новый Excel файл: {self.filename}")
            self._create_new_file()
                
        except Exception as e:
            print(f"❌ Ошибка при создании файла: {e}")
            import traceback
            traceback.print_exc()
            
    def _create_new_file(self):
        """Создает новый Excel файл с правильной структурой"""
        try:
            wb = Workbook()
            # ✅ НЕ удаляем дефолтный лист - оставляем хотя бы один лист
            # Переименовываем дефолтный лист
            default_sheet = wb.active
            default_sheet.title = "default_sheet"
            default_sheet['A1'] = "Информация"
            default_sheet['A2'] = "Этот файл создан Work Tracker Bot"
            default_sheet['A3'] = f"Дата создания: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            
            wb.save(self.filename)
            print(f"✅ Создан новый Excel файл: {self.filename}")
        except Exception as e:
            print(f"❌ Ошибка при создании файла: {e}")
            raise

    def get_user_sheet(self, user_id: int, last_name: str = ""):
        """Возвращает или создает лист для пользователя"""
        try:
            wb = openpyxl.load_workbook(self.filename)
        except Exception as e:
            print(f"❌ Ошибка загрузки файла: {e}")
            # Если файл поврежден, создаем новый
            self._create_new_file()
            wb = openpyxl.load_workbook(self.filename)

        if last_name and last_name.strip():
            sheet_name = ''.join(c for c in last_name.strip() if c.isalnum() or c in ' _-')[:31]
            if not sheet_name:
                sheet_name = f"user_{user_id}"
        else:
            sheet_name = f"user_{user_id}"

        if sheet_name not in wb.sheetnames:
            sheet = wb.create_sheet(sheet_name)
            sheet['A1'] = "Дата"
            sheet['B1'] = "Время работы"
            sheet['C1'] = "Описание работы"
            sheet['D1'] = "Часы работы без обеда"

            sheet.column_dimensions['A'].width = 12
            sheet.column_dimensions['B'].width = 15
            sheet.column_dimensions['C'].width = 50
            sheet.column_dimensions['D'].width = 20

            bold_font = openpyxl.styles.Font(bold=True)
            for cell in ['A1', 'B1', 'C1', 'D1']:
                sheet[cell].font = bold_font

            print(f"✅ Создан новый лист: {sheet_name}")

        try:
            wb.save(self.filename)
        except Exception as e:
            print(f"❌ Ошибка сохранения файла: {e}")
            # Пробуем сохранить с новым именем
            backup_name = f"{self.filename}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                wb.save(backup_name)
                print(f"✅ Создан backup файл: {backup_name}")
            except:
                pass
            # Создаем новый основной файл
            self._create_new_file()
            return self.get_user_sheet(user_id, last_name)

        return sheet_name

    def calculate_work_hours(self, time_range: str):
        try:
            time_range_clean = re.sub(r'[с\-\–\—]', ' ', time_range).strip()
            times = re.findall(r'(\d{1,2}:\d{2}|\d{1,2})', time_range_clean)
            if len(times) >= 2:
                start_time = times[0]
                end_time = times[1]
                if ':' not in start_time:
                    start_time += ':00'
                if ':' not in end_time:
                    end_time += ':00'
                start = datetime.strptime(start_time, '%H:%M')
                end = datetime.strptime(end_time, '%H:%M')
                if end < start:
                    end += timedelta(days=1)
                total_hours = (end - start).total_seconds() / 3600
                work_hours = total_hours - 0.5
                result = round(max(work_hours, 0), 2)
                return result
            return 0.0
        except Exception as e:
            print(f"Ошибка вычисления часов: {e}")
            return 0.0

    def add_entry(self, user_id: int, time_range: str, description: str, last_name: str = ""):
        try:
            print(f"🔧 Попытка сохранить запись для user_id: {user_id}")
            print(f"📁 Путь к файлу: {self.filename}")
            print(f"📝 Данные: {time_range}, {description}")
            
            # Загружаем workbook
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            # Находим следующую строку
            row = sheet.max_row + 1
            work_hours = self.calculate_work_hours(time_range)
            current_date = datetime.now().strftime("%d.%m.%Y")
            
            # Записываем данные
            sheet[f'A{row}'] = current_date
            sheet[f'B{row}'] = time_range
            sheet[f'C{row}'] = description
            sheet[f'D{row}'] = work_hours
            
            # Сохраняем файл
            wb.save(self.filename)
            print(f"✅ Запись добавлена для пользователя {user_id}: {work_hours:.2f} ч.")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка при записи в Excel: {e}")
            import traceback
            traceback.print_exc()
            
            # Пробуем восстановить файл
            try:
                print("🔄 Пытаемся восстановить файл...")
                # Удаляем поврежденный файл
                if os.path.exists(self.filename):
                    os.remove(self.filename)
                    print(f"🗑️ Удален поврежденный файл: {self.filename}")
                
                # Создаем новый файл
                self._create_new_file()
                print("✅ Создан новый файл")
                
                # Пробуем снова добавить запись
                return self.add_entry(user_id, time_range, description, last_name)
            except Exception as e2:
                print(f"❌ Критическая ошибка восстановления: {e2}")
                return False

    def get_user_stats(self, user_id: int, last_name: str = ""):
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            return sheet.max_row - 1
        except Exception as e:
            print(f"❌ Ошибка при получении статистики: {e}")
            return 0

excel_manager = ExcelManager(EXCEL_FILE)
user_data_cache = {}

def get_main_menu_keyboard():
    keyboard = [
        ["📝 Отчет", "📊 Статистика"],
        ["⏰ Мое время", "⚙️ Напомнить"],
        ["🔔 Тест напоминания", "📥 Скачать отчет"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")

async def send_welcome_message(update: Update, user):
    welcome_text = (
        "🎉 *ДОБРО ПОЖАЛОВАТЬ!* 🎉\n"
        "🤖 *Я - Work Tracker Bot* 🤖\n\n"
        "*Моя задача:* Помогать тебе вести учет рабочего времени!\n\n"
        "*Как это работает:*\n"
        "• Каждый день я буду напоминать тебе заполнить отчет\n"
        "• Ты указываешь, в какое время работал и что делал\n"
        "• Все данные автоматически сохраняются в Excel таблицу\n"
        "• У каждого сотрудника свой лист в таблице\n\n"
        "*Преимущества:*\n"
        "✅ Всегда актуальная информация о работе\n"
        "✅ Удобный учет времени\n"
        "✅ Автоматическое сохранение\n"
        "✅ Индивидуальные настройки\n\n"
        "Используй кнопки меню ниже для навигации!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    is_new_user = user_id not in WELCOMED_USERS

    if is_new_user:
        await send_welcome_message(update, user)
        WELCOMED_USERS.add(user_id)
        await asyncio.sleep(2)

    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {
            'reminder_time': time(hour=DEFAULT_REMINDER_HOUR, minute=DEFAULT_REMINDER_MINUTE),
            'username': user.username or "",
            'first_name': user.first_name or "",
            'last_name': user.last_name or "",
            'first_seen': datetime.now()
        }

    last_name = user.last_name or user.first_name or ""
    stats = excel_manager.get_user_stats(user_id, last_name)
    reminder_time = USER_SETTINGS[user_id]['reminder_time']

    if is_new_user:
        message_text = f"👋 *Рад познакомиться, {user.first_name}!*\n\n"
    else:
        message_text = f"👋 *С возвращением, {user.first_name}!*\n\n"

    message_text += (
        f"📊 Твоя статистика: *{stats} записей*\n"
        f"⏰ Напоминание установлено на: *{reminder_time.strftime('%H:%M')}*\n\n"
        f"*Используй кнопки меню для управления:*\n\n"
        f"📝 *Отчет* - добавить запись о работе\n"
        f"📊 *Статистика* - посмотреть статистику\n"
        f"⏰ *Мое время* - посмотреть мое время\n"
        f"⚙️ *Напомнить* - изменить время напоминания\n"
        f"🔔 *Тест напоминания* - проверить напоминание\n"
        f"📥 *Скачать отчет* - получить Excel файл"
    )

    await update.message.reply_text(message_text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 Отчет":
        return await report_command(update, context)
    elif text == "📊 Статистика":
        return await stats_command(update, context)
    elif text == "⏰ Мое время":
        return await my_time_command(update, context)
    elif text == "⚙️ Напомнить":
        return await reminder_command(update, context)
    elif text == "🔔 Тест напоминания":
        return await manual_reminder(update, context)
    elif text == "📥 Скачать отчет":
        return await download_file(update, context)
    else:
        await update.message.reply_text("Неизвестная команда. Используй кнопки меню.", reply_markup=get_main_menu_keyboard())

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *Заполним отчет о работе!*\n\n"
        "🕐 *ШАГ 1:* Укажи ВРЕМЯ РАБОТЫ, когда ты работал:\n\n"
        "*Примеры:*\n"
        "• 9:00-18:00\n"
        "• с 10 до 19\n"
        "• 14:00-22:30\n"
        "• 8:30-17:45\n\n"
        "*Примечание:* Автоматически вычитается 0.5 часа на обед",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_TIME

async def receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    time_range = update.message.text

    if user_id not in user_data_cache:
        user_data_cache[user_id] = {}
    user_data_cache[user_id]['time_range'] = time_range

    work_hours = excel_manager.calculate_work_hours(time_range)

    await update.message.reply_text(
        f"✅ *Отлично!*\n\n"
        f"⏱️ *Рассчитано часов работы:* {work_hours:.2f} ч. (с учетом обеда)\n\n"
        "📝 *ШАГ 2:* Теперь опиши ОПИСАНИЕ РАБОТЫ - что ты делал:\n\n"
        "*Примеры:*\n"
        "• Разрабатывал новый функционал\n"
        "• Участвовал в совещаниях\n"
        "• Изучал документацию\n"
        "• Исправлял ошибки\n"
        "• Общался с клиентами",
        parse_mode='Markdown'
    )
    return WAITING_DESCRIPTION

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    description = update.message.text
    user = update.message.from_user

    if user_id not in user_data_cache or 'time_range' not in user_data_cache[user_id]:
        await update.message.reply_text("❌ Что-то пошло не так. Давай начнем заново", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    time_range = user_data_cache[user_id]['time_range']
    last_name = user.last_name or user.first_name or ""
    success = excel_manager.add_entry(user_id, time_range, description, last_name)

    if success:
        stats = excel_manager.get_user_stats(user_id, last_name)
        current_date = datetime.now().strftime("%d.%m.%Y")
        work_hours = excel_manager.calculate_work_hours(time_range)

        await update.message.reply_text(
            "🎉 *ОТЛИЧНО! Запись сохранена!*\n\n"
            f"📅 *Дата:* {current_date}\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n"
            f"📊 *Всего записей:* {stats}\n\n"
            "Можешь добавить еще запись через кнопку '📝 Отчет'",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Произошла ошибка при сохранении. Попробуй позже.",
            reply_markup=get_main_menu_keyboard()
        )

    if user_id in user_data_cache:
        del user_data_cache[user_id]

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_data_cache:
        del user_data_cache[user_id]
    await update.message.reply_text("❌ Диалог отменен.", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    stats = excel_manager.get_user_stats(user_id, last_name)

    await update.message.reply_text(
        f"📊 *Твоя статистика:*\n\n"
        f"• *Всего записей:* {stats}\n"
        f"• *Дата последней записи:* {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"Продолжай в том же духе! 💪",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

async def my_time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {
            'reminder_time': time(hour=DEFAULT_REMINDER_HOUR, minute=DEFAULT_REMINDER_MINUTE),
            'first_name': update.message.from_user.first_name or ""
        }
    reminder_time = USER_SETTINGS[user_id]['reminder_time']
    await update.message.reply_text(
        f"⏰ *Твое текущее время напоминания:* {reminder_time.strftime('%H:%M')}\n\n"
        f"Чтобы изменить время, нажми кнопку '⚙️ Напомнить'",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

async def reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏰ *Установи свое индивидуальное время напоминания!*\n\n"
        "Введи время в формате *ЧАСЫ:МИНУТЫ* (24-часовой формат):\n\n"
        "*Примеры:*\n"
        "• 18:00 - в 6 вечера\n"
        "• 09:30 - в 9:30 утра\n"
        "• 17:45 - в 5:45 вечера\n\n"
        "*Введи время:*",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_REMINDER_TIME

async def receive_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    time_pattern = r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$'

    if not re.match(time_pattern, user_input):
        await update.message.reply_text(
            "❌ *Неверный формат времени!*\n\n"
            "Пожалуйста, введи время в формате *ЧАСЫ:МИНУТЫ* (24-часовой формат):\n"
            "• 18:00\n• 09:30\n• 17:45\n\nПопробуй еще раз:",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

    hours, minutes = map(int, user_input.split(':'))

    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {}

    # Сохраняем время напоминания
    reminder_time = time(hour=hours, minute=minutes)
    USER_SETTINGS[user_id]['reminder_time'] = reminder_time
    USER_SETTINGS[user_id]['first_name'] = update.message.from_user.first_name or ""
    USER_SETTINGS[user_id]['last_name'] = update.message.from_user.last_name or ""

    await update.message.reply_text(
        f"✅ *Отлично! Твое время напоминания установлено на {user_input}*\n\n"
        f"Каждый день в это время я буду присылать тебе напоминание заполнить отчет о работе.\n\n"
        f"Ты всегда можешь изменить время через кнопку '⚙️ Напомнить'",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def manual_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # Получаем время напоминания пользователя
    reminder_time_str = "18:00"
    if user_id in USER_SETTINGS and 'reminder_time' in USER_SETTINGS[user_id]:
        reminder_time = USER_SETTINGS[user_id]['reminder_time']
        reminder_time_str = reminder_time.strftime('%H:%M')

    await update.message.reply_text(
        f"🔔 *ТЕСТОВОЕ НАПОМИНАНИЕ!*\n\n"
        f"Привет! Пора заполнить отчет о работе за сегодня.\n\n"
        f"⏰ Твое установленное время: *{reminder_time_str}*\n\n"
        f"Нажми кнопку '📝 Отчет' чтобы добавить запись о работе!",
        parse_mode='Markdown'
    )
    
    await update.message.reply_text(
        "✅ Тестовое напоминание отправлено!",
        reply_markup=get_main_menu_keyboard()
    )

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not os.path.exists(EXCEL_FILE):
            await update.message.reply_text(
                "❌ Файл с отчетами еще не создан. Добавь первую запись через кнопку '📝 Отчет'",
                reply_markup=get_main_menu_keyboard()
            )
            return

        with open(EXCEL_FILE, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename=f"work_reports_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
                caption="📊 *Вот твой файл с отчетами!*\n\n"
                       "Файл содержит все записи о рабочем времени.\n"
                       "Каждый пользователь имеет свой лист в файле.",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
        print(f"✅ Файл отправлен пользователю {update.message.from_user.id}")
    except Exception as e:
        print(f"❌ Ошибка при отправке файла: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при отправке файла. Попробуй позже.",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ *Неизвестная команда.*\n\n"
        "*Используй кнопки меню:*\n"
        "📝 Отчет - добавить запись о работе\n"
        "📊 Статистика - посмотреть статистику\n"
        "⏰ Мое время - посмотреть мое время\n"
        "⚙️ Напомнить - изменить время напоминания\n"
        "🔔 Тест напоминания - проверить напоминание\n"
        "📥 Скачать отчет - получить Excel файл",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

def main():
    print("🚀 Запуск Work Tracker Bot...")
    print("📊 Бот для учета рабочего времени")
    print("💾 Excel файл:", EXCEL_FILE)
    print("⏱️ Расчет часов с точностью до 2 знаков")

    application = Application.builder().token(BOT_TOKEN).build()

    report_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("report", report_command),
            MessageHandler(filters.Regex("^(📝 Отчет)$"), report_command)
        ],
        states={
            WAITING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time)],
            WAITING_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    reminder_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("reminder", reminder_command),
            MessageHandler(filters.Regex("^(⚙️ Напомнить)$"), reminder_command)
        ],
        states={
            WAITING_REMINDER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("my_time", my_time_command))
    application.add_handler(CommandHandler("test_remind", manual_reminder))
    application.add_handler(CommandHandler("download", download_file))

    application.add_handler(MessageHandler(filters.Regex("^(📊 Статистика)$"), stats_command))
    application.add_handler(MessageHandler(filters.Regex("^(⏰ Мое время)$"), my_time_command))
    application.add_handler(MessageHandler(filters.Regex("^(🔔 Тест напоминания)$"), manual_reminder))
    application.add_handler(MessageHandler(filters.Regex("^(📥 Скачать отчет)$"), download_file))

    application.add_handler(report_conv_handler)
    application.add_handler(reminder_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    print("✅ Бот успешно запущен!")
    print("📱 Ожидаем сообщения от пользователей...")
    try:
        application.run_polling()
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
