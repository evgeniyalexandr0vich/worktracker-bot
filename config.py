import os
from datetime import time

BOT_TOKEN = os.getenv('BOT_TOKEN', '8108841583:AAHNAxCDantgG51JfjyBmDdaubVFWiDHvyI')

# ✅ Автоматическое определение пути для Railway
if os.path.exists('/app'):
    # Production на Railway
    EXCEL_DIR = "/app/excel_data"
else:
    # Локальная разработка
    EXCEL_DIR = "./excel_data"

# Создаем папку
os.makedirs(EXCEL_DIR, exist_ok=True)
EXCEL_FILE = os.path.join(EXCEL_DIR, "work_tracker_new.xlsx")

DEFAULT_REMINDER_HOUR = 18
DEFAULT_REMINDER_MINUTE = 0
USER_SETTINGS = {}
WELCOMED_USERS = set()

# ✅ Новые константы для ограничения записей
MAX_ENTRIES_PER_DAY = 1

# ✅ Настройки Яндекс.Диск
YANDEX_DISK_ENABLED = True  # Включить/выключить сохранение на Яндекс.Диск
YANDEX_DISK_TOKEN = os.getenv('YANDEX_DISK_TOKEN', '')  # OAuth-токен Яндекс.Диск

# ✅ Укажите путь к СУЩЕСТВУЮЩЕЙ папке на Яндекс.Диске
YANDEX_DISK_FOLDER = "/PolitechCNC/Планирование и загрузка /Планирование"

# ✅ Новая константа для автоматического заполнения пропущенных дней
AUTO_FILL_MISSING_DAYS = True

print("🚀 Конфигурация Work Tracker Bot:")
print(f"✅ BOT_TOKEN: {'Установлен' if BOT_TOKEN and BOT_TOKEN != '8108841583:AAHNAxCDantgG51JfjyBmDdaubVFWiDHvyI' else 'ПРОВЕРЬТЕ НАСТРОЙКИ'}")
print(f"📁 Используемая папка: {EXCEL_DIR}")
print(f"💾 Файл данных: {EXCEL_FILE}")
print(f"🔧 Папка существует: {os.path.exists(EXCEL_DIR)}")
print(f"🔧 Можно писать в папку: {os.access(EXCEL_DIR, os.W_OK) if os.path.exists(EXCEL_DIR) else 'НЕТ'}")
print(f"📊 Максимум записей в день: {MAX_ENTRIES_PER_DAY}")
print(f"✅ Автозаполнение пропущенных дней: {'ВКЛЮЧЕНО' if AUTO_FILL_MISSING_DAYS else 'ВЫКЛЮЧЕНО'}")
print(f"☁️  Яндекс.Диск: {'ВКЛЮЧЕН' if YANDEX_DISK_ENABLED and YANDEX_DISK_TOKEN else 'ВЫКЛЮЧЕН'}")
if YANDEX_DISK_ENABLED and YANDEX_DISK_TOKEN:
    print(f"📂 Папка на Яндекс.Диске: {YANDEX_DISK_FOLDER}")
