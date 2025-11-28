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
WAITING_TIME, WAITING_LUNCH_CONFIRMATION, WAITING_DESCRIPTION, WAITING_REMINDER_TIME = range(4)

# Импорт конфигурации
from config import BOT_TOKEN, EXCEL_FILE, DEFAULT_REMINDER_HOUR, DEFAULT_REMINDER_MINUTE, USER_SETTINGS, WELCOMED_USERS

global_app = None

class ExcelManager:
    def __init__(self, filename: str):
        self.filename = filename
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        try:
            directory = os.path.dirname(self.filename)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                print(f"✅ Создана папка: {directory}")
            if not os.path.exists(self.filename):
                wb = Workbook()
                # НЕ удаляем активный лист — иначе файл будет битым!
                wb.save(self.filename)
                print(f"✅ Создан новый Excel файл: {self.filename}")
            else:
                print(f"📁 Excel файл уже существует: {self.filename}")
            if os.path.exists(self.filename):
                file_stats = os.stat(self.filename)
                print(f"📊 Размер файла: {file_stats.st_size} байт")
        except Exception as e:
            print(f"❌ Ошибка при создании файла: {e}")
            import traceback
            traceback.print_exc()

    def get_user_sheet(self, user_id: int, last_name: str = ""):
        try:
            wb = openpyxl.load_workbook(self.filename)
        except Exception as e:
            print(f"Ошибка загрузки файла: {e}")
            self._ensure_file_exists()
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
        wb.save(self.filename)
        return sheet_name

    def calculate_work_hours(self, time_range: str, had_lunch: bool = False):
        """Надёжный парсинг нескольких периодов"""
        try:
            total_seconds = 0
            # Разделяем по запятым
            periods = re.split(r',', time_range)
            for period in periods:
                # Удаляем всё, кроме цифр, двоеточий и пробелов
                clean = re.sub(r'[^\d:\s]', ' ', period).strip()
                if not clean:
                    continue
                # Ищем все временные метки: H, HH, H:MM, HH:MM
                times = re.findall(r'\b(\d{1,2})(?::(\d{2}))?\b', clean)
                if len(times) >= 2:
                    def to_datetime(t):
                        h = int(t[0])
                        m = int(t[1]) if t[1] else 0
                        return datetime.strptime(f"{h:02}:{m:02}", "%H:%M")
                    start = to_datetime(times[0])
                    end = to_datetime(times[1])
                    if end < start:
                        end += timedelta(days=1)
                    total_seconds += (end - start).total_seconds()
            total_hours = total_seconds / 3600
            work_hours = total_hours - (0.5 if had_lunch else 0)
            return round(max(work_hours, 0), 2)
        except Exception as e:
            print(f"Ошибка в calculate_work_hours: {e}")
            return 0.0

    def add_entry(self, user_id: int, time_range: str, description: str, had_lunch: bool, last_name: str = ""):
        try:
            print(f"🔧 Попытка сохранить запись для user_id: {user_id}")
            print(f"📁 Путь к файлу: {self.filename}")
            print(f"📝 Данные: {time_range}, обед: {had_lunch}")

            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]

            current_date = datetime.now().strftime("%d.%m.%Y")
            target_row = None

            # Ищем существующую запись за сегодня (со 2-й строки)
            for row in range(2, sheet.max_row + 1):
                if sheet[f'A{row}'].value == current_date:
                    target_row = row
                    break

            work_hours = self.calculate_work_hours(time_range, had_lunch)

            if target_row:
                # Перезаписываем
                sheet[f'B{target_row}'] = time_range
                sheet[f'C{target_row}'] = description
                sheet[f'D{target_row}'] = work_hours
                print(f"🔄 Обновлена запись за {current_date} (строка {target_row})")
            else:
                # Добавляем новую
                new_row = sheet.max_row + 1
                sheet[f'A{new_row}'] = current_date
                sheet[f'B{new_row}'] = time_range
                sheet[f'C{new_row}'] = description
                sheet[f'D{new_row}'] = work_hours
                print(f"🆕 Добавлена запись за {current_date} (строка {new_row})")

            wb.save(self.filename)
            print(f"✅ Запись сохранена: {work_hours:.2f} ч.")
            return True
        except Exception as e:
            print(f"❌ Ошибка при записи в Excel: {e}")
            import traceback
            traceback.print_exc()
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

def get_yes_no_keyboard():
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---

async def send_welcome_message(update: Update, user):
    welcome_text = (
        "🎉 *ДОБРО ПОЖАЛОВАТЬ!* 🎉\n"
        "🤖 *Я - Work Tracker Bot* 🤖\n"
        "*Моя задача:* Помогать тебе вести учет рабочего времени!\n"
        "*Как это работает:*\n"
        "• Каждый день я буду напоминать тебе заполнить отчет\n"
        "• Ты указываешь, в какое время работал и что делал\n"
        "• Все данные автоматически сохраняются в Excel таблицу\n"
        "• У каждого сотрудника свой лист в таблице\n"
        "*Преимущества:*\n"
        "✅ Всегда актуальная информация о работе\n"
        "✅ Удобный учет времени\n"
        "✅ Автоматическое сохранение\n"
        "✅ Индивидуальные настройки\n"
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
        message_text = f"👋 *Рад познакомиться, {user.first_name}!*\n"
    else:
        message_text = f"👋 *С возвращением, {user.first_name}!*\n"
    message_text += (
        f"📊 Твоя статистика: *{stats} записей*\n"
        f"⏰ Напоминание установлено на: *{reminder_time.strftime('%H:%M')}*\n"
        f"*Используй кнопки меню для управления:*\n"
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
        "📝 *Заполним отчет о работе!*\n"
        "🕐 *ШАГ 1:* Укажи ВРЕМЯ РАБОТЫ (можно несколько периодов):\n"
        "*Примеры:*\n"
        "• 9:00-18:00\n"
        "• 9:00-14:00, 15:00-18:00\n"
        "• с 10 до 12, 14:00-17:30\n"
        "Используй запятую для разделения периодов.\n"
        "*Примечание:* После ввода я уточню, был ли у тебя обед.",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_TIME

async def receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    time_range = update.message.text.strip()
    try:
        if user_id not in user_data_cache:
            user_data_cache[user_id] = {}
        user_data_cache[user_id]['time_range'] = time_range
        total_hours = excel_manager.calculate_work_hours(time_range, had_lunch=False)
        await update.message.reply_text(
            f"✅ *Отлично!*\n"
            f"⏱️ *Общее время работы:* {total_hours:.2f} ч.\n"
            "🍽️ *Был ли у тебя сегодня обед?*\n"
            "(Обед = вычет 0.5 часа)",
            reply_markup=get_yes_no_keyboard()
        )
        return WAITING_LUNCH_CONFIRMATION
    except Exception as e:
        print(f"❌ Ошибка в receive_time: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка. Попробуй ввести время ещё раз.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

async def receive_lunch_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip().lower()
    if text in ["да", "yes", "д"]:
        had_lunch = True
    elif text in ["нет", "no", "н"]:
        had_lunch = False
    else:
        await update.message.reply_text("Пожалуйста, выбери «Да» или «Нет».", reply_markup=get_yes_no_keyboard())
        return WAITING_LUNCH_CONFIRMATION

    if user_id not in user_data_cache:
        user_data_cache[user_id] = {}
    user_data_cache[user_id]['had_lunch'] = had_lunch

    await update.message.reply_text(
        "📝 *ШАГ 2:* Теперь опиши ОПИСАНИЕ РАБОТЫ — что ты делал:\n"
        "*Примеры:*\n"
        "• Разрабатывал новый функционал\n"
        "• Участвовал в совещаниях\n"
        "• Изучал документацию\n"
        "• Исправлял ошибки\n"
        "• Общался с клиентами",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_DESCRIPTION

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    description = update.message.text
    user = update.message.from_user
    cache = user_data_cache.get(user_id, {})
    if 'time_range' not in cache or 'had_lunch' not in cache:
        await update.message.reply_text("❌ Что-то пошло не так. Давай начнем заново", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    time_range = cache['time_range']
    had_lunch = cache['had_lunch']
    last_name = user.last_name or user.first_name or ""

    success = excel_manager.add_entry(user_id, time_range, description, had_lunch, last_name)
    if success:
        stats = excel_manager.get_user_stats(user_id, last_name)
        current_date = datetime.now().strftime("%d.%m.%Y")
        work_hours = excel_manager.calculate_work_hours(time_range, had_lunch)
        await update.message.reply_text(
            "🎉 *ОТЛИЧНО! Запись сохранена!*\n"
            f"📅 *Дата:* {current_date}\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"🍽️ *Обед:* {'Да' if had_lunch else 'Нет'}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n"
            f"📊 *Всего записей:* {stats}\n"
            "❗ Если отправишь отчёт ещё раз сегодня — старая запись будет заменена!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Произошла ошибка при сохранении. Попробуй еще раз",
            reply_markup=get_main_menu_keyboard()
        )
    user_data_cache.pop(user_id, None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data_cache.pop(user_id, None)
    await update.message.reply_text("❌ Диалог отменен.", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

# --- ОСТАЛЬНЫЕ ФУНКЦИИ (stats, my_time, reminder, download, напоминания) ---

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    stats = excel_manager.get_user_stats(user_id, last_name)
    await update.message.reply_text(
        f"📊 *Твоя статистика:*\n"
        f"• *Всего записей:* {stats}\n"
        f"• *Дата последней записи:* {datetime.now().strftime('%d.%m.%Y')}\n"
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
        f"⏰ *Твое текущее время напоминания:* {reminder_time.strftime('%H:%M')}\n"
        f"Чтобы изменить время, нажми кнопку '⚙️ Напомнить'",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

async def reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏰ *Установи свое индивидуальное время напоминания!*\n"
        "Введи время в формате *ЧАСЫ:МИНУТЫ* (24-часовой формат):\n"
        "*Примеры:*\n"
        "• 18:00 - в 6 вечера\n"
        "• 09:30 - в 9:30 утра\n"
        "• 17:45 - в 5:45 вечера\n"
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
            "❌ *Неверный формат времени!*\n"
            "Пожалуйста, введи время в формате *ЧАСЫ:МИНУТЫ* (24-часовой формат):\n"
            "• 18:00\n• 09:30\n• 17:45\nПопробуй еще раз:",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    hours, minutes = map(int, user_input.split(':'))
    if user_id not in USER_SETTINGS:
        USER_SETTINGS[user_id] = {}
    reminder_time = time(hour=hours, minute=minutes)
    USER_SETTINGS[user_id]['reminder_time'] = reminder_time
    USER_SETTINGS[user_id]['first_name'] = update.message.from_user.first_name or ""
    USER_SETTINGS[user_id]['last_name'] = update.message.from_user.last_name or ""

    global global_app
    job_queue = global_app.job_queue
    if job_queue:
        for job in job_queue.get_jobs_by_name(str(user_id)):
            job.schedule_removal()
        job_time = time(hour=hours, minute=minutes, tzinfo=TIMEZONE)
        job_queue.run_daily(
            send_daily_reminder,
            time=job_time,
            days=tuple(range(7)),
            data=user_id,
            name=str(user_id)
        )
        job_queue.run_once(
            send_test_reminder,
            when=60,
            data=user_id,
            name=f"test_{user_id}"
        )
        print(f"✅ Напоминание для {user_id} установлено на {hours:02d}:{minutes:02d}")
    else:
        print("❌ job_queue недоступен — критическая ошибка!")

    await update.message.reply_text(
        f"✅ *Отлично! Твое время напоминания установлено на {user_input}*\n"
        f"Каждый день в это время я буду присылать тебе напоминание.\n"
        f"*Тестовое напоминание придет через 1 минуту* ⏰",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def send_test_reminder(context):
    try:
        user_id = context.job.data
        await context.bot.send_message(
            chat_id=user_id,
            text="🧪 *ТЕСТОВОЕ НАПОМИНАНИЕ!*\n"
                 "Это тестовое сообщение чтобы проверить работу напоминаний.\n"
                 "Если ты видишь это сообщение - значит система напоминаний работает правильно! ✅",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        print(f"✅ Тестовое напоминание отправлено пользователю {user_id}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

async def send_daily_reminder(context):
    try:
        user_id = context.job.data
        reminder_time_str = "18:00"
        if user_id in USER_SETTINGS and 'reminder_time' in USER_SETTINGS[user_id]:
            reminder_time_str = USER_SETTINGS[user_id]['reminder_time'].strftime('%H:%M')
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🕔 *ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ({reminder_time_str})!*\n"
                 f"Привет! Пора заполнить отчет о работе за сегодня.\n"
                 f"Нажми '📝 Отчет' чтобы указать:\n"
                 f"1️⃣ Время работы (можно несколько периодов)\n"
                 f"2️⃣ Был ли обед\n"
                 f"3️⃣ Описание работы",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        print(f"✅ Ежедневное напоминание отправлено пользователю {user_id}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

async def manual_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    original_job = getattr(context, 'job', None)
    class MockJob:
        def __init__(self, data):
            self.data = data
    context.job = MockJob(user_id)
    try:
        await send_daily_reminder(context)
    finally:
        context.job = original_job
    await update.message.reply_text("✅ Тестовое напоминание отправлено!", reply_markup=get_main_menu_keyboard())

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not os.path.exists(EXCEL_FILE):
            await update.message.reply_text("❌ Файл ещё не создан. Добавь первую запись!", reply_markup=get_main_menu_keyboard())
            return
        with open(EXCEL_FILE, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename=f"work_reports_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
                caption="📊 *Твой файл с отчётами!*",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
        print(f"✅ Файл отправлен пользователю {update.message.from_user.id}")
    except Exception as e:
        print(f"❌ Ошибка при отправке: {e}")
        await update.message.reply_text("❌ Ошибка отправки файла.", reply_markup=get_main_menu_keyboard())

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Неизвестная команда. Используй кнопки меню.", reply_markup=get_main_menu_keyboard())

def restore_reminders(application: Application):
    job_queue = application.job_queue
    for user_id, settings in USER_SETTINGS.items():
        if 'reminder_time' in settings:
            for job in job_queue.get_jobs_by_name(str(user_id)):
                job.schedule_removal()
            job_time = time(
                hour=settings['reminder_time'].hour,
                minute=settings['reminder_time'].minute,
                tzinfo=TIMEZONE
            )
            job_queue.run_daily(
                send_daily_reminder,
                time=job_time,
                days=tuple(range(7)),
                data=user_id,
                name=str(user_id)
            )
    print(f"✅ Восстановлены напоминания для {len(USER_SETTINGS)} пользователей.")

def main():
    global global_app
    print("🚀 Запуск Work Tracker Bot...")
    print("✅ Несколько периодов + вопрос про обед + перезапись за день")
    application = Application.builder().token(BOT_TOKEN).build()
    global_app = application

    report_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("report", report_command)],
        states={
            WAITING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time)],
            WAITING_LUNCH_CONFIRMATION: [MessageHandler(filters.Regex("^(Да|Нет)$"), receive_lunch_confirmation)],
            WAITING_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    reminder_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("reminder", reminder_command)],
        states={WAITING_REMINDER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("my_time", my_time_command))
    application.add_handler(CommandHandler("test_remind", manual_reminder))
    application.add_handler(CommandHandler("download", download_file))
    application.add_handler(MessageHandler(filters.Regex("^(📊 Статистика|⏰ Мое время|🔔 Тест напоминания|📥 Скачать отчет|📝 Отчет|⚙️ Напомнить)$"), handle_menu_buttons))
    application.add_handler(report_conv_handler)
    application.add_handler(reminder_conv_handler)
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    restore_reminders(application)
    print("✅ Бот запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()
