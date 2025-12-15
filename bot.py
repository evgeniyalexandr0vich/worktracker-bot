import os
import pytz
import logging
import asyncio
import requests
from datetime import datetime, time, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
)
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
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
    WAITING_TIME, 
    WAITING_LUNCH_CONFIRMATION, 
    WAITING_DESCRIPTION, 
    WAITING_REMINDER_TIME,
    WAITING_MISSED_DATE,  # ✅ Новое состояние для выбора даты
    WAITING_MISSED_TIME,   # ✅ Новое состояние для времени пропущенного дня
    WAITING_MISSED_LUNCH,  # ✅ Новое состояние для обеда пропущенного дня
    WAITING_MISSED_DESCRIPTION  # ✅ Новое состояние для описания пропущенного дня
) = range(8)

# Импорт конфигурации
from config import (
    BOT_TOKEN, EXCEL_FILE, DEFAULT_REMINDER_HOUR, 
    DEFAULT_REMINDER_MINUTE, USER_SETTINGS, WELCOMED_USERS, 
    MAX_ENTRIES_PER_DAY, YANDEX_DISK_ENABLED, 
    YANDEX_DISK_TOKEN, YANDEX_DISK_FOLDER, 
    MISSED_DAYS_HISTORY
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
            # Проверяем существование папка
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

class CalendarManager:
    """Класс для управления календарной таблицей в Excel"""
    
    def __init__(self, wb, current_date=None):
        self.wb = wb
        self.current_date = current_date or datetime.now()
        self.current_year = self.current_date.year
        self.current_month = self.current_date.month
        self.sheet_name = f"Календарь_{self.current_month:02d}.{self.current_year}"
        
    def _get_month_days(self, year, month):
        """Получает список дней месяца"""
        if month == 12:
            next_month = 1
            next_year = year + 1
        else:
            next_month = month + 1
            next_year = year
        
        first_day = datetime(year, month, 1)
        last_day = datetime(next_year, next_month, 1) - timedelta(days=1)
        
        days = []
        current_day = first_day
        while current_day <= last_day:
            days.append(current_day)
            current_day += timedelta(days=1)
        
        return days
    
    def _get_weekday_name(self, weekday):
        """Получает название дня недели"""
        weekdays = {
            0: "Пн",
            1: "Вт",
            2: "Ср", 
            3: "Чт",
            4: "Пт",
            5: "Сб",
            6: "Вс"
        }
        return weekdays.get(weekday, "")
    
    def _get_month_name(self, month):
        """Получает название месяца"""
        months = {
            1: "Январь",
            2: "Февраль",
            3: "Март",
            4: "Апрель",
            5: "Май",
            6: "Июнь",
            7: "Июль",
            8: "Август",
            9: "Сентябрь",
            10: "Октябрь",
            11: "Ноябрь",
            12: "Декабрь"
        }
        return months.get(month, "")
    
    def create_or_update_calendar_sheet(self, user_data):
        """Создает или обновляет календарный лист"""
        try:
            # Удаляем старый лист календаря для этого месяца, если существует
            if self.sheet_name in self.wb.sheetnames:
                old_sheet = self.wb[self.sheet_name]
                self.wb.remove(old_sheet)
            
            # Создаем новый лист календаря
            calendar_sheet = self.wb.create_sheet(title=self.sheet_name)
            
            # Получаем дни месяца
            month_days = self._get_month_days(self.current_year, self.current_month)
            
            # Заголовок
            calendar_sheet.merge_cells('A1:H1')
            header_cell = calendar_sheet['A1']
            header_cell.value = f"Календарь учета рабочего времени - {self._get_month_name(self.current_month)} {self.current_year}"
            header_cell.font = Font(bold=True, size=14)
            header_cell.alignment = Alignment(horizontal='center', vertical='center')
            
            # Шапка таблицы
            headers = ["Дата", "День недели", "Часы работы", "Статус", "Время работы", "Описание", "Обед", "Примечания"]
            for col, header in enumerate(headers, 1):
                cell = calendar_sheet.cell(row=3, column=col)
                cell.value = header
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.fill = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")
                
                # Настраиваем ширину столбцов
                if col == 1:  # Дата
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 12
                elif col == 2:  # День недели
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 10
                elif col == 3:  # Часы работы
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 12
                elif col == 4:  # Статус
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 12
                elif col == 5:  # Время работы
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 20
                elif col == 6:  # Описание
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 40
                elif col == 7:  # Обед
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 8
                elif col == 8:  # Примечания
                    calendar_sheet.column_dimensions[get_column_letter(col)].width = 20
            
            # Заполняем данные по дням
            total_hours = 0
            work_days_count = 0
            row = 4
            
            for day in month_days:
                date_str = day.strftime("%d.%m.%Y")
                weekday_str = self._get_weekday_name(day.weekday())
                
                # Ищем запись для этого дня в user_data
                day_data = None
                for entry in user_data:
                    if entry.get('date') == date_str:
                        day_data = entry
                        break
                
                # Заполняем строку
                calendar_sheet.cell(row=row, column=1).value = date_str
                calendar_sheet.cell(row=row, column=2).value = weekday_str
                
                if day_data:
                    hours = day_data.get('hours', 0)
                    status = day_data.get('status', '')
                    time_range = day_data.get('time_range', '')
                    description = day_data.get('description', '')
                    lunch = day_data.get('lunch', '')
                    
                    calendar_sheet.cell(row=row, column=3).value = hours
                    calendar_sheet.cell(row=row, column=4).value = status
                    calendar_sheet.cell(row=row, column=5).value = time_range
                    calendar_sheet.cell(row=row, column=6).value = description
                    calendar_sheet.cell(row=row, column=7).value = lunch
                    
                    if hours > 0:
                        total_hours += hours
                        work_days_count += 1
                        
                    # Раскрашиваем ячейку статуса
                    status_cell = calendar_sheet.cell(row=row, column=4)
                    if status == "НОРМА":
                        status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                    elif status == "ЗАПОЛНЕНО":
                        status_cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                    elif status == "ПРОПУЩЕНО":
                        status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    
                    # Раскрашиваем ячейку часов
                    hours_cell = calendar_sheet.cell(row=row, column=3)
                    if hours >= 8:
                        hours_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                    elif hours >= 4:
                        hours_cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                    else:
                        hours_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                else:
                    # Если нет данных за день
                    calendar_sheet.cell(row=row, column=4).value = "НЕТ ДАННЫХ"
                    calendar_sheet.cell(row=row, column=4).fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
                
                row += 1
            
            # Итоговая строка
            row += 1
            calendar_sheet.merge_cells(f'A{row}:B{row}')
            total_cell = calendar_sheet.cell(row=row, column=1)
            total_cell.value = "ИТОГО ЗА МЕСЯЦ:"
            total_cell.font = Font(bold=True)
            total_cell.alignment = Alignment(horizontal='right', vertical='center')
            
            calendar_sheet.cell(row=row, column=3).value = total_hours
            calendar_sheet.cell(row=row, column=3).font = Font(bold=True)
            calendar_sheet.cell(row=row, column=4).value = f"Дней с работой: {work_days_count}"
            
            row += 1
            calendar_sheet.merge_cells(f'A{row}:H{row}')
            info_cell = calendar_sheet.cell(row=row, column=1)
            info_cell.value = "Легенда: 🟢 НОРМА - сегодняшняя запись, 🟡 ЗАПОЛНЕНО - пропущенный день заполнен, 🔴 ПРОПУЩЕНО - день пропущен"
            info_cell.font = Font(italic=True, size=9)
            info_cell.alignment = Alignment(horizontal='center', vertical='center')
            
            # Применяем границы ко всем ячейкам с данными
            thin_border = Border(
                left=Side(style='thin'),
                right=Side(style='thin'),
                top=Side(style='thin'),
                bottom=Side(style='thin')
            )
            
            for row in calendar_sheet.iter_rows(min_row=3, max_row=row, min_col=1, max_col=8):
                for cell in row:
                    cell.border = thin_border
                    if cell.row >= 4 and cell.column in [1, 2, 3, 4]:
                        cell.alignment = Alignment(horizontal='center', vertical='center')
            
            return True
            
        except Exception as e:
            print(f"❌ Ошибка при создании календарного листа: {e}")
            import traceback
            traceback.print_exc()
            return False

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
                if 'Sheet' in wb.sheetnames:
                    std_sheet = wb['Sheet']
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
            sheet['B1'] = "Время работы"
            sheet['C1'] = "Описание работы"
            sheet['D1'] = "Часы работы без обеда"
            sheet['E1'] = "Статус"
            sheet['F1'] = "Обед"
            sheet.column_dimensions['A'].width = 12
            sheet.column_dimensions['B'].width = 15
            sheet.column_dimensions['C'].width = 50
            sheet.column_dimensions['D'].width = 20
            sheet.column_dimensions['E'].width = 15
            sheet.column_dimensions['F'].width = 10
            bold_font = Font(bold=True)
            for cell in ['A1', 'B1', 'C1', 'D1', 'E1', 'F1']:
                sheet[cell].font = bold_font
                sheet[cell].alignment = Alignment(horizontal='center', vertical='center')
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
                    status_cell = sheet[f'E{row}']
                    # Проверяем, что это не пропущенный день (пустая запись)
                    if status_cell.value != "ПРОПУЩЕНО":
                        return True
            return False
        except Exception as e:
            print(f"❌ Ошибка при проверке записи за сегодня: {e}")
            return False

    def add_entry(self, user_id: int, time_range: str, description: str, had_lunch: bool, last_name: str = "", 
                  target_date: str = None, is_missed_day: bool = False):
        """
        Добавляет запись в таблицу и обновляет календарь
        """
        try:
            print(f"🔧 Попытка сохранить запись для user_id: {user_id}")
            print(f"📁 Путь к файлу: {self.filename}")
            print(f"📝 Данные: {time_range}, {description}, обед: {had_lunch}")
            print(f"📅 Целевая дата: {target_date}, is_missed_day: {is_missed_day}")

            # Определяем дату для записи
            if target_date:
                entry_date = target_date
                entry_datetime = datetime.strptime(entry_date, "%d.%m.%Y")
            else:
                entry_date = datetime.now().strftime("%d.%m.%Y")
                entry_datetime = datetime.now()

            # ✅ ПУНКТ 1: Автоматическое добавление пустых строк за пропущенные дни
            self._add_missing_days_entries(user_id, last_name, entry_date if is_missed_day else None)

            # Гарантируем существование листа
            sheet_name = self.get_user_sheet(user_id, last_name)
            wb = openpyxl.load_workbook(self.filename)
            sheet = wb[sheet_name]

            # Ищем существующую запись за эту дату
            existing_row = None
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value == entry_date:
                    existing_row = row
                    break

            work_hours = self.calculate_work_hours(time_range, had_lunch)
            lunch_status = "Да" if had_lunch else "Нет"
            
            if existing_row:
                # Обновляем существующую запись (особенно для пропущенных дней)
                sheet[f'B{existing_row}'] = time_range
                sheet[f'C{existing_row}'] = description
                sheet[f'D{existing_row}'] = work_hours
                sheet[f'E{existing_row}'] = "ЗАПОЛНЕНО" if is_missed_day else "НОРМА"
                sheet[f'F{existing_row}'] = lunch_status
                print(f"✅ Обновлена запись за {entry_date}")
            else:
                # Добавляем новую запись
                row = sheet.max_row + 1
                sheet[f'A{row}'] = entry_date
                sheet[f'B{row}'] = time_range
                sheet[f'C{row}'] = description
                sheet[f'D{row}'] = work_hours
                sheet[f'E{row}'] = "ЗАПОЛНЕНО" if is_missed_day else "НОРМА"
                sheet[f'F{row}'] = lunch_status
                print(f"✅ Добавлена новая запись за {entry_date}")

            # ✅ Обновляем календарную таблицу
            self._update_calendar_for_user(wb, user_id, sheet_name, entry_datetime)
            
            wb.save(self.filename)
            
            # ✅ Сохраняем на Яндекс.Диск после добавления записи
            if yandex_disk:
                remote_file_path = f"{YANDEX_DISK_FOLDER}/work_tracker_backup.xlsx"
                if yandex_disk.upload_file(self.filename, remote_file_path):
                    print(f"✅ Резервная копия загружена на Яндекс.Диск")
                else:
                    print(f"⚠️ Не удалось загрузить резервную копию на Яндекс.Диск")
            
            print(f"✅ Запись добавлена для пользователя {user_id} за {entry_date}: {work_hours:.2f} ч.")
            return True, "success"
        except Exception as e:
            print(f"❌ Ошибка при записи в Excel: {e}")
            import traceback
            traceback.print_exc()
            return False, "error"

    def _add_missing_days_entries(self, user_id: int, last_name: str, target_date: str = None):
        """Добавляет пустые строки за пропущенные дни"""
        try:
            sheet_name = self.get_user_sheet(user_id, last_name)
            wb = openpyxl.load_workbook(self.filename)
            sheet = wb[sheet_name]
            
            # Получаем все даты, которые уже есть в таблице
            existing_dates = set()
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    existing_dates.add(date_cell.value)
            
            # Определяем диапазон дат для проверки
            today = datetime.now()
            if target_date:
                # Если указана целевая дата, проверяем от нее до сегодня
                target_dt = datetime.strptime(target_date, "%d.%m.%Y")
                start_date = min(target_dt, today - timedelta(days=MISSED_DAYS_HISTORY))
            else:
                # Иначе проверяем последние N дней
                start_date = today - timedelta(days=MISSED_DAYS_HISTORY)
            
            # Добавляем пустые строки за пропущенные дни
            current_date = start_date
            while current_date <= today:
                date_str = current_date.strftime("%d.%m.%Y")
                
                if date_str not in existing_dates:
                    # Добавляем пустую строку
                    row = sheet.max_row + 1
                    sheet[f'A{row}'] = date_str
                    sheet[f'B{row}'] = "ПРОПУЩЕНО"
                    sheet[f'C{row}'] = "Отсутствовал"
                    sheet[f'D{row}'] = 0
                    sheet[f'E{row}'] = "ПРОПУЩЕНО"
                    sheet[f'F{row}'] = "Нет данных"
                    print(f"✅ Добавлена пустая строка за пропущенный день: {date_str}")
                    existing_dates.add(date_str)
                
                current_date += timedelta(days=1)
            
            wb.save(self.filename)
            return True
        except Exception as e:
            print(f"❌ Ошибка при добавлении пропущенных дней: {e}")
            return False

    def _update_calendar_for_user(self, wb, user_id: int, sheet_name: str, target_date: datetime):
        """Обновляет календарную таблицу для пользователя"""
        try:
            # Получаем данные пользователя за текущий месяц
            user_sheet = wb[sheet_name]
            user_data = []
            
            for row in range(2, user_sheet.max_row + 1):
                date_cell = user_sheet[f'A{row}']
                if date_cell.value:
                    try:
                        entry_date = datetime.strptime(date_cell.value, "%d.%m.%Y")
                        # Берем только записи за текущий месяц
                        if entry_date.year == target_date.year and entry_date.month == target_date.month:
                            hours = user_sheet[f'D{row}'].value or 0
                            status = user_sheet[f'E{row}'].value or "НЕТ ДАННЫХ"
                            time_range = user_sheet[f'B{row}'].value or ""
                            description = user_sheet[f'C{row}'].value or ""
                            lunch = user_sheet[f'F{row}'].value or "Нет данных"
                            
                            user_data.append({
                                'date': date_cell.value,
                                'hours': hours,
                                'status': status,
                                'time_range': time_range,
                                'description': description,
                                'lunch': lunch
                            })
                    except ValueError:
                        continue
            
            # Создаем или обновляем календарный лист
            calendar_manager = CalendarManager(wb, target_date)
            calendar_manager.create_or_update_calendar_sheet(user_data)
            
            print(f"✅ Календарная таблица обновлена для пользователя {user_id}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка при обновлении календаря: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_user_monthly_summary(self, user_id: int, last_name: str = "", year: int = None, month: int = None):
        """Получает сводку за месяц для пользователя"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            current_date = datetime.now()
            target_year = year or current_date.year
            target_month = month or current_date.month
            
            total_hours = 0
            work_days = 0
            missed_days = 0
            entries = []
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    try:
                        entry_date = datetime.strptime(date_cell.value, "%d.%m.%Y")
                        if entry_date.year == target_year and entry_date.month == target_month:
                            hours = sheet[f'D{row}'].value or 0
                            status = sheet[f'E{row}'].value or ""
                            time_range = sheet[f'B{row}'].value or ""
                            description = sheet[f'C{row}'].value or ""
                            lunch = sheet[f'F{row}'].value or ""
                            
                            entries.append({
                                'date': date_cell.value,
                                'hours': hours,
                                'status': status,
                                'time_range': time_range,
                                'description': description,
                                'lunch': lunch
                            })
                            
                            if status == "НОРМА" or status == "ЗАПОЛНЕНО":
                                total_hours += hours
                                work_days += 1
                            elif status == "ПРОПУЩЕНО":
                                missed_days += 1
                    except ValueError:
                        continue
            
            return {
                'year': target_year,
                'month': target_month,
                'month_name': self._get_month_name(target_month),
                'total_hours': total_hours,
                'work_days': work_days,
                'missed_days': missed_days,
                'entries': entries,
                'average_hours_per_day': total_hours / work_days if work_days > 0 else 0
            }
            
        except Exception as e:
            print(f"❌ Ошибка при получении месячной сводки: {e}")
            return None

    def _get_month_name(self, month):
        """Получает название месяца"""
        months = {
            1: "Январь",
            2: "Февраль",
            3: "Март",
            4: "Апрель",
            5: "Май",
            6: "Июнь",
            7: "Июль",
            8: "Август",
            9: "Сентябрь",
            10: "Октябрь",
            11: "Ноябрь",
            12: "Декабрь"
        }
        return months.get(month, "")

    def delete_today_entry(self, user_id: int, last_name: str = ""):
        """Удаляет последнюю запись за сегодня"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            current_date = datetime.now().strftime("%d.%m.%Y")
            deleted_data = None
            
            for row in range(sheet.max_row, 1, -1):
                date_cell = sheet[f'A{row}']
                if date_cell.value == current_date:
                    deleted_data = {
                        'date': sheet[f'A{row}'].value,
                        'time_range': sheet[f'B{row}'].value,
                        'description': sheet[f'C{row}'].value,
                        'work_hours': sheet[f'D{row}'].value,
                        'status': sheet[f'E{row}'].value,
                        'lunch': sheet[f'F{row}'].value
                    }
                    sheet.delete_rows(row)
                    
                    # ✅ Обновляем календарь после удаления
                    self._update_calendar_for_user(wb, user_id, sheet_name, datetime.now())
                    wb.save(self.filename)
                    
                    # ✅ Сохраняем на Яндекс.Диск после удаления записи
                    if yandex_disk:
                        remote_file_path = f"{YANDEX_DISK_FOLDER}/work_tracker_backup.xlsx"
                        if yandex_disk.upload_file(self.filename, remote_file_path):
                            print(f"✅ Резервная копия загружена на Яндекс.Диск после удаления")
                    
                    print(f"✅ Запись за сегодня удалена для пользователя {user_id}")
                    return True, deleted_data
            
            return False, None
        except Exception as e:
            print(f"❌ Ошибка при удалении записи: {e}")
            return False, None

    def get_user_stats(self, user_id: int, last_name: str = ""):
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            total_entries = 0
            filled_entries = 0
            missed_entries = 0
            total_hours = 0
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value:
                    total_entries += 1
                    status_cell = sheet[f'E{row}']
                    hours_cell = sheet[f'D{row}']
                    
                    if status_cell and status_cell.value == "ПРОПУЩЕНО":
                        missed_entries += 1
                    else:
                        filled_entries += 1
                        if hours_cell and hours_cell.value:
                            total_hours += hours_cell.value
            
            return {
                'total': total_entries,
                'filled': filled_entries,
                'missed': missed_entries,
                'total_hours': round(total_hours, 2)
            }
        except Exception as e:
            print(f"❌ Ошибка при получении статистики: {e}")
            return {'total': 0, 'filled': 0, 'missed': 0, 'total_hours': 0}

    def check_date_exists(self, user_id: int, date_str: str, last_name: str = ""):
        """Проверяет, существует ли запись за указанную дату"""
        try:
            wb = openpyxl.load_workbook(self.filename)
            sheet_name = self.get_user_sheet(user_id, last_name)
            sheet = wb[sheet_name]
            
            for row in range(2, sheet.max_row + 1):
                date_cell = sheet[f'A{row}']
                if date_cell.value == date_str:
                    status_cell = sheet[f'E{row}']
                    return True, status_cell.value if status_cell else "НОРМА"
            
            return False, None
        except Exception as e:
            print(f"❌ Ошибка при проверке даты: {e}")
            return False, None

    def get_calendar_sheet_name(self):
        """Получает имя календарного листа для текущего месяца"""
        current_date = datetime.now()
        return f"Календарь_{current_date.month:02d}.{current_date.year}"

excel_manager = ExcelManager(EXCEL_FILE)
user_data_cache = {}

def get_main_menu_keyboard():
    keyboard = [
        ["📝 Отчет"],
        ["✏️ Редактировать", "🗑️ Удалить запись"],
        ["📊 Месячный отчет", "⚙️ Напоминание"],
        ["📥 Скачать отчет", "☁️ Синхронизировать"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, input_field_placeholder="Выберите действие...")

def get_yes_no_keyboard():
    return ReplyKeyboardMarkup([["Да", "Нет"]], resize_keyboard=True, one_time_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True, one_time_keyboard=True)

async def send_welcome_message(update: Update, user):
    yandex_status = "✅ ВКЛЮЧЕН" if yandex_disk else "❌ ВЫКЛЮЧЕН"
    yandex_folder_info = f"\n📂 *Папка:* {YANDEX_DISK_FOLDER}" if yandex_disk else ""
    
    welcome_text = (
        "🎉 *ДОБРО ПОЖАЛОВАТЬ!* 🎉\n"
        "🤖 *Я - Work Tracker Bot* 🤖\n"
        "*Моя задача:* Помогать тебе вести учет рабочего времени!\n"
        "*Как это работает:*\n"
        "• Каждый день я буду напоминать тебе заполнить отчет\n"
        "• Ты указываешь, в какое время работал и что делал\n"
        "• Все данные автоматически сохраняются в Excel таблицу\n"
        "• У каждого сотрудника свой лист в таблице\n"
        f"• ☁️ *Резервное копирование:* {yandex_status}{yandex_folder_info}\n"
        "*Новые возможности:*\n"
        "✅ *Автоматические пропуски* - если пропустил день, создается пустая запись\n"
        "✅ *Редактирование* - можно заполнить отчеты за пропущенные дни\n"
        "✅ *Календарная таблица* - автоматическая таблица с часами работы за месяц\n"
        "*Важно:* Можно сделать только *1 запись в день*\n"
        "*Преимущества:*\n"
        "✅ Всегда актуальная информация о работе\n"
        "✅ Удобный учет времени\n"
        "✅ Автоматическое сохранение\n"
        "✅ Индивидуальные настройки\n"
        "✅ Резервное копирование на Яндекс.Диск\n"
        "✅ Наглядный календарь учета рабочего времени\n"
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
    has_today_entry = excel_manager.has_today_entry(user_id, last_name)
    
    if is_new_user:
        message_text = f"👋 *Рад познакомиться, {user.first_name}!*\n"
    else:
        message_text = f"👋 *С возвращением, {user.first_name}!*\n"
    
    message_text += (
        f"📊 Твоя статистика: *{stats['filled']} заполненных записей*\n"
        f"⏱️ Всего отработано: *{stats['total_hours']:.1f} часов*\n"
        f"⏰ Напоминание установлено на: *{reminder_time.strftime('%H:%M')}*\n"
    )
    
    if stats['missed'] > 0:
        message_text += f"📅 *Пропущенных дней:* {stats['missed']} (можно заполнить через '✏️ Редактировать')\n"
    
    if has_today_entry:
        message_text += f"📝 *Сегодняшняя запись:* ✅ УЖЕ СДЕЛАНА\n"
    else:
        message_text += f"📝 *Сегодняшняя запись:* ❌ ЕЩЕ НЕТ\n"
        
    yandex_status = "✅ ВКЛЮЧЕНО" if yandex_disk else "❌ ВЫКЛЮЧЕНО"
    message_text += f"☁️ *Резервное копирование:* {yandex_status}"
    
    if yandex_disk:
        message_text += f"\n📂 *Папка на Яндекс.Диске:* {YANDEX_DISK_FOLDER}"
    
    # Получаем месячную сводку
    monthly_summary = excel_manager.get_user_monthly_summary(user_id, last_name)
    if monthly_summary:
        message_text += f"\n\n📅 *Текущий месяц ({monthly_summary['month_name']}):*\n"
        message_text += f"• 🕐 Отработано: *{monthly_summary['total_hours']:.1f} часов*\n"
        message_text += f"• 📅 Рабочих дней: *{monthly_summary['work_days']}*\n"
        if monthly_summary['work_days'] > 0:
            message_text += f"• 📊 Среднее в день: *{monthly_summary['average_hours_per_day']:.1f} часов*"
    
    message_text += "\n\n"
        
    message_text += (
        f"*Используй кнопки меню для управления:*\n"
        f"📝 *Отчет* - добавить запись о работе\n"
        f"✏️ *Редактировать* - заполнить отчеты за пропущенные дни\n"
        f"🗑️ *Удалить запись* - удалить сегодняшнюю запись\n"
        f"📊 *Месячный отчет* - получить сводку за месяц\n"
        f"⚙️ *Напоминание* - изменить время напоминания\n"
        f"📥 *Скачать отчет* - получить Excel файл\n"
        f"☁️ *Синхронизировать* - принудительно сохранить на Яндекс.Диск"
    )
    await update.message.reply_text(message_text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 Отчет":
        return await report_command(update, context)
    elif text == "✏️ Редактировать":
        return await edit_missed_days_command(update, context)
    elif text == "🗑️ Удалить запись":
        return await delete_entry_command(update, context)
    elif text == "📊 Месячный отчет":
        return await monthly_report_command(update, context)
    elif text == "⚙️ Напоминание":
        return await reminder_command(update, context)
    elif text == "📥 Скачать отчет":
        return await download_file(update, context)
    elif text == "☁️ Синхронизировать":
        return await sync_to_yandex_disk(update, context)
    else:
        await update.message.reply_text("Неизвестная команда. Используй кнопки меню.", reply_markup=get_main_menu_keyboard())

async def monthly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает месячный отчет пользователю"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    monthly_summary = excel_manager.get_user_monthly_summary(user_id, last_name)
    
    if not monthly_summary or monthly_summary['work_days'] == 0:
        await update.message.reply_text(
            "📊 *Месячный отчет*\n\n"
            "У вас еще нет записей за текущий месяц.\n"
            "Начните добавлять записи через кнопку \"📝 Отчет\".",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    # Создаем красивый отчет
    report_text = f"📊 *ОТЧЕТ ЗА {monthly_summary['month_name'].upper()} {monthly_summary['year']}*\n\n"
    report_text += f"👤 *Пользователь:* {user.first_name} {user.last_name or ''}\n"
    report_text += f"📅 *Период:* {monthly_summary['month_name']} {monthly_summary['year']}\n"
    report_text += "─" * 40 + "\n\n"
    
    report_text += "📈 *СТАТИСТИКА:*\n"
    report_text += f"• 🕐 Всего отработано: *{monthly_summary['total_hours']:.1f} часов*\n"
    report_text += f"• 📅 Рабочих дней: *{monthly_summary['work_days']}*\n"
    report_text += f"• 📊 Среднее в день: *{monthly_summary['average_hours_per_day']:.1f} часов*\n"
    report_text += f"• ❌ Пропущенных дней: *{monthly_summary['missed_days']}*\n\n"
    
    if monthly_summary['entries']:
        report_text += "📝 *ПОСЛЕДНИЕ ЗАПИСИ:*\n"
        for i, entry in enumerate(monthly_summary['entries'][-5:], 1):  # Последние 5 записей
            status_icon = "✅" if entry['status'] in ["НОРМА", "ЗАПОЛНЕНО"] else "❌"
            report_text += f"{i}. {entry['date']} {status_icon} {entry['hours']}ч. - {entry['description'][:30]}...\n"
    
    report_text += "\n" + "─" * 40 + "\n\n"
    report_text += "*Примечание:* Полный отчет с календарной таблицей доступен при скачивании Excel файла (кнопка \"📥 Скачать отчет\")"
    
    await update.message.reply_text(
        report_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

# Остальные функции остаются аналогичными предыдущей версии
# Я покажу только ключевые изменения

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    # Проверяем, есть ли уже запись за сегодня
    if excel_manager.has_today_entry(user_id, last_name):
        await update.message.reply_text(
            "❌ *Вы уже сделали запись за сегодняшний день.*\n\n"
            "Чтобы создать новую запись, сначала удалите предыдущую через кнопку \"🗑️ Удалить запись\", "
            "а затем создайте новую через кнопку \"📝 Отчет\".\n\n"
            "Или используйте \"✏️ Редактировать\" для заполнения пропущенных дней.",
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

    success, result = excel_manager.add_entry(user_id, time_range, description, had_lunch, last_name)
    
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
        current_date = datetime.now().strftime("%d.%m.%Y")
        work_hours = excel_manager.calculate_work_hours(time_range, had_lunch)
        
        # Получаем месячную сводку
        monthly_summary = excel_manager.get_user_monthly_summary(user_id, last_name)
        
        yandex_sync_text = ""
        if yandex_disk:
            yandex_sync_text = "☁️ *Данные автоматически сохранены на Яндекс.Диск*\n"
        
        response_text = (
            "🎉 *ОТЛИЧНО! Запись сохранена!*\n"
            f"{yandex_sync_text}\n"
            f"📅 *Дата:* {current_date}\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"🍽️ *Обед:* {'Да' if had_lunch else 'Нет'}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n"
            f"📊 *Всего заполненных записей:* {stats['filled']}\n"
            f"⏱️ *Всего часов:* {stats['total_hours']:.1f} ч.\n\n"
        )
        
        if monthly_summary:
            response_text += (
                f"📅 *Текущий месяц ({monthly_summary['month_name']}):*\n"
                f"• 🕐 Отработано: *{monthly_summary['total_hours']:.1f} часов*\n"
                f"• 📅 Рабочих дней: *{monthly_summary['work_days']}*\n"
                f"• 📊 Среднее в день: *{monthly_summary['average_hours_per_day']:.1f} часов*\n\n"
            )
        
        response_text += (
            "*Теперь ты можешь:*\n"
            "• ✏️ *Редактировать* - заполнить пропущенные дни\n"
            "• 🗑️ *Удалить запись* - если нужно исправить\n"
            "• 📊 *Месячный отчет* - посмотреть статистику\n"
            "• 📥 *Скачать отчет* - получить Excel файл с календарем\n"
            "• ☁️ *Синхронизировать* - принудительно сохранить в облако\n"
            "*Новая запись будет доступна завтра*"
        )
        
        await update.message.reply_text(
            response_text,
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

# Функции для редактирования пропущенных дней (аналогично предыдущей версии)
async def edit_missed_days_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса редактирования пропущенных дней"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    # Получаем статистику пропущенных дней
    stats = excel_manager.get_user_stats(user_id, last_name)
    
    if stats['missed'] == 0:
        await update.message.reply_text(
            "✅ *У вас нет пропущенных дней для заполнения!*\n\n"
            "Все дни за последний месяц уже заполнены или еще не наступили.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"✏️ *Заполнение пропущенных дней*\n\n"
        f"📊 У вас есть *{stats['missed']} пропущенных дней* за последний месяц.\n"
        f"📅 *Введите дату пропущенного дня в формате ДД.ММ.ГГГГ:*\n"
        f"*Пример:* 15.12.2023\n\n"
        f"*Ограничения:*\n"
        f"• Можно заполнять только дни за последние {MISSED_DAYS_HISTORY} дней\n"
        f"• Нельзя редактировать будущие даты\n"
        f"• Одна дата - одна запись",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return WAITING_MISSED_DATE

async def receive_missed_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введенной даты для редактирования"""
    user_id = update.message.from_user.id
    user = update.message.from_user
    text = update.message.text.strip()
    
    # Проверка на отмену
    if text == "❌ Отмена":
        await update.message.reply_text(
            "❌ Заполнение пропущенного дня отменено.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    # Проверка формата даты
    date_pattern = r'^\d{2}\.\d{2}\.\d{4}$'
    if not re.match(date_pattern, text):
        await update.message.reply_text(
            "❌ *Неверный формат даты!*\n"
            "Пожалуйста, введите дату в формате *ДД.ММ.ГГГГ*\n"
            "*Пример:* 15.12.2023\n"
            "Попробуйте еще раз:",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return WAITING_MISSED_DATE
    
    try:
        input_date = datetime.strptime(text, "%d.%m.%Y")
        today = datetime.now()
        
        # Проверяем, что дата не в будущем
        if input_date > today:
            await update.message.reply_text(
                "❌ *Нельзя заполнять отчеты за будущие даты!*\n"
                "Пожалуйста, введите прошедшую дату:",
                parse_mode='Markdown',
                reply_markup=get_cancel_keyboard()
            )
            return WAITING_MISSED_DATE
        
        # Проверяем, что дата не слишком старая
        max_history_date = today - timedelta(days=MISSED_DAYS_HISTORY)
        if input_date < max_history_date:
            await update.message.reply_text(
                f"❌ *Слишком старая дата!*\n"
                f"Можно заполнять только дни за последние {MISSED_DAYS_HISTORY} дней.\n"
                f"Самая ранняя доступная дата: {max_history_date.strftime('%d.%m.%Y')}\n"
                f"Пожалуйста, введите другую дату:",
                parse_mode='Markdown',
                reply_markup=get_cancel_keyboard()
            )
            return WAITING_MISSED_DATE
        
        last_name = user.last_name or user.first_name or ""
        date_exists, status = excel_manager.check_date_exists(user_id, text, last_name)
        
        if date_exists and status != "ПРОПУЩЕНО":
            await update.message.reply_text(
                f"❌ *Запись за {text} уже существует и заполнена!*\n"
                f"Статус: {status}\n\n"
                f"Выберите другую дату или нажмите \"❌ Отмена\":",
                parse_mode='Markdown',
                reply_markup=get_cancel_keyboard()
            )
            return WAITING_MISSED_DATE
        
        # Сохраняем дату в кэше пользователя
        if user_id not in user_data_cache:
            user_data_cache[user_id] = {}
        user_data_cache[user_id]['missed_date'] = text
        
        await update.message.reply_text(
            f"✅ *Отлично! Выбранная дата: {text}*\n\n"
            f"📝 *ШАГ 1:* Укажите ВРЕМЯ РАБОТЫ за этот день (можно несколько периодов):\n"
            f"*Примеры:*\n"
            f"• 9:00-18:00\n"
            f"• 9:00-14:00, 15:00-18:00\n"
            f"• с 10 до 12, 14:00-17:30",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return WAITING_MISSED_TIME
        
    except ValueError as e:
        await update.message.reply_text(
            f"❌ *Некорректная дата!*\n"
            f"Пожалуйста, введите корректную дату в формате ДД.ММ.ГГГГ\n"
            f"Пример: 15.12.2023\n"
            f"Попробуйте еще раз:",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return WAITING_MISSED_DATE

async def receive_missed_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка времени работы для пропущенного дня"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    # Проверка на отмену
    if text == "❌ Отмена":
        await cancel_missed_entry(update, context)
        return ConversationHandler.END
    
    if user_id not in user_data_cache or 'missed_date' not in user_data_cache[user_id]:
        await update.message.reply_text(
            "❌ Что-то пошло не так. Давайте начнем заново.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    user_data_cache[user_id]['missed_time'] = text
    
    total_hours = excel_manager.calculate_work_hours(text, had_lunch=False)
    await update.message.reply_text(
        f"✅ *Отлично!*\n"
        f"⏱️ *Общее время работы:* {total_hours:.2f} ч.\n"
        f"🍽️ *Был ли у вас обед в этот день?*\n"
        f"(Обед = вычет 0.5 часа)",
        parse_mode='Markdown',
        reply_markup=get_yes_no_keyboard()
    )
    return WAITING_MISSED_LUNCH

async def receive_missed_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка информации об обеде для пропущенного дня"""
    user_id = update.message.from_user.id
    text = update.message.text.strip().lower()
    
    if text in ["да", "yes", "д"]:
        had_lunch = True
    elif text in ["нет", "no", "н"]:
        had_lunch = False
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите «Да» или «Нет».",
            reply_markup=get_yes_no_keyboard()
        )
        return WAITING_MISSED_LUNCH

    if user_id not in user_data_cache:
        user_data_cache[user_id] = {}
    user_data_cache[user_id]['missed_had_lunch'] = had_lunch

    await update.message.reply_text(
        "📝 *ШАГ 2:* Теперь опишите ОПИСАНИЕ РАБОТЫ за этот день:\n"
        "*Примеры:*\n"
        "• Разрабатывал новый функционал\n"
        "• Участвовал в совещаниях\n"
        "• Изучал документацию\n"
        "• Исправлял ошибки\n"
        "• Общался с клиентами",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return WAITING_MISSED_DESCRIPTION

async def receive_missed_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка описания работы для пропущенного дня"""
    user_id = update.message.from_user.id
    description = update.message.text.strip()
    user = update.message.from_user
    
    # Проверка на отмену
    if description == "❌ Отмена":
        await cancel_missed_entry(update, context)
        return ConversationHandler.END
    
    if (user_id not in user_data_cache or 
        'missed_date' not in user_data_cache[user_id] or
        'missed_time' not in user_data_cache[user_id] or
        'missed_had_lunch' not in user_data_cache[user_id]):
        await update.message.reply_text(
            "❌ Что-то пошло не так. Давайте начнем заново.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    missed_date = user_data_cache[user_id]['missed_date']
    time_range = user_data_cache[user_id]['missed_time']
    had_lunch = user_data_cache[user_id]['missed_had_lunch']
    last_name = user.last_name or user.first_name or ""
    
    # Сохраняем запись за пропущенный день
    success, result = excel_manager.add_entry(
        user_id, time_range, description, had_lunch, 
        last_name, missed_date, is_missed_day=True
    )
    
    if success:
        work_hours = excel_manager.calculate_work_hours(time_range, had_lunch)
        stats = excel_manager.get_user_stats(user_id, last_name)
        monthly_summary = excel_manager.get_user_monthly_summary(user_id, last_name)
        
        yandex_sync_text = ""
        if yandex_disk:
            yandex_sync_text = "☁️ *Данные автоматически сохранены на Яндекс.Диск*\n"
        
        response_text = (
            f"🎉 *ОТЛИЧНО! Запись за пропущенный день сохранена!*\n"
            f"{yandex_sync_text}\n"
            f"📅 *Дата:* {missed_date}\n"
            f"🕐 *Время работы:* {time_range}\n"
            f"🍽️ *Обед:* {'Да' if had_lunch else 'Нет'}\n"
            f"⏱️ *Часы работы без обеда:* {work_hours:.2f} ч.\n"
            f"📝 *Описание работы:* {description}\n"
            f"📊 *Всего заполненных записей:* {stats['filled']}\n"
            f"⏱️ *Всего часов:* {stats['total_hours']:.1f} ч.\n"
            f"📅 *Осталось пропущенных дней:* {stats['missed']}\n\n"
        )
        
        if monthly_summary:
            response_text += (
                f"📅 *Текущий месяц ({monthly_summary['month_name']}):*\n"
                f"• 🕐 Отработано: *{monthly_summary['total_hours']:.1f} часов*\n"
                f"• 📅 Рабочих дней: *{monthly_summary['work_days']}*\n\n"
            )
        
        response_text += (
            f"*Что дальше?*\n"
            f"• ✏️ *Редактировать* - заполнить еще один пропущенный день\n"
            f"• 📝 *Отчет* - добавить запись за сегодня\n"
            f"• 📊 *Месячный отчет* - посмотреть статистику\n"
            f"• 📥 *Скачать отчет* - получить Excel файл с календарем"
        )
        
        await update.message.reply_text(
            response_text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Произошла ошибка при сохранении. Попробуйте еще раз.",
            reply_markup=get_main_menu_keyboard()
        )
    
    # Очищаем кэш
    if user_id in user_data_cache:
        keys_to_remove = ['missed_date', 'missed_time', 'missed_had_lunch']
        for key in keys_to_remove:
            user_data_cache[user_id].pop(key, None)
    
    return ConversationHandler.END

async def cancel_missed_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена процесса заполнения пропущенного дня"""
    user_id = update.message.from_user.id
    if user_id in user_data_cache:
        keys_to_remove = ['missed_date', 'missed_time', 'missed_had_lunch']
        for key in keys_to_remove:
            user_data_cache[user_id].pop(key, None)
    
    await update.message.reply_text(
        "❌ Заполнение пропущенного дня отменено.",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def delete_entry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = update.message.from_user
    last_name = user.last_name or user.first_name or ""
    
    success, deleted_data = excel_manager.delete_today_entry(user_id, last_name)
    
    if success:
        yandex_sync_text = ""
        if yandex_disk:
            yandex_sync_text = "\n☁️ *Изменения сохранены на Яндекс.Диск*"
            
        await update.message.reply_text(
            "🗑️ *Запись за сегодня успешно удалена!*\n"
            f"{yandex_sync_text}\n\n"
            f"📅 *Дата:* {deleted_data['date']}\n"
            f"🕐 *Время работы:* {deleted_data['time_range']}\n"
            f"📝 *Описание:* {deleted_data['description']}\n"
            f"⏱️ *Часы работы:* {deleted_data['work_hours']} ч.\n"
            f"📊 *Статус:* {deleted_data['status']}\n\n"
            "Теперь ты можешь создать новую запись через кнопку \"📝 Отчет\"\n"
            "Или заполнить пропущенные дни через \"✏️ Редактировать\"",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ *Не найдено записей за сегодня для удаления.*\n\n"
            "Сначала создайте запись через кнопку \"📝 Отчет\"",
            parse_mode='Markdown',
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
    await update.message.reply_text("❌ Диалог отменен.", reply_markup=get_main_menu_keyboard())
    return ConversationHandler.END

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
            stats = excel_manager.get_user_stats(user_id, last_name)
            message_text = (
                f"🕔 *ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ({reminder_time_str})!*\n"
                f"Привет! Я вижу, что ты уже заполнил отчет за сегодня. ✅\n\n"
                f"📊 *Твоя статистика:*\n"
                f"• 📝 Заполнено дней: {stats['filled']}\n"
                f"• ⏱️ Всего часов: {stats['total_hours']:.1f}\n\n"
                f"Если нужно что-то исправить:\n"
                f"1️⃣ Нажми '🗑️ Удалить запись'\n"
                f"2️⃣ Затем создай новую через '📝 Отчет'\n\n"
                f"Или заполни пропущенные дни через '✏️ Редактировать'"
            )
        else:
            stats = excel_manager.get_user_stats(user_id, last_name)
            missed_days_text = ""
            if stats['missed'] > 0:
                missed_days_text = f"\n📅 *У тебя есть {stats['missed']} пропущенных дней* (заполни через '✏️ Редактировать')"
            
            message_text = (
                f"🕔 *ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ({reminder_time_str})!*\n"
                f"Привет! Пора заполнить отчет о работе за сегодня.\n"
                f"Нажми кнопку '📝 Отчет' чтобы указать:\n"
                f"1️⃣ В какое время ты работал (можно несколько периодов)\n"
                f"2️⃣ Был ли обед\n"
                f"3️⃣ Что ты делал\n"
                f"Это займет всего 30 секунд! ⏱️"
                f"{missed_days_text}"
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
        
        stats = excel_manager.get_user_stats(update.message.from_user.id, 
                                           update.message.from_user.last_name or update.message.from_user.first_name or "")
        
        monthly_summary = excel_manager.get_user_monthly_summary(update.message.from_user.id,
                                                               update.message.from_user.last_name or update.message.from_user.first_name or "")
            
        with open(EXCEL_FILE, 'rb') as file:
            caption = f"📊 *Вот твой файл с отчетами!*\n"
            caption += f"📈 *СТАТИСТИКА:*\n"
            caption += f"• ✅ Заполнено: {stats['filled']} дней\n"
            caption += f"• 📅 Пропущено: {stats['missed']} дней\n"
            caption += f"• ⏱️ Всего часов: {stats['total_hours']:.1f}\n"
            
            if monthly_summary:
                caption += f"\n📅 *ТЕКУЩИЙ МЕСЯЦ ({monthly_summary['month_name']}):*\n"
                caption += f"• 🕐 Отработано: {monthly_summary['total_hours']:.1f} часов\n"
                caption += f"• 📅 Рабочих дней: {monthly_summary['work_days']}\n"
                caption += f"• 📊 Среднее в день: {monthly_summary['average_hours_per_day']:.1f} часов\n"
            
            caption += f"\n📁 *ФАЙЛ СОДЕРЖИТ:*\n"
            caption += f"1. Лист с вашими записями\n"
            caption += f"2. Календарную таблицу за текущий месяц\n"
            caption += f"3. Автоматический расчет часов\n"
            caption += f"4. Цветовую индикацию статусов\n"
            
            caption += f"{yandex_status}"
            
            await update.message.reply_document(
                document=file,
                filename=f"work_tracker_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                caption=caption,
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
        "❌ *Неизвестная команда.*\n"
        "*Используй кнопки меню:*\n"
        "📝 Отчет - добавить запись о работе\n"
        "✏️ Редактировать - заполнить отчеты за пропущенные дни\n"
        "🗑️ Удалить запись - удалить сегодняшнюю запись\n"
        "📊 Месячный отчет - получить сводку за месяц\n"
        "⚙️ Напоминание - изменить время напоминания\n"
        "📥 Скачать отчет - получить Excel файл с календарем\n"
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
    print("🚀 Запуск Work Tracker Bot...")
    print("📊 Бот для учета рабочего времени")
    print("💾 Excel файл:", EXCEL_FILE)
    print("⏱️ Поддержка нескольких периодов + выбор обеда")
    print("📝 Ограничение: 1 запись в день на пользователя")
    print("📅 История пропущенных дней:", MISSED_DAYS_HISTORY, "дней")
    print("📆 Календарная таблица: ВКЛЮЧЕНА (автоматическое обновление)")
    print(f"☁️  Яндекс.Диск: {'ВКЛЮЧЕН' if yandex_disk else 'ВЫКЛЮЧЕН'}")
    
    if yandex_disk:
        print(f"📂 Папка на Яндекс.Диске: {YANDEX_DISK_FOLDER}")
        # Проверяем существование папки при запуске
        if yandex_disk.check_folder_exists(YANDEX_DISK_FOLDER):
            print(f"✅ Папка существует на Яндекс.Диске")
        else:
            print(f"⚠️  Папка не найдена. Создайте папку вручную: {YANDEX_DISK_FOLDER}")

    application = Application.builder().token(BOT_TOKEN).build()
    global_app = application

    # Conversation handler для обычного отчета
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

    # Conversation handler для напоминаний
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

    # Conversation handler для редактирования пропущенных дней
    edit_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("edit", edit_missed_days_command),
            MessageHandler(filters.Regex("^(✏️ Редактировать)$"), edit_missed_days_command)
        ],
        states={
            WAITING_MISSED_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_missed_date)],
            WAITING_MISSED_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_missed_time)],
            WAITING_MISSED_LUNCH: [MessageHandler(filters.Regex("^(Да|Нет)$"), receive_missed_lunch)],
            WAITING_MISSED_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_missed_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel_missed_entry)]
    )

    # Добавляем все обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("download", download_file))
    application.add_handler(CommandHandler("delete", delete_entry_command))
    application.add_handler(CommandHandler("sync", sync_to_yandex_disk))
    application.add_handler(CommandHandler("monthly", monthly_report_command))
    application.add_handler(MessageHandler(filters.Regex("^(🗑️ Удалить запись)$"), delete_entry_command))
    application.add_handler(MessageHandler(filters.Regex("^(📥 Скачать отчет)$"), download_file))
    application.add_handler(MessageHandler(filters.Regex("^(☁️ Синхронизировать)$"), sync_to_yandex_disk))
    application.add_handler(MessageHandler(filters.Regex("^(📊 Месячный отчет)$"), monthly_report_command))
    application.add_handler(report_conv_handler)
    application.add_handler(reminder_conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    restore_reminders(application)

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
