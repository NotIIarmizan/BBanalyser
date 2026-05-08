import os
import sqlite3
import re
import tempfile
import logging
from PIL import Image
from paddleocr import PaddleOCR
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

OWNER_ID = 7602966724
TARGET_BOT = "banditchatbot"

# Инициализация PaddleOCR (один раз)
logging.info("Загружаю PaddleOCR...")
ocr = PaddleOCR(lang='en', use_angle_cls=False, show_log=False)
logging.info("PaddleOCR готов.")

DB_PATH = os.path.expanduser("~/roulette_stats.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS spins
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              number INTEGER,
              color TEXT,
              parity TEXT,
              half TEXT,
              dozen TEXT,
              chat_id INTEGER,
              timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

def main_keyboard():
    keyboard = [
        [KeyboardButton("📊 Статистика"), KeyboardButton("🔍 Закономерности"), KeyboardButton("🕐 Последние 10")],
        [KeyboardButton("🗑 Сбросить"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_props(n):
    if n == 0:
        return {'color': 'зелёное', 'parity': 'ноль', 'half': 'ноль', 'dozen': 'ноль'}
    red = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    color = 'красное' if n in red else 'чёрное'
    parity = 'чёт' if n % 2 == 0 else 'нечёт'
    half = 'малые' if n <= 18 else 'большие'
    if n <= 12: dozen = '1-12'
    elif n <= 24: dozen = '13-24'
    else: dozen = '25-36'
    return {'color': color, 'parity': parity, 'half': half, 'dozen': dozen}

def save_spin(n, p, chat_id=0):
    c.execute("INSERT INTO spins (number, color, parity, half, dozen, chat_id) VALUES (?,?,?,?,?,?)",
              (n, p['color'], p['parity'], p['half'], p['dozen'], chat_id))
    conn.commit()
    logging.info(f"Сохранено: {n} | {p['color']} | {p['parity']}")

def get_stats():
    c.execute("SELECT COUNT(*) FROM spins")
    total = c.fetchone()[0]
    if total == 0: return None
    stats = {'total': total}
    c.execute("SELECT number, COUNT(*) as cnt FROM spins GROUP BY number ORDER BY cnt DESC LIMIT 5")
    stats['top_numbers'] = c.fetchall()
    c.execute("SELECT number, MAX(id) as last_id, COUNT(*) as cnt FROM spins GROUP BY number ORDER BY last_id ASC LIMIT 5")
    stats['cold_numbers'] = c.fetchall()
    c.execute("SELECT color, COUNT(*) FROM spins WHERE color != 'зелёное' GROUP BY color")
    stats['colors'] = c.fetchall()
    c.execute("SELECT parity, COUNT(*) FROM spins WHERE parity != 'ноль' GROUP BY parity")
    stats['parity'] = c.fetchall()
    c.execute("SELECT half, COUNT(*) FROM spins WHERE half != 'ноль' GROUP BY half")
    stats['half'] = c.fetchall()
    c.execute("SELECT dozen, COUNT(*) FROM spins WHERE dozen != 'ноль' GROUP BY dozen")
    stats['dozen'] = c.fetchall()
    c.execute("SELECT COUNT(*) FROM spins WHERE number = 0")
    stats['zeros'] = c.fetchone()[0]
    c.execute("SELECT number FROM spins ORDER BY id DESC LIMIT 10")
    stats['last_10'] = [r[0] for r in c.fetchall()]
    return stats

def format_stats(stats):
    if not stats: return "Нет данных."
    t = stats['total']
    txt = f"📊 Статистика\nВсего вращений: {t}\n\n🔥 Горячие:\n"
    for num, cnt in stats['top_numbers']:
        txt += f"  {num} — {cnt} раз\n"
    txt += "\n❄️ Давно не выпадали:\n"
    for num, last_id, cnt in stats['cold_numbers']:
        txt += f"  {num} — {cnt} раз\n"
    txt += f"\n🟢 Зеро: {stats['zeros']} раз\n"
    txt += "\n🎨 Цвета:\n"
    for color, cnt in stats['colors']:
        txt += f"  {color}: {cnt} ({cnt/t*100:.1f}%)\n"
    txt += "\n🔢 Чёт/нечёт:\n"
    for parity, cnt in stats['parity']:
        txt += f"  {parity}: {cnt} ({cnt/t*100:.1f}%)\n"
    txt += "\n📐 Малые/большие:\n"
    for half, cnt in stats['half']:
        txt += f"  {half}: {cnt} ({cnt/t*100:.1f}%)\n"
    txt += "\n📦 Дюжины:\n"
    for dozen, cnt in stats['dozen']:
        txt += f"  {dozen}: {cnt} ({cnt/t*100:.1f}%)\n"
    txt += f"\n🕐 Последние 10: {' → '.join(map(str, stats['last_10']))}"
    return txt

def find_patterns():
    c.execute("SELECT number FROM spins ORDER BY id DESC LIMIT 50")
    numbers = [r[0] for r in c.fetchall()]
    if len(numbers) < 10: return "Недостаточно данных."
    repeats = sum(1 for i in range(len(numbers)-1) if numbers[i] == numbers[i+1])
    switches = 0
    for i in range(len(numbers)-1):
        if get_props(numbers[i])['color'] != get_props(numbers[i+1])['color']:
            switches += 1
    max_series = cur = 1
    for i in range(len(numbers)-1):
        p1, p2 = get_props(numbers[i]), get_props(numbers[i+1])
        if p1['color'] == p2['color'] and p1['color'] != 'зелёное': cur += 1
        else: max_series, cur = max(max_series, cur), 1
    max_series = max(max_series, cur)
    return f"🔍 Закономерности ({len(numbers)} спинов):\n\nПовторы: {repeats}\nСмен цвета: {switches}\nМакс. серия цвета: {max_series}"

def extract_number_from_text(text):
    if not text: return None
    for line in text.split('\n'):
        match = re.match(r'^\s*(\d{1,2})\s*$', line.strip())
        if match:
            num = int(match.group(1))
            if 0 <= num <= 36: return num
    return None

def extract_number_from_image(file_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(file_bytes)
            tmp_path = f.name
        img = Image.open(tmp_path)
        w, h = img.size
        crops = [
            (w//2-100, h-250, w//2+100, h-30),
            (w//2-150, h-300, w//2+150, h-10),
            (0, h-200, w, h),
        ]
        for crop_area in crops:
            crop = img.crop(crop_area)
            crop_path = tmp_path + "_crop.jpg"
            crop.save(crop_path)
            result = ocr.ocr(crop_path, cls=False)
            os.unlink(crop_path)
            if result and result[0]:
                for line in result[0]:
                    text = line[1][0]
                    logging.info(f"PaddleOCR: {text}")
                    num = extract_number_from_text(text)
                    if num is not None:
                        os.unlink(tmp_path)
                        return num
        os.unlink(tmp_path)
    except Exception as e:
        logging.error(f"OCR error: {e}")
    return None

async def reply_with_result(msg, num):
    props = get_props(num)
    save_spin(num, props, msg.chat_id)
    await msg.reply_text(
        f"✅ Выпало: {num}\nЦвет: {props['color']} | {props['parity']} | {props['half']} | {props['dozen']}",
        reply_markup=main_keyboard()
    )

async def cmd_start(update, context):
    await update.message.reply_text(
        "🎰 Анализатор Бот Бандита\n\n"
        "👥 Группа: добавь меня в чат с @banditchatbot\n"
        "💬 Личка: перешли сообщение или скрин\n"
        "🔢 Или напиши число 0-36",
        reply_markup=main_keyboard()
    )

async def auto_detect(update, context):
    msg = update.message
    if not msg or not msg.from_user: return
    if (msg.from_user.username or "").lower() != TARGET_BOT: return
    if not msg.photo: return
    logging.info("Авто-захват фото...")
    file = await msg.photo[-1].get_file()
    file_bytes = await file.download_as_bytearray()
    num = extract_number_from_image(file_bytes)
    if num is not None:
        await reply_with_result(msg, num)
    else:
        logging.warning("Не распознано")

async def handle_manual(update, context):
    msg = update.message
    if not msg: return
    if msg.from_user and (msg.from_user.username or "").lower() == TARGET_BOT: return

    if msg.photo:
        logging.info("Ручной: фото")
        file = await msg.photo[-1].get_file()
        file_bytes = await file.download_as_bytearray()
        num = extract_number_from_image(file_bytes)
        if num is not None:
            await reply_with_result(msg, num)
        else:
            await msg.reply_text("❌ Не распознано. Напиши число текстом.", reply_markup=main_keyboard())
        return

    text = msg.text or msg.caption or ""
    if not text: return
    num = extract_number_from_text(text)
    if num is not None:
        await reply_with_result(msg, num)
        return
    match = re.match(r'^\s*(\d{1,2})\s*$', text.strip())
    if match:
        num = int(match.group(1))
        if 0 <= num <= 36:
            await reply_with_result(msg, num)

async def btn_stats(update, context): 
    await update.message.reply_text(format_stats(get_stats()), reply_markup=main_keyboard())
async def btn_patterns(update, context): 
    await update.message.reply_text(find_patterns(), reply_markup=main_keyboard())
async def btn_last(update, context):
    c.execute("SELECT number FROM spins ORDER BY id DESC LIMIT 10")
    nums = [str(r[0]) for r in c.fetchall()]
    await update.message.reply_text(f"Последние 10: {' → '.join(nums)}" if nums else "Нет данных.", reply_markup=main_keyboard())
async def btn_help(update, context):
    await update.message.reply_text("🎰 Бот-анализатор\n\nГруппа: добавь с @banditchatbot\nЛичка: скрин или число", reply_markup=main_keyboard())
async def btn_reset(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Только @IIarmizan.", reply_markup=main_keyboard())
        return
    c.execute("SELECT COUNT(*) FROM spins")
    count = c.fetchone()[0]
    if count == 0: 
        await update.message.reply_text("Нечего сбрасывать.", reply_markup=main_keyboard())
    else:
        kb = [[InlineKeyboardButton("✅ Да", callback_data="reset_confirm"), InlineKeyboardButton("❌ Нет", callback_data="reset_cancel")]]
        await update.message.reply_text(f"⚠️ Удалить {count} записей?", reply_markup=InlineKeyboardMarkup(kb))
async def reset_callback(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "reset_confirm":
        deleted = c.execute("SELECT COUNT(*) FROM spins").fetchone()[0]
        c.execute("DELETE FROM spins")
        conn.commit()
        await q.edit_message_text(f"✅ Удалено: {deleted}")
    else: 
        await q.edit_message_text("❌ Отмена.")

def main():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN: return
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.PHOTO & filters.User(username=TARGET_BOT), auto_detect))
    app.add_handler(MessageHandler(filters.Regex("📊 Статистика"), btn_stats))
    app.add_handler(MessageHandler(filters.Regex("🔍 Закономерности"), btn_patterns))
    app.add_handler(MessageHandler(filters.Regex("🕐 Последние 10"), btn_last))
    app.add_handler(MessageHandler(filters.Regex("🗑 Сбросить"), btn_reset))
    app.add_handler(MessageHandler(filters.Regex("❓ Помощь"), btn_help))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern="reset_"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT | filters.CAPTION, handle_manual))
    logging.info("Бот запущен с PaddleOCR.")
    app.run_polling()

if __name__ == '__main__':
    main()
