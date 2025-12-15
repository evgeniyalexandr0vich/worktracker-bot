import os
import pytz
import logging
import asyncio
import requests
import pandas as pd
from datetime import datetime, time, timedelta
from typing import Optional, Dict, Any, List, Tuple
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
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
(
    WAITING_TIME, WAITING_LUNCH_CONFIRMATION, 
    WAITING_DESCRIPTION, WAITING_REMINDER_TIME,
    WAITING_DATE_SELECTION, WAITING_DATE_EDIT,
    WAITING_EDIT_TIME, WAITING_EDIT_LUNCH,
    WAITING_EDIT_DESCRIPTION
) = range(9)

# Импорт конфигурации
from config import (
    BOT_TOKEN, EXCEL_FILE, DEFAULT_REMINDER_HOUR, 
    DEFAULT_REMINDER_MINUTE, USER_SETTINGS, WELCOMED_USERS, 
    MAX_ENTRIES_PER_DAY, YANDEX_DISK_ENABLED, 
    YANDEX_DISK_TOKEN, YANDEX_DISK_FOLDER, WEEKDAYS_RU,
    USER_EDIT_STATE
)

# ✅ Глобальная ссылка на application для доступа к job_queue
global_app = None

class YandexDiskManager:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://cloud-api.yandex.net/v1/disk/resources"
        self.headers = {
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json"
        }

    def check_folder_exists(self, folder_path: str):
        """Проверяет существование папки на Яндекс.Диске"""
        try:
            url = f"{self.base_url}?path={folder_path}"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                print(f"✅ Папка существует на Яндекс.Диске: {folder_path}")
                return True
            else:
                print(f"❌ Папка не найдена на Яндекс.Диске: {folder_path}")
                print(f"Код ошибки: {response.status_code}")
                print(f"Ответ: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Ошибка проверки папки: {e}")
            return False

    def upload_file(self, local_file_path: str, remote_file_path: str):
        """Загружает файл на Яндекс.Диск в существующую папку"""
        try:
            # Проверяем существование папки
            folder_path = os.path.dirname(remote_file_path)
            if not self.check_folder_exists(folder_path):
                print(f"❌ Папка {folder_path} не существует на Яндекс.Диске")
                print(f"📝 Создайте папку {folder_path} вручную через Яндекс.Диск")
                return False

            # Получаем URL для загрузки
            url = f"{self.base_url}/upload?path={remote_file_path}&overwrite=true"
            response = requests.get(url, headers=self.headers)
            
            if response.status_code != 200:
                print(f"❌ Ошибка получения URL для загрузки: {response.status_code} - {response.text}")
                return False
            
            upload_url = response.json()["href"]
            
            # Загружаем файл
            with open(local_file_path, 'rb') as file:
                upload_response = requests.put(upload_url, files={"file": file})
            
            if upload_response.status_code in [200, 201]:
                print(f"✅ Файл успешно загружен на Яндекс.Диск: {remote_file_path}")
                return True
            else:
                print(f"❌ Ошибка загрузки файла: {upload_response.status_code} - {upload_response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Ошибка при загрузке файла: {e}")
            return False

    def get_file_info(self, file_path: str):
        """Получает информацию о файле на Яндекс.Диске"""
        try:
            url = f"{self.base_url}?path={file_path}"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            else:
                return None
        except Exception as e:
            print(f"❌ Ошибка получения информации о файле: {e}")
            return None

# ✅ Инициализация менеджера Яндекс.Диска
yandex_disk = YandexDiskManager(YANDEX_DISK_TOKEN) if YANDEX_DISK_ENABLED and YANDEX_DISK_TOKEN else None

class ExcelManager:
    def __init__(self, filename: str):
        self.filename = filename
        self._ensure_file_exists()
        
    def _ensure_file_exists(self):
        """Создаёт файл, если не существует."""
        try:
            directory = os.path.dirname(self.filename)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                print(f"✅ Создана локальная папка: {directory}")

            if not os.path.exists(self.filename):
                wb = Workbook()
                # Удаляем стандартный лист
                if "Sheet" in wb.sheetnames:
                    std_sheet = wb["Sheet"]
                    wb.remove(std_sheet)
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
        """Возвращает или создаёт лист для пользователя"""
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
            sheet['B1'] = "День недели"
            sheet['C1'] = "Время работы"
            sheet['D1'] = "Описание работы"
            sheet['E1'] = "Часы работы без обеда"
            sheet['F1'] = "Статус"
            
            # Форматирование заголовков
            bold_font = Font(bold=True)
            header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            white_font = Font(color="FFFFFF", bold=True)
            center_alignment = Alignment(horizontal="center", vertical="center")
            
            for col in ['A', 'B', 'C', 'D', 'E', 'F']:
                cell = sheet[f'{col}1']
                cell.font = white_font
                cell.fill = header_fill
                cell.alignment = center_alignment
                
                # Устанавливаем ширину колонок
                if col == 'A':
                    sheet.column_dimensions['A'].width = 12
                elif col == 'B':
                    sheet.column_dimensions['B'].width = 15
                elif col == 'C':
                    sheet.column_dimensions['C'].width = 25
                elif col == 'D':
                    sheet.column_dimensions['D'].width = 50
                elif col == 'E':
                    sheet.column_dimensions['E'].width = 20
                elif col == 'F':
                    sheet.column_dimensions['F'].width = 15
            
            print(f"✅ Создан новый лист: {sheet_name}")
        
        wb.save(self.filename)
        return sheet_name

    def calculate_work_hours(self, time_range: str, had_lunch: bool = False):
        """Поддерживает несколько периодов, разделённых запятыми."""
        try:
            total_seconds = 0
            periods = re.split(r',\s*', time_range.strip())
            for period in periods:
                if not period:
                    continue
                clean_period = re.sub(r'[с\-\–\—]', ' ', period).strip()
                times = re.findall(r'(\d{1,2}:\d{2}|\d{1,2})', clean_period)
                if len(times) >= 2:
                    start_str = times[0]
                    end_str = times[1]
                    if ':' not in start_str:
                        start_str += ':00'
                    if ':' not in end_str:
                        end_str += ':00'
                    start = datetime.strptime(start_str, '%H:%M')
                    end = datetime.strptime(end_str, '%H:%M')
                    if end < start:
                        end += timedelta(days=1)
                    total_seconds += (end - start).total_seconds()

            total_hours = total_seconds / 3600
            work_hours = total_hours - (0.5 if had_lunch else 0)
            return round(max(work_hours, 0), 2)
        except Exception as e:
            print(f"Ошибка вычисления часов: {e}")
            return 0.0

    def has_today_entry(self, user_id: int, last_name: str = ""):
        """Проверяет, есть ли уже запись за сегодня"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            current_date = datetime.now().strftime("%d.%m.%Y")
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value == current_date:
                    return True
            return False
        except Exception as e:
            print(f"❌ Ошибка при проверке записи за сегодня: {e}")
            return False

    def create_missing_dates(self, user_id: int, last_name: str = ""):
        """Создает записи-заглушки для пропущенных дней"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            # Получаем все существующие даты
            existing_dates = []
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    try:
                        date_obj = datetime.strptime(str(date_cell.value), "%d.%m.%Y")
                        existing_dates.append(date_obj.date())
                    except:
                        continue
            
            if not existing_dates:
                # Если нет записей, начинаем с сегодняшнего дня
                start_date = datetime.now().date()
                # Создаем записи за последние 7 дней
                date_range = []
                for i in range(7):
                    date = start_date - timedelta(days=i)
                    date_range.append(date)
                
                date_range.sort()  # Сортируем от старых к новым
                
                created_count = 0
                for date in date_range:
                    row = sheet.max_row + 1
                    sheet[f'A{row}'] = date.strftime("%d.%m.%Y")
                    sheet[f'B{row}'] = WEEKDAYS_RU[date.weekday()]
                    sheet[f'C{row}'] = "ПРОПУЩЕНО"
                    sheet[f'D{row}'] = "День пропущен"
                    sheet[f'E{row}'] = 0
                    sheet[f'F{row}'] = "ПРОПУЩЕН"
                    
                    # Форматирование для пропущенных дней
                    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    for col in ['A', 'B', 'C', 'D', 'E', 'F']:
                        sheet[f'{col}{row}'].fill = red_fill
                    
                    created_count += 1
                
                if created_count > 0:
                    print(f"✅ Создано {created_count} записей-заглушек для новых пользователей")
                
                wb.save(self.filename)
                return created_count
            else:
                # Находим самую раннюю и самую позднюю дату
                min_date = min(existing_dates)
                max_date = max(existing_dates)
                
                # Добавляем все даты от min_date до сегодня
                today = datetime.now().date()
                
                # Создаем диапазон дат от min_date до today
                date_range = []
                current_date = min_date
                while current_date <= today:
                    date_range.append(current_date)
                    current_date += timedelta(days=1)
                
                # Находим пропущенные даты
                missing_dates = []
                for date in date_range:
                    if date not in existing_dates:
                        missing_dates.append(date)
                
                # Создаем записи-заглушки для пропущенных дат
                created_count = 0
                for date in missing_dates:
                    row = sheet.max_row + 1
                    sheet[f'A{row}'] = date.strftime("%d.%m.%Y")
                    sheet[f'B{row}'] = WEEKDAYS_RU[date.weekday()]
                    sheet[f'C{row}'] = "ПРОПУЩЕНО"
                    sheet[f'D{row}'] = "День пропущен"
                    sheet[f'E{row}'] = 0
                    sheet[f'F{row}'] = "ПРОПУЩЕН"
                    
                    # Форматирование для пропущенных дней
                    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    for col in ['A', 'B', 'C', 'D', 'E', 'F']:
                        sheet[f'{col}{row}'].fill = red_fill
                    
                    created_count += 1
                
                if created_count > 0:
                    print(f"✅ Создано {created_count} записей-заглушек для пропущенных дней")
                
                wb.save(self.filename)
                return created_count
            
        except Exception as e:
            print(f"❌ Ошибка при создании пропущенных дат: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def get_user_records(self, user_id: int, last_name: str = "", days_back: int = 30):
        """Возвращает последние записи пользователя"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            records = []
            cutoff_date = (datetime.now() - timedelta(days=days_back)).date()
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    try:
                        date_obj = datetime.strptime(str(date_cell.value), "%d.%m.%Y")
                        if date_obj.date() >= cutoff_date:
                            record = {
                                'row': row,
                                'date': date_cell.value,
                                'weekday': sheet[f'B{row}'].value,
                                'time_range': sheet[f'C{row}'].value,
                                'description': sheet[f'D{row}'].value,
                                'work_hours': sheet[f'E{row}'].value,
                                'status': sheet[f'F{row}'].value
                            }
                            records.append(record)
                    except:
                        continue
            
            # Сортируем по дате (самые новые сначала)
            records.sort(key=lambda x: datetime.strptime(x['date'], "%d.%m.%Y"), reverse=True)
            return records
            
        except Exception as e:
            print(f"❌ Ошибка при получении записей пользователя: {e}")
            return []

    def add_entry(self, user_id: int, date_str: str, time_range: str, description: str, had_lunch: bool, last_name: str = ""):
        """Добавляет или обновляет запись"""
        try:
            # Создаем пропущенные даты перед добавлением
            self.create_missing_dates(user_id, last_name)
            
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            # Проверяем, есть ли уже запись на эту дату
            target_row = None
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value == date_str:
                    target_row = row
                    break
            
            work_hours = self.calculate_work_hours(time_range, had_lunch)
            
            if target_row:
                # Обновляем существующую запись
                sheet[f'C{target_row}'] = time_range
                sheet[f'D{target_row}'] = description
                sheet[f'E{target_row}'] = work_hours
                sheet[f'F{target_row}'] = "ЗАПОЛНЕН"
                
                # Убираем форматирование пропущенного дня
                no_fill = PatternFill(fill_type=None)
                for col in ['A', 'B', 'C', 'D', 'E', 'F']:
                    sheet[f'{col}{target_row}'].fill = no_fill
                
                action = "обновлена"
            else:
                # Добавляем новую запись
                target_row = sheet.max_row + 1
                date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                sheet[f'A{target_row}'] = date_str
                sheet[f'B{target_row}'] = WEEKDAYS_RU[date_obj.weekday()]
                sheet[f'C{target_row}'] = time_range
                sheet[f'D{target_row}'] = description
                sheet[f'E{target_row}'] = work_hours
                sheet[f'F{target_row}'] = "ЗАПОЛНЕН"
                action = "добавлена"
            
            wb.save(self.filename)
            
            # ✅ Сохраняем на Яндекс.Диск после добавления/обновления записи
            if yandex_disk:
                remote_file_path = f"{YANDEX_DISK_FOLDER}/work_tracker_backup.xlsx"
                if yandex_disk.upload_file(self.filename, remote_file_path):
                    print(f"✅ Резервная копия загружена на Яндекс.Диск")
                else:
                    print(f"⚠️ Не удалось загрузить резервную копию на Яндекс.Диск")
            
            print(f"✅ Запись {action} для пользователя {user_id} на {date_str}: {work_hours:.2f} ч.")
            return True, "success", target_row
            
        except Exception as e:
            print(f"❌ Ошибка при записи в Excel: {e}")
            import traceback
            traceback.print_exc()
            return False, "error", None

    def delete_entry(self, user_id: int, date_str: str, last_name: str = ""):
        """Удаляет запись на конкретную дату"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            deleted_data = None
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value == date_str:
                    deleted_data = {
                        'date': sheet[f'A{row}'].value,
                        'weekday': sheet[f'B{row}'].value,
                        'time_range': sheet[f'C{row}'].value,
                        'description': sheet[f'D{row}'].value,
                        'work_hours': sheet[f'E{row}'].value,
                        'status': sheet[f'F{row}'].value
                    }
                    
                    # Превращаем запись в пропущенную
                    sheet[f'C{row}'] = "ПРОПУЩЕНО"
                    sheet[f'D{row}'] = "День пропущен"
                    sheet[f'E{row}'] = 0
                    sheet[f'F{row}'] = "ПРОПУЩЕН"
                    
                    # Форматирование для пропущенных дней
                    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    for col in ['A', 'B', 'C', 'D', 'E', 'F']:
                        sheet[f'{col}{row}'].fill = red_fill
                    
                    wb.save(self.filename)
                    
                    # ✅ Сохраняем на Яндекс.Диск после удаления записи
                    if yandex_disk:
                        remote_file_path = f"{YANDEX_DISK_FOLDER}/work_tracker_backup.xlsx"
                        if yandex_disk.upload_file(self.filename, remote_file_path):
                            print(f"✅ Резервная копия загружена на Яндекс.Диск после удаления")
                    
                    print(f"✅ Запись на {date_str} помечена как пропущенная для пользователя {user_id}")
                    return True, deleted_data
            
            return False, None
        except Exception as e:
            print(f"❌ Ошибка при удалении записи: {e}")
            return False, None

    def get_user_stats(self, user_id: int, last_name: str = ""):
        """Получает статистику пользователя"""
        try:
            # Создаем пропущенные даты перед подсчетом статистики
            self.create_missing_dates(user_id, last_name)
            
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            total_entries = 0
            filled_entries = 0
            total_hours = 0
            missing_days = 0
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    total_entries += 1
                    status = sheet[f'F{row}'].value
                    work_hours = sheet[f'E{row}'].value or 0
                    
                    if status == "ЗАПОЛНЕН":
                        filled_entries += 1
                        total_hours += float(work_hours)
                    elif status == "ПРОПУЩЕН":
                        missing_days += 1
            
            return {
                'total_entries': total_entries,
                'filled_entries': filled_entries,
                'total_hours': round(total_hours, 2),
                'missing_days': missing_days,
                'completion_rate': round((filled_entries / total_entries * 100) if total_entries > 0 else 0, 1)
            }
        except Exception as e:
            print(f"❌ Ошибка при получении статистики: {e}")
            return {
                'total_entries': 0,
                'filled_entries': 0,
                'total_hours': 0,
                'missing_days': 0,
                'completion_rate': 0
            }

excel_manager = ExcelManager(EXCEL_FILE)

def get_main_menu_keyboard():
    keyboard = [
        ["📝 Отчет", "✏️ Редактировать"],
        ["🗑️ Удалить запись", "📊 Статистика"],
        ["📥 Скачать отчет", "☁️ Синхронизировать"],
        ["⚙️ Напоминание"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")

def get_yes_no_keyboard():
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)

def get_date_selection_keyboard(records):
    """Создает клавиатуру для выбора даты"""
    keyboard = []
    row = []
    
    for i, record in enumerate(records[:10]):  # Показываем последние 10 записей
        date_str = record['date']
        status = "✅" if record['status'] == "ЗАПОЛНЕН" else "❌"
        button_text = f"{date_str} {status}"
        
        row.append(InlineKeyboardButton(button_text, callback_data=f"select_date_{date_str}"))
        
        if len(row) == 2 or i == len(records[:10]) - 1:
            keyboard.append(row)
            row = []
    
    # Добавляем кнопку "Сегодня"
    today_str = datetime.now().strftime("%d.%m.%Y")
    keyboard.append([InlineKeyboardButton(f"📅 Сегодня ({today_str})", callback_data=f"select_date_{today_str}")])
    
    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton("« Назад в меню", callback_data="back_to_menu")])
    
    return InlineKeyboardMarkup(keyboard)

async def send_welcome_message(update: Update, user):
    yandex_status = "✅ ВКЛЮЧЕН" if yandex_disk else "❌ ВЫКЛЮЧЕН"
    yandex_folder_info = f"\n📂 *Папка:* {YANDEX_DISK_FOLDER}" if yandex_disk else ""
    
    welcome_text = (
        "🎉 *РАСШИРЕННЫЙ WORK TRACKER BOT* 🎉\n"
        "🤖 *Новые возможности:*\n"
        "• *Автоматические заглушки* для пропущенных дней\n"
        "• *Редактирование любых записей* (включая прошлые)\n"
        "• *Подробная статистика* с % заполнения\n"
        "• *Визуальное отображение* пропущенных дней\n"
        "• *Поиск и выбор дат* из календаря\n\n"
        "*Как это работает:*\n"
        "1️⃣ Бот автоматически создает записи для всех дней\n"
        "2️⃣ Пропущенные дни отмечаются красным цветом\n"
        "3️⃣ Вы можете заполнить отчет за ЛЮБОЙ день\n"
        "4️⃣ Редактировать можно любую существующую запись\n"
        f"5️⃣ ☁️ *Резервное копирование:* {yandex_status}{yandex_folder_info}\n\n"
        "*Используйте новые кнопки:*\n"
        "✏️ *Редактировать* - заполнить/изменить любой день\n"
        "📊 *Статистика* - увидеть % заполнения и пропуски\n"
        "📝 *Отчет* - быстрая запись за сегодня"
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
    
    # Создаем пропущенные даты при первом старте
    last_name = user.last_name or user.first_name or ""
    excel_manager.create_missing_dates(user_id, last_name)
    
    stats = excel_manager.get_user_stats(user_id, last_name)
    reminder_time = USER_SETTINGS[user_id]['reminder_time']
    has_today_entry = excel_manager.has_today_entry(user_id, last_name)
    
    if is_new_user:
        message_text = f"👋 *Рад познакомиться, {user.first_name}!*\n\n"
    else:
        message_text = f"👋 *С возвращением, {user.first_name}!*\n\n"
    
    # Расширенная статистика
    message_text += (
        f"📊 *ВАША СТАТИСТИКА:*\n"
        f"• 📅 Всего дней в базе: *{stats['total_entries']}*\n"
        f"• ✅ Заполнено дней: *{stats['filled_entries']}*\n"
        f"• ❌ Пропущено дней: *{stats['missing_days']}*\n"
        f"• 📈 Процент заполнения: *{stats['completion_rate']}%*\n"
        f"• ⏱️ Всего часов работы: *{stats['total_hours']} ч.*\n"
        f"• ⏰ Напоминание: *{reminder_time.strftime('%H:%M')}*\n\n"
    )
    
    # Статус сегодняшнего дня
    today_status = "✅ УЖЕ СДЕЛАНА" if has_today_entry else "❌ ЕЩЕ НЕТ"
    message_text += f"📝 *Сегодняшняя запись:* {today_status}\n\n"
    
    yandex_status = "✅ ВКЛЮЧЕНО" if yandex_disk else "❌ ВЫКЛЮЧЕНО"
    message_text += f"☁️ *Резервное копирование:* {yandex_status}"
    
    if yandex_disk:
        message_text += f"\n📂 *Папка на Яндекс.Диске:* {YANDEX_DISK_FOLDER}"
    
    message_text += "\n\n"
    
    # Описание функций
    message_text += (
        f"*Используй кнопки меню для управления:*\n"
        f"📝 *Отчет* - быстрая запись за сегодня\n"
        f"✏️ *Редактировать* - заполнить/изменить ЛЮБОЙ день\n"
        f"🗑️ *Удалить запись* - удалить/сбросить запись\n"
        f"📊 *Статистика* - подробная статистика\n"
        f"📥 *Скачать отчет* - получить Excel файл\n"
        f"☁️ *Синхронизировать* - сохранить в облако\n"
        f"⚙️ *Напоминание* - изменить время напоминания"
    )
    
    await update.message.reply_text(message_text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 Отчет":
        return await report_command(update, context)
    elif text == "✏️ Редактировать":
        return await edit_command(update, context)
    elif text == "🗑️ Удалить запись":
        return await delete_command(update, context)
    elif text == "📊 Статистика":
        return await stats_command(update, context)
    elif text == "⚙️ Напоминание":
        return await reminder_command(update, context)
    elif text == "📥 Скачать отчет":
        return await download_file(update, context)
    elif text == "☁️ Синхронизировать":
        return await sync_to_yandex_disk(update, context)
    else:
        await update.message.reply_text("Неизвестная команда. Используй кнопки меню.", reply_markup=get_main_menu_keyboard())

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса редактирования/заполнения записей"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    # Получаем последние записи пользователя
    records = excel_manager.get_user_records(user_id, last_name, days_back=60)
    
    if not records:
        await update.message.reply_text(
            "📊 *У вас пока нет записей для редактирования.*\n\n"
            "Сначала создайте несколько записей через кнопку '📝 Отчет'.\n"
            "Бот автоматически создаст структуру для всех дней.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    # Сохраняем состояние
    USER_EDIT_STATE[user_id] = {
        'action': 'edit',
        'records': records
    }
    
    keyboard = get_date_selection_keyboard(records)
    
    await update.message.reply_text(
        "📅 *ВЫБЕРИТЕ ДАТУ ДЛЯ РЕДАКТИРОВАНИЯ*\n\n"
        "*Статусы:*\n"
        "✅ - запись заполнена\n"
        "❌ - день пропущен\n\n"
        "Вы можете:\n"
        "• Заполнить пропущенный день\n"
        "• Изменить существующую запись\n"
        "• Выбрать любой день из списка\n\n"
        "*Выберите дату:*",
        parse_mode='Markdown',
        reply_markup=keyboard
    )
    
    return WAITING_DATE_SELECTION

async def date_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора даты из календаря"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "back_to_menu":
        await query.edit_message_text(
            "Возвращаемся в главное меню...",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    if query.data.startswith("select_date_"):
        selected_date = query.data.replace("select_date_", "")
        user = query.from_user
        last_name = user.last_name or user.first_name or ""
        
        # Сохраняем выбранную дату в состоянии
        if user_id not in USER_EDIT_STATE:
            USER_EDIT_STATE[user_id] = {}
        USER_EDIT_STATE[user_id]['selected_date'] = selected_date
        
        # Проверяем, есть ли уже запись на эту дату
        records = excel_manager.get_user_records(user_id, last_name, days_back=365)
        existing_record = None
        
        for record in records:
            if record['date'] == selected_date:
                existing_record = record
                break
        
        if existing_record and existing_record['status'] == "ЗАПОЛНЕН":
            # Показываем существующую запись и предлагаем редактировать
            await query.edit_message_text(
                f"📅 *РЕДАКТИРОВАНИЕ ЗАПИСИ ЗА {selected_date}*\n\n"
                f"*Текущие данные:*\n"
                f"🕐 *Время работы:* {existing_record['time_range']}\n"
                f"📝 *Описание:* {existing_record['description']}\n"
                f"⏱️ *Часы работы:* {existing_record['work_hours']} ч.\n\n"
                "Что вы хотите сделать?",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Изменить запись", callback_data=f"edit_record_{selected_date}")],
                    [InlineKeyboardButton("🗑️ Удалить запись", callback_data=f"delete_record_{selected_date}")],
                    [InlineKeyboardButton("« Назад к выбору даты", callback_data="back_to_date_selection")]
                ])
            )
        else:
            # Предлагаем заполнить пропущенный день
            date_obj = datetime.strptime(selected_date, "%d.%m.%Y")
            weekday = WEEKDAYS_RU[date_obj.weekday()]
            
            await query.edit_message_text(
                f"📅 *ЗАПОЛНЕНИЕ ПРОПУЩЕННОГО ДНЯ*\n"
                f"*Дата:* {selected_date} ({weekday})\n\n"
                "Этот день отмечен как пропущенный.\n"
                "Хотите заполнить отчет за этот день?",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Заполнить отчет", callback_data=f"fill_missing_{selected_date}")],
                    [InlineKeyboardButton("« Назад к выбору даты", callback_data="back_to_date_selection")]
                ])
            )
    
    elif query.data.startswith("fill_missing_") or query.data.startswith("edit_record_"):
        selected_date = query.data.split("_")[2]
        USER_EDIT_STATE[user_id]['selected_date'] = selected_date
        USER_EDIT_STATE[user_id]['step'] = 'waiting_time'
        
        await query.edit_message_text(
            f"📝 *ЗАПОЛНЕНИЕ ОТЧЕТА ЗА {selected_date}*\n\n"
            "🕐 *ШАГ 1:* Укажи ВРЕМЯ РАБОТЫ (можно несколько периодов):\n"
            "*Примеры:*\n"
            "• 9:00-18:00\n"
            "• 9:00-14:00, 15:00-18:00\n"
            "• с 10 до 12, 14:00-17:30\n"
            "Используй запятую для разделения периодов.\n"
            "*Примечание:* После ввода я уточню, был ли у тебя обед.",
            parse_mode='Markdown'
        )
        
        return WAITING_EDIT_TIME
    
    elif query.data.startswith("delete_record_"):
        selected_date = query.data.split("_")[2]
        user = query.from_user
        last_name = user.last_name or user.first_name or ""
        
        success, deleted_data = excel_manager.delete_entry(user_id, selected_date, last_name)
        
        if success:
            await query.edit_message_text(
                f"🗑️ *ЗАПИСЬ УДАЛЕНА!*\n"
                f"*Дата:* {selected_date}\n\n"
                "Запись помечена как пропущенная.\n"
                "Вы можете заполнить ее заново в любое время.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 Выбрать другую дату", callback_data="back_to_date_selection")],
                    [InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")]
                ])
            )
        else:
            await query.edit_message_text(
                "❌ *Не удалось удалить запись.*\n\n"
                "Попробуйте еще раз или обратитесь к администратору.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 Выбрать другую дату", callback_data="back_to_date_selection")],
                    [InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")]
                ])
            )
    
    elif query.data == "back_to_date_selection":
        user = query.from_user
        last_name = user.last_name or user.first_name or ""
        records = excel_manager.get_user_records(user_id, last_name, days_back=60)
        keyboard = get_date_selection_keyboard(records)
        
        await query.edit_message_text(
            "📅 *ВЫБЕРИТЕ ДАТУ ДЛЯ РЕДАКТИРОВАНИЯ*\n\n"
            "*Статусы:*\n"
            "✅ - запись заполнена\n"
            "❌ - день пропущен\n\n"
            "Выберите дату:",
            parse_mode='Markdown',
            reply_markup=keyboard
        )

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрое создание отчета за сегодня"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    today_str = datetime.now().strftime("%d.%m.%Y")
    
    # Проверяем, есть ли уже запись за сегодня
    records = excel_manager.get_user_records(user_id, last_name, days_back=1)
    today_record = None
    
    for record in records:
        if record['date'] == today_str:
            today_record = record
            break
    
    if today_record and today_record['status'] == "ЗАПОЛНЕН":
        await update.message.reply_text(
            f"❌ *Вы уже заполнили отчет за сегодня ({today_str}).*\n\n"
            "Если нужно внести изменения:\n"
            "1. Используйте кнопку '✏️ Редактировать'\n"
            "2. Выберите сегодняшнюю дату\n"
            "3. Отредактируйте существующую запись",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    # Сохраняем состояние для быстрого отчета
    if user_id not in USER_EDIT_STATE:
        USER_EDIT_STATE[user_id] = {}
    USER_EDIT_STATE[user_id]['selected_date'] = today_str
    USER_EDIT_STATE[user_id]['step'] = 'waiting_time'
    USER_EDIT_STATE[user_id]['quick_report'] = True
    
    await update.message.reply_text(
        f"📝 *БЫСТРЫЙ ОТЧЕТ ЗА СЕГОДНЯ ({today_str})*\n\n"
        "🕐 *ШАГ 1:* Укажи ВРЕМЯ РАБОТЫ (можно несколько периодов):\n"
        "*Примеры:*\n"
        "• 9:00-18:00\n"
        "• 9:00-14:00, 15:00-18:00\n"
        "• с 10 до 12, 14:00-17:30\n"
        "Используй запятую для разделения периодов.",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_EDIT_TIME

async def receive_edit_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение времени работы при редактировании"""
    user_id = update.message.from_user.id
    time_range = update.message.text
    
    if user_id not in USER_EDIT_STATE:
        await update.message.reply_text(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    USER_EDIT_STATE[user_id]['time_range'] = time_range
    
    total_hours = excel_manager.calculate_work_hours(time_range, had_lunch=False)
    
    await update.message.reply_text(
        f"✅ *Отлично!*\n"
        f"⏱️ *Общее время работы:* {total_hours:.2f} ч.\n"
        "🍽️ *Был ли у тебя обед в этот день?*\n"
        "(Обед = вычет 0.5 часа)",
        reply_markup=get_yes_no_keyboard()
    )
    return WAITING_EDIT_LUNCH

async def receive_edit_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение информации об обеде при редактировании"""
    user_id = update.message.from_user.id
    text = update.message.text.strip().lower()
    
    if text in ["да", "yes", "д"]:
        had_lunch = True
    elif text in ["нет", "no", "н"]:
        had_lunch = False
    else:
        await update.message.reply_text("Пожалуйста, выбери «Да» или «Нет».", reply_markup=get_yes_no_keyboard())
        return WAITING_EDIT_LUNCH
    
    if user_id not in USER_EDIT_STATE:
        await update.message.reply_text(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    USER_EDIT_STATE[user_id]['had_lunch'] = had_lunch
    
    selected_date = USER_EDIT_STATE[user_id].get('selected_date', datetime.now().strftime("%d.%m.%Y"))
    
    await update.message.reply_text(
        f"📝 *ШАГ 2:* Опиши ОПИСАНИЕ РАБОТЫ за {selected_date}:\n"
        "*Примеры:*\n"
        "• Разрабатывал новый функционал\n"
        "• Участвовал в совещаниях\n"
        "• Изучал документацию\n"
        "• Исправлял ошибки\n"
        "• Общался с клиентами",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_EDIT_DESCRIPTION

async def receive_edit_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение описания работы при редактировании"""
    user_id = update.message.from_user.id
    description = update.message.text
    
    if user_id not in USER_EDIT_STATE:
        await update.message.reply_text(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    if not all(key in USER_EDIT_STATE[user_id] for key in ['selected_date', 'time_range', 'had_lunch']):
        await update.message.reply_text(
            "❌ Недостаточно данных. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    selected_date = USER_EDIT_STATE[user_id]['selected_date']
    time_range = USER_EDIT_STATE[user_id]['time_range']
    had_lunch = USER_EDIT_STATE[user_id]['had_lunch']
    
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    success, result, row_num = excel_manager.add_entry(
        user_id, selected_date, time_range, description, had_lunch, last_name
    )
    
    if success:
        stats = excel_manager.get_user_stats(user_id, last_name)
        work_hours = excel_manager.calculate_work_hours(time_range, had_lunch)
        
        yandex_sync_text = ""
        if yandex_disk:
            yandex_sync_text = "☁️ *Данные автоматически сохранены на Яндекс.Диск*\n"
        
        date_obj = datetime.strptime(selected_date, "%d.%m.%Y")
        weekday = WEEKDAYS_RU[date_obj.weekday()]
        
        message_text = (
            f"🎉 *{'ОТЧЕТ СОХРАНЕН' if USER_EDIT_STATE[user_id].get('quick_report') else 'ЗАПИСЬ ОБНОВЛЕНА'}!*\n"
            f"{yandex_sync_text}\n"
            f"📅 *Дата:* {selected_date} ({weekday})\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"🍽️ *Обед:* {'Да' if had_lunch else 'Нет'}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n\n"
            f"📊 *СТАТИСТИКА:*\n"
            f"• 📅 Всего дней: {stats['total_entries']}\n"
            f"• ✅ Заполнено: {stats['filled_entries']}\n"
            f"• 📈 Заполнение: {stats['completion_rate']}%\n\n"
            "*Вы можете:*\n"
            "• ✏️ *Редактировать* другие дни\n"
            "• 📊 *Посмотреть статистику*\n"
            "• 📥 *Скачать полный отчет*"
        )
        
        await update.message.reply_text(
            message_text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Произошла ошибка при сохранении. Попробуйте еще раз.",
            reply_markup=get_main_menu_keyboard()
        )
    
    # Очищаем состояние
    if user_id in USER_EDIT_STATE:
        del USER_EDIT_STATE[user_id]
    
    return ConversationHandler.END

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление записи за сегодня (для обратной совместимости)"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    today_str = datetime.now().strftime("%d.%m.%Y")
    
    success, deleted_data = excel_manager.delete_entry(user_id, today_str, last_name)
    
    if success:
        await update.message.reply_text(
            f"🗑️ *ЗАПИСЬ ЗА СЕГОДНЯ ({today_str}) УДАЛЕНА!*\n\n"
            "День помечен как пропущенный.\n"
            "Вы можете заполнить его заново через кнопку '📝 Отчет'.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            f"❌ *Не найдено записей за сегодня ({today_str}).*\n\n"
            "Сначала создайте запись через кнопку '📝 Отчет'.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подробная статистика пользователя"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    stats = excel_manager.get_user_stats(user_id, last_name)
    
    # Получаем последние записи
    records = excel_manager.get_user_records(user_id, last_name, days_back=7)
    
    # Формируем сообщение со статистикой
    message_text = (
        f"📊 *ПОДРОБНАЯ СТАТИСТИКА*\n"
        f"👤 *Пользователь:* {user.first_name} {last_name}\n\n"
        f"*ОБЩАЯ СТАТИСТИКА:*\n"
        f"📅 Всего дней в базе: *{stats['total_entries']}*\n"
        f"✅ Заполнено дней: *{stats['filled_entries']}*\n"
        f"❌ Пропущено дней: *{stats['missing_days']}*\n"
        f"📈 Процент заполнения: *{stats['completion_rate']}%*\n"
        f"⏱️ Всего часов работы: *{stats['total_hours']} ч.*\n\n"
    )
    
    # Последние 7 дней
    if records:
        message_text += "*ПОСЛЕДНИЕ 7 ДНЕЙ:*\n"
        for record in records[:7]:
            status_emoji = "✅" if record['status'] == "ЗАПОЛНЕН" else "❌"
            hours_text = f"{record['work_hours']} ч." if record['work_hours'] else "0 ч."
            message_text += f"{status_emoji} {record['date']} ({record['weekday']}): {hours_text}\n"
    
    # Рекомендации
    if stats['completion_rate'] < 50:
        message_text += "\n⚠️ *Рекомендация:* Попробуйте заполнить пропущенные дни через '✏️ Редактировать'"
    elif stats['completion_rate'] < 80:
        message_text += "\n👍 *Хорошая работа!* Продолжайте в том же духе!"
    else:
        message_text += "\n🎉 *Отличный результат!* Вы очень дисциплинированы!"
    
    # Кнопки действий
    keyboard = [
        ["✏️ Заполнить пропуски", "📥 Скачать отчет"],
        ["🏠 В главное меню"]
    ]
    
    await update.message.reply_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# ========== ФУНКЦИИ ДЛЯ НАПОМИНАНИЙ ==========

async def receive_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение нового времени напоминания"""
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    
    # Проверяем формат времени
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
        
        print(f"✅ Напоминание установлено для {user_id} на {hours:02d}:{minutes:02d}")
    else:
        print("❌ job_queue недоступен — критическая ошибка!")

    await update.message.reply_text(
        f"✅ *Отлично! Твое время напоминания установлено на {user_input}*\n"
        f"Каждый день в это время я буду присылать тебе напоминание заполнить отчет о работе.\n"
        f"*Тестовое напоминание придет через 1 минуту* ⏰\n"
        f"Ты всегда можешь изменить время через кнопку '⚙️ Напоминание'",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def send_test_reminder(context):
    """Отправка тестового напоминания"""
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
        print(f"❌ Ошибка при отправке тестового напоминания: {e}")

async def send_daily_reminder(context):
    """Отправка ежедневного напоминания"""
    try:
        user_id = context.job.data
        reminder_time_str = "18:00"
        if user_id in USER_SETTINGS and 'reminder_time' in USER_SETTINGS[user_id]:
            reminder_time_str = USER_SETTINGS[user_id]['reminder_time'].strftime('%H:%M')
        
        user = USER_SETTINGS.get(user_id, {})
        last_name = user.get('last_name', '') or user.get('first_name', '')
        has_today_entry = excel_manager.has_today_entry(user_id, last_name)
        
        if has_today_entry:
            message_text = (
                f"🕔 *ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ({reminder_time_str})!*\n"
                f"Привет! Я вижу, что ты уже заполнил отчет за сегодня. ✅\n\n"
                f"Если нужно что-то исправить:\n"
                f"1️⃣ Нажми '✏️ Редактировать'\n"
                f"2️⃣ Выбери сегодняшнюю дату\n"
                f"3️⃣ Исправь данные"
            )
        else:
            message_text = (
                f"🕔 *ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ({reminder_time_str})!*\n"
                f"Привет! Пора заполнить отчет о работе за сегодня.\n\n"
                f"Нажми кнопку '📝 Отчет' чтобы быстро добавить запись за сегодня.\n\n"
                f"Или используй '✏️ Редактировать' чтобы:\n"
                f"• Заполнить пропущенные дни\n"
                f"• Отредактировать старые записи"
            )
            
        await context.bot.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        print(f"✅ Ежедневное напоминание отправлено пользователю {user_id}")
    except Exception as e:
        print(f"❌ Ошибка при отправке напоминания пользователю {user_id}: {e}")

def restore_reminders(application: Application):
    """Восстановление напоминаний при перезапуске бота"""
    job_queue = application.job_queue
    restored_count = 0
    
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
            
            restored_count += 1
            print(f"🔁 Восстановлено напоминание для {user_id} на {settings['reminder_time'].strftime('%H:%M')}")
    
    print(f"✅ Восстановлено {restored_count} напоминаний.")

# ========== ОСТАЛЬНЫЕ ФУНКЦИИ ==========

async def reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установка времени напоминания"""
    user_id = update.message.from_user.id
    
    # Получаем текущее время напоминания
    current_time = DEFAULT_REMINDER_HOUR, DEFAULT_REMINDER_MINUTE
    if user_id in USER_SETTINGS and 'reminder_time' in USER_SETTINGS[user_id]:
        current_time = (USER_SETTINGS[user_id]['reminder_time'].hour, 
                       USER_SETTINGS[user_id]['reminder_time'].minute)
    
    await update.message.reply_text(
        f"⏰ *НАСТРОЙКА НАПОМИНАНИЙ*\n\n"
        f"Текущее время напоминания: *{current_time[0]:02d}:{current_time[1]:02d}*\n\n"
        "Введи новое время в формате *ЧАСЫ:МИНУТЫ* (24-часовой формат):\n"
        "*Примеры:*\n"
        "• 18:00 - в 6 вечера\n"
        "• 09:30 - в 9:30 утра\n"
        "• 17:45 - в 5:45 вечера\n"
        "*Введи время:*",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_REMINDER_TIME

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скачивание файла"""
    try:
        if not os.path.exists(EXCEL_FILE):
            await update.message.reply_text(
                "❌ Файл с отчетами еще не создан. Добавьте первую запись.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Обновляем все листы перед скачиванием
        wb = openpyxl.load_workbook(EXCEL_FILE)
        for sheet_name in wb.sheetnames:
            # Пытаемся извлечь user_id из имени листа
            if sheet_name.startswith("user_"):
                try:
                    user_id = int(sheet_name.split("_")[1])
                    excel_manager.create_missing_dates(user_id, "")
                except:
                    continue
        
        wb.save(EXCEL_FILE)
        
        yandex_status = ""
        if yandex_disk:
            yandex_status = "\n☁️ *Резервная копия хранится на Яндекс.Диске*"
            
        with open(EXCEL_FILE, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename=f"work_reports_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
                caption=f"📊 *ВОТ ВАШ ФАЙЛ С ОТЧЕТАМИ!*\n"
                       f"Файл содержит:\n"
                       f"• Все записи о рабочем времени\n"
                       f"• Автоматические заглушки для пропущенных дней\n"
                       f"• Цветовую маркировку статусов\n"
                       f"• Индивидуальные листы для каждого пользователя\n"
                       f"{yandex_status}",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
        print(f"✅ Файл отправлен пользователю {update.message.from_user.id}")
    except Exception as e:
        print(f"❌ Ошибка при отправке файла: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при отправке файла. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard()
        )

async def sync_to_yandex_disk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительная синхронизация с Яндекс.Диском"""
    if not yandex_disk:
        await update.message.reply_text(
            "❌ *Синхронизация с Яндекс.Диском отключена.*\n\n"
            "Для включения:\n"
            "1. Получите OAuth-токен Яндекс.Диск\n"
            "2. Установите переменную YANDEX_DISK_TOKEN\n"
            "3. Перезапустите бота",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    await update.message.reply_text(
        "☁️ *ПРОВЕРКА ПОДКЛЮЧЕНИЯ К ЯНДЕКС.ДИСКУ...*",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    
    try:
        # Сначала обновляем все листы
        wb = openpyxl.load_workbook(EXCEL_FILE)
        for sheet_name in wb.sheetnames:
            if sheet_name.startswith("user_"):
                try:
                    user_id = int(sheet_name.split("_")[1])
                    excel_manager.create_missing_dates(user_id, "")
                except:
                    continue
        wb.save(EXCEL_FILE)
        
        # Проверяем существование папки
        if not yandex_disk.check_folder_exists(YANDEX_DISK_FOLDER):
            await update.message.reply_text(
                f"❌ *ПАПКА НЕ НАЙДЕНА НА ЯНДЕКС.ДИСКЕ!*\n\n"
                f"Создайте папку вручную:\n"
                f"`{YANDEX_DISK_FOLDER}`\n\n"
                f"После создания попробуйте снова.",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return

        remote_file_path = f"{YANDEX_DISK_FOLDER}/work_tracker_backup.xlsx"
        
        if yandex_disk.upload_file(EXCEL_FILE, remote_file_path):
            file_info = yandex_disk.get_file_info(remote_file_path)
            if file_info:
                file_size = file_info.get('size', 0)
                modified = file_info.get('modified', '')
                
                # Получаем статистику файла
                stats = "Не удалось получить статистику"
                try:
                    wb = openpyxl.load_workbook(EXCEL_FILE)
                    sheet_count = len(wb.sheetnames)
                    total_rows = 0
                    for sheet in wb.sheetnames:
                        total_rows += wb[sheet].max_row
                    
                    stats = f"📑 Листов: {sheet_count}\n📊 Строк: {total_rows}"
                except:
                    pass
                
                await update.message.reply_text(
                    f"✅ *СИНХРОНИЗАЦИЯ УСПЕШНО ЗАВЕРШЕНА!*\n\n"
                    f"📊 *СТАТИСТИКА ФАЙЛА:*\n"
                    f"{stats}\n\n"
                    f"📁 *ДАННЫЕ НА ЯНДЕКС.ДИСКЕ:*\n"
                    f"• 📦 Размер: {int(file_size) / 1024 / 1024:.2f} MB\n"
                    f"• 📅 Обновлен: {modified[:19] if modified else 'Недавно'}\n"
                    f"• 🔗 Путь: {remote_file_path}\n\n"
                    f"Все данные надежно сохранены в облаке! ☁️",
                    parse_mode='Markdown',
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text(
                    "✅ *ФАЙЛ ЗАГРУЖЕН НА ЯНДЕКС.ДИСК!*\n\n"
                    f"Резервная копия успешно сохранена в папке:\n"
                    f"`{remote_file_path}`\n\n"
                    "Все данные надежно сохранены в облаке! ☁️",
                    parse_mode='Markdown',
                    reply_markup=get_main_menu_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ *ОШИБКА СИНХРОНИЗАЦИИ!*\n\n"
                "Не удалось загрузить файл на Яндекс.Диск. "
                "Проверьте:\n"
                "1. Существует ли папка на Яндекс.Диске\n"
                "2. Правильность OAuth-токена\n"
                "3. Достаточно ли места на диске",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            
    except Exception as e:
        print(f"❌ Ошибка при синхронизации: {e}")
        await update.message.reply_text(
            "❌ *ПРОИЗОШЛА ОШИБКА ПРИ СИНХРОНИЗАЦИИ!*\n\n"
            "Попробуйте позже или проверьте настройки Яндекс.Диска.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена операции"""
    user_id = update.message.from_user.id
    
    if user_id in USER_EDIT_STATE:
        del USER_EDIT_STATE[user_id]
    
    await update.message.reply_text(
        "❌ Операция отменена.",
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка неизвестных команд"""
    await update.message.reply_text(
        "❌ *Неизвестная команда.*\n"
        "*Используй кнопки меню:*\n"
        "📝 Отчет - добавить запись о работе\n"
        "✏️ Редактировать - заполнить/изменить любой день\n"
        "🗑️ Удалить запись - удалить сегодняшнюю запись\n"
        "📊 Статистика - подробная статистика\n"
        "⚙️ Напоминание - изменить время напоминания\n"
        "📥 Скачать отчет - получить Excel файл\n"
        "☁️ Синхронизировать - принудительно сохранить на Яндекс.Диск",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

def main():
    global global_app
    print("🚀 ЗАПУСК РАСШИРЕННОГО WORK TRACKER BOT...")
    print("📊 Бот для учета рабочего времени с пропусками дней")
    print("💾 Excel файл:", EXCEL_FILE)
    print("⏱️ Автоматические заглушки для пропущенных дней")
    print("✏️ Редактирование любых записей")
    print(f"☁️  Яндекс.Диск: {'ВКЛЮЧЕН' if yandex_disk else 'ВЫКЛЮЧЕН'}")
    
    if yandex_disk:
        print(f"📂 Папка на Яндекс.Диске: {YANDEX_DISK_FOLDER}")
        if yandex_disk.check_folder_exists(YANDEX_DISK_FOLDER):
            print(f"✅ Папка существует на Яндекс.Диске")
        else:
            print(f"⚠️  Папка не найдена. Создайте папку вручную: {YANDEX_DISK_FOLDER}")

    application = Application.builder().token(BOT_TOKEN).build()
    global_app = application

    # Обычный отчет (быстрый, за сегодня)
    report_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("report", report_command),
            MessageHandler(filters.Regex("^(📝 Отчет)$"), report_command)
        ],
        states={
            WAITING_EDIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_time)],
            WAITING_EDIT_LUNCH: [MessageHandler(filters.Regex("^(Да|Нет)$"), receive_edit_lunch)],
            WAITING_EDIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False  # Это устраняет предупреждение
    )

    # Редактирование/заполнение любых записей
    edit_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("edit", edit_command),
            MessageHandler(filters.Regex("^(✏️ Редактировать)$"), edit_command)
        ],
        states={
            WAITING_DATE_SELECTION: [CallbackQueryHandler(date_selection_callback)],
            WAITING_EDIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_time)],
            WAITING_EDIT_LUNCH: [MessageHandler(filters.Regex("^(Да|Нет)$"), receive_edit_lunch)],
            WAITING_EDIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False  # Это устраняет предупреждение
    )

    # Напоминания
    reminder_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("reminder", reminder_command),
            MessageHandler(filters.Regex("^(⚙️ Напоминание)$"), reminder_command)
        ],
        states={
            WAITING_REMINDER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False  # Это устраняет предупреждение
    )

    # Основные обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("download", download_file))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("sync", sync_to_yandex_disk))
    
    application.add_handler(MessageHandler(filters.Regex("^(🗑️ Удалить запись)$"), delete_command))
    application.add_handler(MessageHandler(filters.Regex("^(📊 Статистика)$"), stats_command))
    application.add_handler(MessageHandler(filters.Regex("^(📥 Скачать отчет)$"), download_file))
    application.add_handler(MessageHandler(filters.Regex("^(☁️ Синхронизировать)$"), sync_to_yandex_disk))
    
    application.add_handler(report_conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(reminder_conv_handler)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    # Восстанавливаем напоминания
    restore_reminders(application)

    print("✅ Бот успешно запущен!")
    print("📱 Ожидаем сообщения от пользователей...")
    print("🎯 Новые возможности:")
    print("   • Автоматические заглушки для пропущенных дней")
    print("   • Редактирование любых записей")
    print("   • Подробная статистика с % заполнения")
    
    try:
        application.run_polling()
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
