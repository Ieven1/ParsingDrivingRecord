import logging
from threading import Thread
import time
import schedule
import sqlite3
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import TELEGRAM_TOKEN, CHAT_ID, PHONE, PASSWORD

logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

DB_PATH = "schedule.db"
calendar_url = "https://www.lk.oz-avtoschool.ru/driving-record"

bot = TeleBot(TELEGRAM_TOKEN)
last_message_id = None

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            date TEXT,
            time TEXT,
            PRIMARY KEY (date, time)
        )
    """)
    conn.commit()
    conn.close()
    logging.info("База данных инициализирована")

def send_or_update_telegram_message(message, update=False):
    global last_message_id
    try:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Обновить", callback_data="update_schedule"))

        if update and last_message_id:
            try:
                bot.edit_message_text(
                    text=message,
                    chat_id=CHAT_ID,
                    message_id=last_message_id,
                    reply_markup=markup,
                    parse_mode=None
                )
                logging.info("Сообщение обновлено в Telegram")
                return
            except Exception as e:
                logging.warning(f"Ошибка обновления сообщения: {e}")

        sent = bot.send_message(CHAT_ID, message, reply_markup=markup, parse_mode=None)
        last_message_id = sent.message_id
        logging.info("Отправлено новое сообщение в Telegram")
    except Exception as e:
        logging.error(f"Ошибка при отправке в Telegram: {e}")

def fetch_schedule():
    driver = None
    try:
        options = webdriver.ChromeOptions()
        for flag in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--headless"]:
            options.add_argument(flag)

        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 10)
        driver.get(calendar_url)

        wait.until(EC.presence_of_element_located((By.ID, "student-phone"))).send_keys(PHONE)
        wait.until(EC.presence_of_element_located((By.ID, "student-password"))).send_keys(PASSWORD)
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))).click()
        logging.info("Авторизация выполнена")

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.nav-link[href='/driving-record']"))).click()
        logging.info("Открыт календарь")

        results = {}
        checked = set()

        for attempt in range(10):
            try:
                date_input = wait.until(EC.element_to_be_clickable((By.ID, "drivingschedule-date")))
                date_input.click()
                wait.until(EC.visibility_of_element_located((By.ID, "ui-datepicker-div")))
                calendar = driver.find_element(By.ID, "ui-datepicker-div")
                dates = calendar.find_elements(By.CSS_SELECTOR, "td[data-handler='selectDay'] a.ui-state-default")
                if not dates:
                    break

                for date_elem in dates:
                    dt = date_elem.text
                    if dt in checked:
                        continue
                    checked.add(dt)
                    date_elem.click()
                    try:
                        sel = wait.until(EC.element_to_be_clickable((By.ID, "drivingschedule-id_time_period")))
                        sel.click()
                        opts = sel.find_elements(By.TAG_NAME, "option")
                        times = [o.text for o in opts if o.get_attribute("value") and o.text != "Выберите время"]
                        results[dt] = times or ["Нет времени"]
                    except TimeoutException:
                        results[dt] = ["Нет времени"]
                    driver.get(calendar_url)
                    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.nav-link[href='/driving-record']"))).click()
                    break
            except (TimeoutException, StaleElementReferenceException) as e:
                logging.warning(f"Попытка {attempt+1}: ошибка при сборе расписания: {e}")
            else:
                logging.info(f"Результаты после попытки {attempt+1}: {results}")

        return results

    except Exception as e:
        logging.error(f"Ошибка fetch_schedule: {e}")
        send_or_update_telegram_message(f"Ошибка при проверке расписания:\n{e}")
        return {}
    finally:
        if driver:
            driver.quit()
            logging.info("WebDriver завершён")

def fetch_my_schedule():
    driver = None
    try:
        options = webdriver.ChromeOptions()
        for flag in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--headless"]:
            options.add_argument(flag)
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 10)

        driver.get(calendar_url)
        wait.until(EC.presence_of_element_located((By.ID, "student-phone"))).send_keys(PHONE)
        wait.until(EC.presence_of_element_located((By.ID, "student-password"))).send_keys(PASSWORD)
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))).click()
        logging.info("Авторизация для personal schedule")

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.nav-link[href='/driving-record']"))).click()
        logging.info("Открытие раздела личного расписания")

        rows = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.table-hover"))).find_elements(By.TAG_NAME, "tr")[1:]
        output = []
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 2:
                dtime = cols[0].text.split('\n')
                if len(dtime) >= 2:
                    output.append((dtime[0], dtime[1], cols[1].text))
        return output or [("Нет данных", "", "")]
    except Exception as e:
        logging.error(f"Ошибка fetch_my_schedule: {e}")
        return [("Ошибка", str(e), "")]
    finally:
        if driver:
            driver.quit()
            logging.info("WebDriver personal schedule завершён")

def check_and_notify():
    logging.info("Запуск check_and_notify")
    new_results = fetch_schedule()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT date, time FROM schedule")
    old = {(r[0], r[1]) for r in cur.fetchall()}
    new = {(d, t) for d, times in new_results.items() for t in times if t != "Нет времени"}

    added = new - old
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    def format_date(d_str):
        try:
            day = int(d_str)
            today = datetime.now()
            return f"{day:02d}.{today.month:02d}.{today.year}"
        except:
            return d_str

    if added:
        msg = f"Новые слоты ({now}):\n"
        msg += "\n".join(f"{format_date(d)} — {t}" for d, t in sorted(added))
        send_or_update_telegram_message(msg)

    cur.execute("DELETE FROM schedule")
    for d, times in new_results.items():
        for t in times:
            if t != "Нет времени":
                cur.execute("INSERT INTO schedule (date, time) VALUES (?, ?)", (d, t))
    conn.commit()
    conn.close()

    summary = f"Расписание ({now}):\n"
    if new_results:
        summary += "\n".join(f"{format_date(d)} — {', '.join(ts)}" for d, ts in sorted(new_results.items()))
    else:
        summary += "Нет доступных слотов"
    send_or_update_telegram_message(summary, update=True)

def run_bot():
    while True:
        try:
            logging.info("Запуск bot.polling()")
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Bot polling error: {e}")
            time.sleep(10)

def run_scheduler():
    schedule.every(6).hours.do(check_and_notify)
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logging.error(f"Scheduler error: {e}")
            time.sleep(10)

@bot.message_handler(commands=['update'])
def handle_update(message):
    if str(message.chat.id) != CHAT_ID:
        bot.reply_to(message, "Доступ запрещён")
        return
    bot.reply_to(message, "Обновляю расписание...")
    Thread(target=check_and_notify).start()

@bot.message_handler(commands=['myschedule'])
def handle_my_schedule(message):
    if str(message.chat.id) != CHAT_ID:
        bot.reply_to(message, "Доступ запрещён")
        return
    data = fetch_my_schedule()
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    if data and data[0][0] not in ["Ошибка", "Нет данных"]:
        text = f"Ваше расписание ({now}):\n\n" + "\n".join(f"{d} {t} — {loc}" for d, t, loc in sorted(data))
    else:
        text = f"Нет запланированных занятий ({now})"
    bot.send_message(message.chat.id, text, parse_mode=None)

@bot.callback_query_handler(func=lambda c: c.data == "update_schedule")
def handle_update_button(call):
    if str(call.message.chat.id) != CHAT_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён")
        return
    bot.answer_callback_query(call.id, "Обновляю...")
    Thread(target=check_and_notify).start()

if __name__ == "__main__":
    init_db()
    Thread(target=run_bot, daemon=True).start()
    Thread(target=run_scheduler, daemon=True).start()

    while True:
        time.sleep(60)
