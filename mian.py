from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import sqlite3
import time
import requests
import telebot
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
import schedule
import os
from datetime import datetime
from threading import Thread
from config import TELEGRAM_TOKEN, CHAT_ID, PHONE, PASSWORD

calendar_url = "https://www.lk.oz-avtoschool.ru/driving-record"
DB_PATH = "schedule.db"

state_storage = StateMemoryStorage()
bot = telebot.TeleBot(TELEGRAM_TOKEN, state_storage=state_storage)

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


def send_telegram_message(message):
    try:
        bot.send_message(CHAT_ID, message)
        print("Сообщение отправлено в Telegram")
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")


def fetch_schedule():
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--headless") 
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 10)

    results = {}
    try:

        driver.get(calendar_url)
        wait.until(EC.presence_of_element_located((By.ID, "student-phone"))).send_keys(PHONE)
        wait.until(EC.presence_of_element_located((By.ID, "student-password"))).send_keys(PASSWORD)
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))).click()
        print("Авторизация выполнена")

        calendar_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.nav-link[href='/driving-record']")))
        calendar_link.click()
        print("Открыт календарь")


        checked_dates = set()
        max_attempts = 10
        attempt = 0

        while attempt < max_attempts:
            attempt += 1

            try:
                date_input = wait.until(EC.element_to_be_clickable((By.ID, "drivingschedule-date")))
                date_input.click()
                wait.until(EC.visibility_of_element_located((By.ID, "ui-datepicker-div")))
            except TimeoutException:
                print("Календарь не найден")
                break


            try:
                calendar = wait.until(EC.presence_of_element_located((By.ID, "ui-datepicker-div")))
                available_dates = calendar.find_elements(By.CSS_SELECTOR, "td[data-handler='selectDay'] a.ui-state-default")
                if not available_dates:
                    print("Даты не найдены")
                    break

                print(f"Найдено {len(available_dates)} дат")
                found_new_date = False
                for date in available_dates:
                    try:
                        date_text = date.text
                        if date_text in checked_dates:
                            continue
                        found_new_date = True
                        print(f"Проверяем дату: {date_text}")
                        checked_dates.add(date_text)

                        # Кликаем по дате
                        date.click()

                        # Проверяем время
                        try:
                            time_select = wait.until(EC.element_to_be_clickable((By.ID, "drivingschedule-id_time_period")))
                            time_select.click()
                            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#drivingschedule-id_time_period option[value]:not([value=''])")))
                            options = time_select.find_elements(By.TAG_NAME, "option")
                            available_times = [option.text for option in options if option.get_attribute("value") and option.text != "Выберите время"]
                            results[date_text] = available_times if available_times else ["Нет времени"]
                        except TimeoutException:
                            results[date_text] = ["Нет времени"]


                        driver.get(calendar_url)
                        wait.until(EC.element_to_be_clickable((By.ID, "drivingschedule-date")))
                        break
                    except StaleElementReferenceException:
                        print(f"Дата {date_text} устарела, перезагружаем...")
                        driver.get(calendar_url)
                        break

                if not found_new_date or len(checked_dates) >= len(available_dates):
                    print("Все даты проверены")
                    break

            except TimeoutException:
                print("Календарь не загрузился")
                break

    except Exception as e:
        print(f"Ошибка: {e}")
        send_telegram_message(f"Ошибка при проверке расписания: {str(e)}")
    finally:
        driver.quit()

    return results


def check_and_notify():
    new_results = fetch_schedule()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()


    cursor.execute("SELECT date, time FROM schedule")
    old_schedule = {(row[0], row[1]) for row in cursor.fetchall()}


    new_schedule = {(date, time) for date, times in new_results.items() for time in times if time != "Нет времени"}


    new_slots = new_schedule - old_schedule
    if new_slots:
        message = "Новые слоты для вождения:\n"
        for date, time in new_slots:
            message += f"{date} июня: {time}\n"
        send_telegram_message(message)


    cursor.execute("DELETE FROM schedule")
    for date, times in new_results.items():
        for time in times:
            if time != "Нет времени":
                cursor.execute("INSERT INTO schedule (date, time) VALUES (?, ?)", (date, time))
    conn.commit()
    conn.close()


    message = "Расписание вождения:\n"
    for date, times in new_results.items():
        time_str = ", ".join(times) if times != ["Нет времени"] else "Нет времени"
        message += f"{date} июня: {time_str}\n"
    send_telegram_message(message)


@bot.message_handler(commands=['update'])
def handle_update(message):
    if str(message.chat.id) != CHAT_ID:
        bot.reply_to(message, "Доступ запрещен")
        return
    bot.reply_to(message, "Обновляю расписание...")
    check_and_notify()


def run_bot():
    bot.polling(none_stop=True)

def run_scheduler():
    schedule.every(30).minutes.do(check_and_notify)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    init_db()
    Thread(target=run_bot).start()
    Thread(target=run_scheduler).start()