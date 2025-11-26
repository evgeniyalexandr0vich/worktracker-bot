import os
from datetime import time

# НАСТРОЙКИ БОТА
BOT_TOKEN = os.getenv('BOT_TOKEN', '8108841583:AAHNAxCDantgG51JfjyBmDdaubVFWiDHvyI')

# Excel файл в постоянном хранилище
EXCEL_FILE = os.getenv('EXCEL_FILE', '/data/work_tracker.xlsx')

# Время отправки напоминания по умолчанию
DEFAULT_REMINDER_HOUR = 18
DEFAULT_REMINDER_MINUTE = 0

USER_SETTINGS = {}
WELCOMED_USERS = set()
