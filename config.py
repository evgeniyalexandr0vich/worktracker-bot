import os
from datetime import time

# НАСТРОЙКИ БОТА
BOT_TOKEN = os.getenv('BOT_TOKEN', '8108841583:AAHNAxCDantgG51JfjyBmDdaubVFWiDHvyI')

# Настройки Excel
EXCEL_FILE = os.getenv('EXCEL_FILE', 'work_tracker.xlsx')

# Время отправки напоминания по умолчанию (24-часовой формат)
DEFAULT_REMINDER_HOUR = 18
DEFAULT_REMINDER_MINUTE = 0

# Словарь для хранения индивидуальных настроек пользователей
USER_SETTINGS = {}

# Список пользователей, которые уже получили приветственное сообщение
WELCOMED_USERS = set()
