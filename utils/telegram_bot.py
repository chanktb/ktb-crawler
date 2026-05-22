import requests

import os
from dotenv import load_dotenv

# Load biến môi trường từ file .env (nếu có)
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_alert(message, image_path=None):
    """
    Gửi tin nhắn cảnh báo tới Telegram. Gửi kèm hình ảnh lỗi nếu có File Path.
    """
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": f"🚨 <b>CẢNH BÁO HỆ THỐNG KTB-POD</b> 🚨\n{message}",
            "parse_mode": "HTML"
        }
        res = requests.post(url, data=payload)
        
        if image_path:
            url_photo = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            with open(image_path, 'rb') as photo:
                files = {"photo": photo}
                data = {"chat_id": CHAT_ID, "caption": "Hình ảnh bị lỗi này đang nằm trong khu vực cách ly (Review_Manual)."}
                requests.post(url_photo, files=files, data=data)
        
        return True
    except Exception as e:
        print(f"[Telegram Bot Error] Lỗi gửi cảnh báo: {e}")
        return False
