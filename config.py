import os
from datetime import time

BOT_TOKEN = os.getenv('BOT_TOKEN', '8108841583:AAHNAxCDantgG51JfjyBmDdaubVFWiDHvyI')

# ✅ Excel файл в созданном Volume
EXCEL_FILE = "/app/excel_data/work_tracker.xlsx"

DEFAULT_REMINDER_HOUR = 18
DEFAULT_REMINDER_MINUTE = 0
USER_SETTINGS = {}
WELCOMED_USERS = set()

print(f"📁 Excel файл будет сохранен в: {EXCEL_FILE}")
