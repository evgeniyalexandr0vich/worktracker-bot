import os
import pytz
import logging
import asyncio
import requests
import calendar
from datetime import datetime, time, timedelta
from typing import Optional, Dict, Any
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
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
    WAITING_EDIT_DATE, WAITING_EDIT_TIME,
    WAITING_EDIT_LUNCH, WAITING_EDIT_DESCRIPTION
) = range(8)

# Импорт конфигурации
from config import (
    BOT_TOKEN, EXCEL_FILE, DEFAULT_REMINDER_HOUR, 
    DEFAULT_REMINDER_MINUTE, USER_SETTINGS, WELCOMED_USERS, 
    MAX_ENTRIES_PER_DAY, YANDEX_DISK_ENABLED, 
    YANDEX_DISK_TOKEN, YANDEX_DISK_FOLDER
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
            # Основные колонки
            sheet['A1'] = "Дата"
            sheet['B1'] = "День недели"
            sheet['C1'] = "Время работы"
            sheet['D1'] = "Описание работы"
            sheet['E1'] = "Часы работы без обеда"
            sheet['F1'] = "Статус"
            sheet['G1'] = "Месяц"
            sheet['H1'] = "Год"
            
            # Форматирование заголовков
            header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            white_font = Font(color="FFFFFF", bold=True)
            center_alignment = Alignment(horizontal="center", vertical="center")
            
            for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
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
                elif col == 'G':
                    sheet.column_dimensions['G'].width = 10
                elif col == 'H':
                    sheet.column_dimensions['H'].width = 8
            
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
        """Создает пустые строки для пропущенных дней"""
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
                # Если нет записей, начинаем с начала месяца
                today = datetime.now().date()
                start_date = today.replace(day=1)
            else:
                # Находим самую раннюю дату
                start_date = min(existing_dates)
                # Если самая ранняя дата не первое число, начинаем с первого числа месяца
                start_date = start_date.replace(day=1)
            
            today = datetime.now().date()
            
            # Создаем диапазон дат от start_date до today
            date_range = []
            current_date = start_date
            while current_date <= today:
                date_range.append(current_date)
                current_date += timedelta(days=1)
            
            # Находим пропущенные даты
            missing_dates = []
            for date in date_range:
                if date not in existing_dates:
                    missing_dates.append(date)
            
            # Создаем пустые строки для пропущенных дат
            created_count = 0
            for date in missing_dates:
                row = sheet.max_row + 1
                sheet[f'A{row}'] = date.strftime("%d.%m.%Y")
                sheet[f'B{row}'] = self._get_weekday_name(date)
                sheet[f'C{row}'] = ""  # Пустое время работы
                sheet[f'D{row}'] = ""  # Пустое описание
                sheet[f'E{row}'] = 0   # Ноль часов
                sheet[f'F{row}'] = "ПРОПУЩЕН"
                sheet[f'G{row}'] = date.month
                sheet[f'H{row}'] = date.year
                
                # Форматирование для пропущенных дней
                red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
                    sheet[f'{col}{row}'].fill = red_fill
                
                created_count += 1
            
            if created_count > 0:
                print(f"✅ Создано {created_count} пустых строк для пропущенных дней")
            
            wb.save(self.filename)
            return created_count
            
        except Exception as e:
            print(f"❌ Ошибка при создании пропущенных дат: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def _get_weekday_name(self, date_obj):
        """Возвращает название дня недели на русском"""
        weekdays = {
            0: "Понедельник",
            1: "Вторник",
            2: "Среда",
            3: "Четверг",
            4: "Пятница",
            5: "Суббота",
            6: "Воскресенье"
        }
        return weekdays[date_obj.weekday()]

    def get_user_stats(self, user_id: int, last_name: str = ""):
        """Получает статистику пользователя"""
        try:
            # Создаем пропущенные даты перед подсчетом статистики
            self.create_missing_dates(user_id, last_name)
            
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            total_days = 0
            filled_days = 0
            total_hours = 0
            missing_days = 0
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    total_days += 1
                    work_hours = sheet[f'E{row}'].value or 0
                    status = sheet[f'F{row}'].value or ""
                    
                    if status == "ЗАПОЛНЕН" and work_hours > 0:
                        filled_days += 1
                        total_hours += float(work_hours)
                    elif status == "ПРОПУЩЕН" or work_hours == 0:
                        missing_days += 1
            
            return {
                'total_days': total_days,
                'filled_days': filled_days,
                'total_hours': round(total_hours, 2),
                'missing_days': missing_days,
                'completion_rate': round((filled_days / total_days * 100) if total_days > 0 else 0, 1)
            }
        except Exception as e:
            print(f"❌ Ошибка при получении статистики: {e}")
            return {
                'total_days': 0,
                'filled_days': 0,
                'total_hours': 0,
                'missing_days': 0,
                'completion_rate': 0
            }

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
                for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:
                    sheet[f'{col}{target_row}'].fill = no_fill
                
                action = "обновлена"
            else:
                # Добавляем новую запись
                target_row = sheet.max_row + 1
                date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                sheet[f'A{target_row}'] = date_str
                sheet[f'B{target_row}'] = self._get_weekday_name(date_obj)
                sheet[f'C{target_row}'] = time_range
                sheet[f'D{target_row}'] = description
                sheet[f'E{target_row}'] = work_hours
                sheet[f'F{target_row}'] = "ЗАПОЛНЕН"
                sheet[f'G{target_row}'] = date_obj.month
                sheet[f'H{target_row}'] = date_obj.year
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

    def get_calendar_table(self, user_id: int, last_name: str = "", month: int = None, year: int = None):
        """Создает календарную таблицу для месяца"""
        try:
            # Создаем пропущенные даты
            self.create_missing_dates(user_id, last_name)
            
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            # Определяем месяц и год
            if month is None:
                month = datetime.now().month
            if year is None:
                year = datetime.now().year
            
            # Получаем данные за указанный месяц
            month_data = {}
            for row in range(2, sheet.max_row + 1):
                try:
                    month_cell = sheet[f'G{row}']
                    year_cell = sheet[f'H{row}']
                    
                    if month_cell.value == month and year_cell.value == year:
                        date_cell = sheet[f'A{row}']
                        hours_cell = sheet[f'E{row}']
                        status_cell = sheet[f'F{row}']
                        
                        if date_cell.value:
                            try:
                                # Извлекаем день из даты
                                day = int(date_cell.value.split('.')[0])
                                hours = float(hours_cell.value) if hours_cell.value else 0
                                status = status_cell.value or ""
                                month_data[day] = {
                                    'hours': hours,
                                    'status': status
                                }
                            except:
                                continue
                except:
                    continue
            
            # Создаем календарь
            cal = calendar.monthcalendar(year, month)
            month_names = {
                1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
                5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
                9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
            }
            
            month_name = month_names.get(month, f"Месяц {month}")
            
            # Формируем таблицу календаря
            table = f"📅 *КАЛЕНДАРЬ: {month_name} {year}*\n\n"
            table += "Пн | Вт | Ср | Чт | Пт | Сб | Вс\n"
            table += "---" * 7 + "\n"
            
            total_month_hours = 0
            work_days_count = 0
            
            for week in cal:
                week_line = ""
                for day in week:
                    if day == 0:
                        week_line += "   | "
                    else:
                        if day in month_data:
                            data = month_data[day]
                            hours = data['hours']
                            status = data['status']
                            
                            if status == "ЗАПОЛНЕН" and hours > 0:
                                week_line += f"{day:2d}✅| "
                                total_month_hours += hours
                                work_days_count += 1
                            elif status == "ПРОПУЩЕН":
                                week_line += f"{day:2d}❌| "
                            else:
                                week_line += f"{day:2d}  | "
                        else:
                            week_line += f"{day:2d}  | "
                table += week_line + "\n"
            
            # Добавляем статистику за месяц
            table += f"\n📊 *СТАТИСТИКА ЗА {month_name.upper()} {year}*\n"
            table += f"• 📅 Отработано дней: *{work_days_count}*\n"
            table += f"• ⏱️ Всего часов: *{total_month_hours:.2f} ч.*\n"
            
            if work_days_count > 0:
                avg_hours = total_month_hours / work_days_count
                table += f"• 📈 Среднее в день: *{avg_hours:.2f} ч.*\n"
            
            # Общая статистика
            stats = self.get_user_stats(user_id, last_name)
            table += f"\n📈 *ОБЩАЯ СТАТИСТИКА:*\n"
            table += f"• 📅 Всего дней: *{stats['total_days']}*\n"
            table += f"• ✅ Заполнено: *{stats['filled_days']}*\n"
            table += f"• ❌ Пропущено: *{stats['missing_days']}*\n"
            table += f"• 🎯 Заполнение: *{stats['completion_rate']}%*\n"
            table += f"• ⏱️ Всего часов: *{stats['total_hours']} ч.*"
            
            return table
            
        except Exception as e:
            print(f"❌ Ошибка при создании календарной таблицы: {e}")
            return "❌ Не удалось создать календарь. Попробуйте позже."

    def get_available_months(self, user_id: int, last_name: str = ""):
        """Возвращает список месяцев, за которые есть данные"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            months = set()
            current_year = datetime.now().year
            
            for row in range(2, sheet.max_row + 1):
                try:
                    year_cell = sheet[f'H{row}']
                    month_cell = sheet[f'G{row}']
                    
                    if year_cell.value and month_cell.value:
                        year = int(year_cell.value)
                        month = int(month_cell.value)
                        
                        # Добавляем только прошедшие и текущий месяц
                        if year < current_year or (year == current_year and month <= datetime.now().month):
                            months.add((year, month))
                except:
                    continue
            
            # Сортируем по году и месяцу
            sorted_months = sorted(months, key=lambda x: (x[0], x[1]), reverse=True)
            
            # Ограничиваем 12 месяцами
            return sorted_months[:12]
            
        except Exception as e:
            print(f"❌ Ошибка при получении списка месяцев: {e}")
            return []

excel_manager = ExcelManager(EXCEL_FILE)
user_data_cache = {}

def get_main_menu_keyboard():
    keyboard = [
        ["📝 Отчет", "✏️ Редактировать"],
        ["🗑️ Удалить запись", "📅 Календарь"],
        ["📥 Скачать отчет", "☁️ Синхронизировать"],
        ["⚙️ Напоминание"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")

def get_yes_no_keyboard():
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)

def get_calendar_menu_keyboard(months_data):
    """Создает клавиатуру для выбора месяца"""
    keyboard = []
    month_names = {
        1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр",
        5: "Май", 6: "Июн", 7: "Июл", 8: "Авг",
        9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек"
    }
    
    row = []
    for i, (year, month) in enumerate(months_data):
        month_name = month_names.get(month, str(month))
        button_text = f"{month_name} {year}"
        row.append(button_text)
        
        if len(row) == 2 or i == len(months_data) - 1:
            keyboard.append(row)
            row = []
    
    # Добавляем кнопку текущего месяца и возврата
    current_month = datetime.now().month
    current_year = datetime.now().year
    current_month_name = month_names.get(current_month, str(current_month))
    keyboard.append([f"{current_month_name} {current_year} (текущий)"])
    keyboard.append(["🏠 В главное меню"])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def send_welcome_message(update: Update, user):
    yandex_status = "✅ ВКЛЮЧЕН" if yandex_disk else "❌ ВЫКЛЮЧЕН"
    yandex_folder_info = f"\n📂 *Папка:* {YANDEX_DISK_FOLDER}" if yandex_disk else ""
    
    welcome_text = (
        "🎉 *УЛУЧШЕННЫЙ WORK TRACKER BOT* 🎉\n"
        "🤖 *Новые возможности:*\n"
        "• *Автоматические пустые строки* для пропущенных дней\n"
        "• *Редактирование пропущенных дней*\n"
        "• *Календарная таблица* с визуализацией часов\n"
        "• *Подробная статистика* по месяцам\n\n"
        "*Как это работает:*\n"
        "1️⃣ Бот автоматически создает строки для всех дней с начала месяца\n"
        "2️⃣ Пропущенные дни отмечаются ❌ в календаре\n"
        "3️⃣ Заполненные дни отмечаются ✅ с количеством часов\n"
        "4️⃣ Вы можете заполнить любой пропущенный день\n"
        f"5️⃣ ☁️ *Резервное копирование:* {yandex_status}{yandex_folder_info}\n\n"
        "*Используйте новые кнопки:*\n"
        "✏️ *Редактировать* - заполнить пропущенный день\n"
        "📅 *Календарь* - посмотреть календарь с часами\n"
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
    
    # Создаем пропущенные даты при старте
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
        f"• 📅 Всего дней: *{stats['total_days']}*\n"
        f"• ✅ Заполнено дней: *{stats['filled_days']}*\n"
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
        f"✏️ *Редактировать* - заполнить пропущенный день\n"
        f"🗑️ *Удалить запись* - удалить/сбросить запись\n"
        f"📅 *Календарь* - посмотреть календарь с часами\n"
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
    elif text == "📅 Календарь":
        return await calendar_command(update, context)
    elif text == "⚙️ Напоминание":
        return await reminder_command(update, context)
    elif text == "📥 Скачать отчет":
        return await download_file(update, context)
    elif text == "☁️ Синхронизировать":
        return await sync_to_yandex_disk(update, context)
    else:
        # Проверяем, не выбрал ли пользователь месяц в календаре
        if " (текущий)" in text:
            # Показываем текущий месяц
            current_month = datetime.now().month
            current_year = datetime.now().year
            user = update.message.from_user
            last_name = user.last_name or user.first_name or ""
            
            calendar_table = excel_manager.get_calendar_table(
                user.id, last_name, current_month, current_year
            )
            
            await update.message.reply_text(
                calendar_table,
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return
        elif any(month_name in text for month_name in ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", 
                                                      "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]):
            # Пытаемся распарсить месяц и год
            try:
                parts = text.split()
                month_names = {
                    "Янв": 1, "Фев": 2, "Мар": 3, "Апр": 4,
                    "Май": 5, "Июн": 6, "Июл": 7, "Авг": 8,
                    "Сен": 9, "Окт": 10, "Ноя": 11, "Дек": 12
                }
                
                month_str = parts[0]
                year = int(parts[1])
                month = month_names.get(month_str)
                
                if month:
                    user = update.message.from_user
                    last_name = user.last_name or user.first_name or ""
                    
                    calendar_table = excel_manager.get_calendar_table(
                        user.id, last_name, month, year
                    )
                    
                    await update.message.reply_text(
                        calendar_table,
                        parse_mode='Markdown',
                        reply_markup=get_main_menu_keyboard()
                    )
                    return
            except:
                pass
        
        await update.message.reply_text("Неизвестная команда. Используй кнопки меню.", reply_markup=get_main_menu_keyboard())

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора месяца для календаря"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    # Получаем список доступных месяцев
    months = excel_manager.get_available_months(user_id, last_name)
    
    if not months:
        # Показываем текущий месяц, если нет данных
        current_month = datetime.now().month
        current_year = datetime.now().year
        
        calendar_table = excel_manager.get_calendar_table(
            user_id, last_name, current_month, current_year
        )
        
        await update.message.reply_text(
            calendar_table,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    # Создаем клавиатуру с месяцами
    keyboard = get_calendar_menu_keyboard(months)
    
    month_names = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }
    
    # Формируем список месяцев
    months_list = ""
    for year, month in months:
        month_name = month_names.get(month, f"Месяц {month}")
        months_list += f"• {month_name} {year}\n"
    
    await update.message.reply_text(
        f"📅 *ВЫБЕРИТЕ МЕСЯЦ ДЛЯ ПРОСМОТРА*\n\n"
        f"*Доступные месяцы:*\n{months_list}\n"
        f"*Обозначения в календаре:*\n"
        f"✅ - день заполнен (отработано X часов)\n"
        f"❌ - день пропущен\n"
        f"цифра - номер дня месяца\n\n"
        f"*Выберите месяц:*",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса редактирования пропущенных дней"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    
    # Создаем пропущенные даты перед началом редактирования
    last_name = user.last_name or user.first_name or ""
    excel_manager.create_missing_dates(user_id, last_name)
    
    await update.message.reply_text(
        "✏️ *РЕДАКТИРОВАНИЕ ПРОПУЩЕННОГО ДНЯ*\n\n"
        "Введите дату в формате *ДД.ММ.ГГГГ*:\n"
        "*Примеры:*\n"
        "• 15.01.2024 - 15 января 2024\n"
        "• 01.12.2023 - 1 декабря 2023\n"
        "• 25.02.2024 - 25 февраля 2024\n\n"
        "*Примечание:* Можно редактировать только дни с начала текущего месяца.",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WAITING_EDIT_DATE

async def receive_edit_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение даты для редактирования"""
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    
    # Проверяем формат даты
    date_pattern = r'^(\d{2})\.(\d{2})\.(\d{4})$'
    match = re.match(date_pattern, user_input)
    
    if not match:
        await update.message.reply_text(
            "❌ *Неверный формат даты!*\n"
            "Пожалуйста, введите дату в формате *ДД.ММ.ГГГГ*:\n"
            "• 15.01.2024\n• 01.12.2023\n• 25.02.2024\n\n"
            "Попробуйте еще раз:",
            parse_mode='Markdown'
        )
        return WAITING_EDIT_DATE
    
    day, month, year = map(int, match.groups())
    
    # Проверяем корректность даты
    try:
        selected_date = datetime(year, month, day).date()
        today = datetime.now().date()
        
        # Проверяем, что дата не в будущем
        if selected_date > today:
            await update.message.reply_text(
                "❌ *Дата не может быть в будущем!*\n"
                "Пожалуйста, введите прошедшую или сегодняшнюю дату.\n\n"
                "Попробуйте еще раз:",
                parse_mode='Markdown'
            )
            return WAITING_EDIT_DATE
        
        # Проверяем, что дата не раньше начала текущего месяца
        first_day_of_month = today.replace(day=1)
        if selected_date < first_day_of_month:
            await update.message.reply_text(
                f"❌ *Дата слишком старая!*\n"
                f"Можно редактировать только дни с {first_day_of_month.strftime('%d.%m.%Y')}\n\n"
                f"Попробуйте еще раз:",
                parse_mode='Markdown'
            )
            return WAITING_EDIT_DATE
        
    except ValueError:
        await update.message.reply_text(
            "❌ *Некорректная дата!*\n"
            "Пожалуйста, введите существующую дату.\n\n"
            "Попробуйте еще раз:",
            parse_mode='Markdown'
        )
        return WAITING_EDIT_DATE
    
    # Сохраняем дату в контексте
    context.user_data['edit_date'] = user_input
    
    await update.message.reply_text(
        f"📅 *Выбрана дата: {user_input}*\n\n"
        "🕐 *ШАГ 1:* Укажите ВРЕМЯ РАБОТЫ (можно несколько периодов):\n"
        "*Примеры:*\n"
        "• 9:00-18:00\n"
        "• 9:00-14:00, 15:00-18:00\n"
        "• с 10 до 12, 14:00-17:30\n"
        "Используйте запятую для разделения периодов.",
        parse_mode='Markdown'
    )
    
    return WAITING_EDIT_TIME

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    # Проверяем, есть ли уже запись за сегодня
    if excel_manager.has_today_entry(user_id, last_name):
        await update.message.reply_text(
            "❌ *Вы уже сделали запись за сегодняшний день.*\n\n"
            "Чтобы создать новую запись, сначала удалите предыдущую через кнопку \"🗑️ Удалить запись\", "
            "а затем создайте новую через кнопку \"📝 Отчет\".",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
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
    time_range = update.message.text
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
    
    if (user_id not in user_data_cache or
        'time_range' not in user_data_cache[user_id] or
        'had_lunch' not in user_data_cache[user_id]):
        await update.message.reply_text("❌ Что-то пошло не так. Давай начнем заново", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    time_range = user_data_cache[user_id]['time_range']
    had_lunch = user_data_cache[user_id]['had_lunch']
    last_name = user.last_name or user.first_name or ""
    
    # Используем сегодняшнюю дату для обычного отчета
    today_str = datetime.now().strftime("%d.%m.%Y")
    
    success, result, row_num = excel_manager.add_entry(user_id, today_str, time_range, description, had_lunch, last_name)
    
    if result == "limit_exceeded":
        await update.message.reply_text(
            "❌ *Вы уже сделали запись за сегодняшний день.*\n\n"
            "Чтобы создать новую запись, сначала удалите предыдущую через кнопку \"🗑️ Удалить запись\", "
            "а затем создайте новую через кнопку \"📝 Отчет\".",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    elif success:
        stats = excel_manager.get_user_stats(user_id, last_name)
        work_hours = excel_manager.calculate_work_hours(time_range, had_lunch)
        
        yandex_sync_text = ""
        if yandex_disk:
            yandex_sync_text = "☁️ *Данные автоматически сохранены на Яндекс.Диск*\n"
        
        await update.message.reply_text(
            "🎉 *ОТЛИЧНО! Запись сохранена!*\n"
            f"{yandex_sync_text}\n"
            f"📅 *Дата:* {today_str}\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"🍽️ *Обед:* {'Да' if had_lunch else 'Нет'}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n\n"
            f"📊 *СТАТИСТИКА:*\n"
            f"• 📅 Всего дней: {stats['total_days']}\n"
            f"• ✅ Заполнено: {stats['filled_days']}\n"
            f"• 📈 Заполнение: {stats['completion_rate']}%\n\n"
            "*Теперь ты можешь:*\n"
            "• ✏️ *Редактировать* другие дни\n"
            "• 📅 *Посмотреть календарь*\n"
            "• 📥 *Скачать полный отчет*",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Произошла ошибка при сохранении. Попробуй еще раз",
            reply_markup=get_main_menu_keyboard()
        )
    
    if user_id in user_data_cache:
        del user_data_cache[user_id]
    return ConversationHandler.END

async def receive_edit_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение времени работы при редактировании"""
    user_id = update.message.from_user.id
    time_range = update.message.text
    
    if 'edit_date' not in context.user_data:
        await update.message.reply_text(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    # Сохраняем время в контексте
    context.user_data['edit_time_range'] = time_range
    
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
    
    if 'edit_date' not in context.user_data or 'edit_time_range' not in context.user_data:
        await update.message.reply_text(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    # Сохраняем информацию об обеде
    context.user_data['edit_had_lunch'] = had_lunch
    
    selected_date = context.user_data['edit_date']
    
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
    
    if not all(key in context.user_data for key in ['edit_date', 'edit_time_range', 'edit_had_lunch']):
        await update.message.reply_text(
            "❌ Недостаточно данных. Начните заново.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    selected_date = context.user_data['edit_date']
    time_range = context.user_data['edit_time_range']
    had_lunch = context.user_data['edit_had_lunch']
    
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
        
        # Получаем день недели
        date_obj = datetime.strptime(selected_date, "%d.%m.%Y")
        weekday = excel_manager._get_weekday_name(date_obj)
        
        message_text = (
            f"🎉 *ЗАПИСЬ ЗА {selected_date} ОБНОВЛЕНА!*\n"
            f"{yandex_sync_text}\n"
            f"📅 *Дата:* {selected_date} ({weekday})\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"🍽️ *Обед:* {'Да' if had_lunch else 'Нет'}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n\n"
            f"📊 *СТАТИСТИКА:*\n"
            f"• 📅 Всего дней: {stats['total_days']}\n"
            f"• ✅ Заполнено: {stats['filled_days']}\n"
            f"• 📈 Заполнение: {stats['completion_rate']}%\n\n"
            "*Вы можете:*\n"
            "• ✏️ *Редактировать* другие дни\n"
            "• 📅 *Посмотреть календарь*\n"
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
    
    # Очищаем контекст
    context.user_data.clear()
    
    return ConversationHandler.END

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление записи за сегодня"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    today_str = datetime.now().strftime("%d.%m.%Y")
    
    # Для удаления просто обнуляем запись (делаем ее пропущенной)
    success, result, row_num = excel_manager.add_entry(user_id, today_str, "", "", False, last_name)
    
    if success:
        await update.message.reply_text(
            f"🗑️ *ЗАПИСЬ ЗА СЕГОДНЯ ({today_str}) УДАЛЕНА!*\n\n"
            "День помечен как пропущенный.\n"
            "Вы можете заполнить его заново через кнопку '📝 Отчет' или '✏️ Редактировать'.",
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

# Остальные функции (reminder_command, download_file, sync_to_yandex_disk и т.д.)
# остаются такими же, но добавлю недостающие функции

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
                f"2️⃣ Введи сегодняшнюю дату\n"
                f"3️⃣ Исправь данные"
            )
        else:
            message_text = (
                f"🕔 *ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ({reminder_time_str})!*\n"
                f"Привет! Пора заполнить отчет о работе за сегодня.\n"
                f"Нажми кнопку '📝 Отчет' чтобы указать:\n"
                f"1️⃣ В какое время ты работал (можно несколько периодов)\n"
                f"2️⃣ Был ли обед\n"
                f"3️⃣ Что ты делал\n"
                f"Это займет всего 30 секунд! ⏱️"
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

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not os.path.exists(EXCEL_FILE):
            await update.message.reply_text(
                "❌ Файл с отчетами еще не создан. Добавь первую запись через кнопку '📝 Отчет'",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        yandex_status = ""
        if yandex_disk:
            yandex_status = "\n☁️ *Резервная копия хранится на Яндекс.Диске*"
            
        with open(EXCEL_FILE, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename=f"work_reports_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
                caption=f"📊 *Вот твой файл с отчетами!*\n"
                       f"Файл содержит:\n"
                       f"• Все записи о рабочем времени\n"
                       f"• Пустые строки для пропущенных дней\n"
                       f"• Календарные данные по месяцам\n"
                       f"• Индивидуальные листы для каждого пользователя\n"
                       f"{yandex_status}",
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
        "☁️ *Проверяю подключение к Яндекс.Диску...*",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    
    try:
        # Проверяем существование папки
        if not yandex_disk.check_folder_exists(YANDEX_DISK_FOLDER):
            await update.message.reply_text(
                f"❌ *Папка не найдена на Яндекс.Диске!*\n\n"
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
                await update.message.reply_text(
                    f"✅ *Синхронизация успешно завершена!*\n\n"
                    f"📊 *Данные файла на Яндекс.Диске:*\n"
                    f"• 📁 Размер: {int(file_size) / 1024 / 1024:.2f} MB\n"
                    f"• 📅 Обновлен: {modified[:19] if modified else 'Неизвестно'}\n"
                    f"• 🔗 Путь: {remote_file_path}\n\n"
                    f"Все данные надежно сохранены в облаке! ☁️",
                    parse_mode='Markdown',
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await update.message.reply_text(
                    "✅ *Файл загружен на Яндекс.Диск!*\n\n"
                    f"Резервная копия успешно сохранена в папке:\n"
                    f"`{remote_file_path}`\n\n"
                    "Все данные надежно сохранены в облаке! ☁️",
                    parse_mode='Markdown',
                    reply_markup=get_main_menu_keyboard()
                )
        else:
            await update.message.reply_text(
                "❌ *Ошибка синхронизации!*\n\n"
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
            "❌ *Произошла ошибка при синхронизации!*\n\n"
            "Попробуйте позже или проверьте настройки Яндекс.Диска.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_data_cache:
        del user_data_cache[user_id]
    # Очищаем контекст редактирования
    if context.user_data:
        context.user_data.clear()
    await update.message.reply_text("❌ Операция отменена.", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ *Неизвестная команда.*\n"
        "*Используй кнопки меню:*\n"
        "📝 Отчет - добавить запись о работе\n"
        "✏️ Редактировать - заполнить пропущенный день\n"
        "🗑️ Удалить запись - удалить сегодняшнюю запись\n"
        "📅 Календарь - посмотреть календарь с часами\n"
        "⚙️ Напоминание - изменить время напоминания\n"
        "📥 Скачать отчет - получить Excel файл\n"
        "☁️ Синхронизировать - принудительно сохранить на Яндекс.Диск",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

def restore_reminders(application: Application):
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

def main():
    global global_app
    print("🚀 ЗАПУСК УЛУЧШЕННОГО WORK TRACKER BOT...")
    print("📊 Бот для учета рабочего времени с календарем")
    print("💾 Excel файл:", EXCEL_FILE)
    print("⏱️ Автоматические пустые строки для пропущенных дней")
    print("✏️ Редактирование пропущенных дней")
    print("📅 Календарная таблица с визуализацией часов")
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
            WAITING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time)],
            WAITING_LUNCH_CONFIRMATION: [MessageHandler(filters.Regex("^(Да|Нет)$"), receive_lunch_confirmation)],
            WAITING_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # Редактирование пропущенных дней
    edit_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("edit", edit_command),
            MessageHandler(filters.Regex("^(✏️ Редактировать)$"), edit_command)
        ],
        states={
            WAITING_EDIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_date)],
            WAITING_EDIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_time)],
            WAITING_EDIT_LUNCH: [MessageHandler(filters.Regex("^(Да|Нет)$"), receive_edit_lunch)],
            WAITING_EDIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
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
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # Основные обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("download", download_file))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("sync", sync_to_yandex_disk))
    
    application.add_handler(MessageHandler(filters.Regex("^(🗑️ Удалить запись)$"), delete_command))
    application.add_handler(MessageHandler(filters.Regex("^(📅 Календарь)$"), calendar_command))
    application.add_handler(MessageHandler(filters.Regex("^(📥 Скачать отчет)$"), download_file))
    application.add_handler(MessageHandler(filters.Regex("^(☁️ Синхронизировать)$"), sync_to_yandex_disk))
    
    application.add_handler(report_conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(reminder_conv_handler)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    restore_reminders(application)

    print("✅ Бот успешно запущен!")
    print("📱 Ожидаем сообщения от пользователей...")
    print("🎯 Новые возможности:")
    print("   • Автоматические пустые строки для пропущенных дней")
    print("   • Редактирование пропущенных дней")
    print("   • Календарная таблица с визуализацией часов")
    print("   • Подробная статистика по месяцам")
    
    try:
        application.run_polling()
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
