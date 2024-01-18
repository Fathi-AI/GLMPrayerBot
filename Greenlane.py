# -*- coding: utf-8 -*-
"""
Created on Fri Nov 17 18:40:07 2023

@author: FathiHassan(NHSSouth
"""

import requests
import logging
import pandas as pd
from pytz import timezone
from config import TELEGRAM_BOT_TOKEN
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, JobQueue, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import ParseMode


# Telegram configuration
telegram_bot_token = TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def log_command_usage(command_name, chat_id):
    now = datetime.now()
    date_today = now.date()  # Get the current date
    hour_of_day = now.hour   # Get the hour of the day
    df = pd.DataFrame([[date_today, hour_of_day, command_name, chat_id]])
    df.to_csv('command_usage.csv', mode='a', header=False, index=False)


def load_subscribers():
    try:
        df = pd.read_csv('subscribers.csv', header=None)
        return set(df[0])
    except FileNotFoundError:
        return set()
    except Exception as e:
        print(f"An error occurred: {e}")
        return set()

subscribers = load_subscribers()

prayer_times = {}


def scrape_prayer_times(jq: JobQueue):
    global prayer_times
    url = 'https://greenlanemasjid.org/'
    prayer_emojis = {
        "Fajr": "🌄",
        "Dhuhr": "🌞",
        "Asr": "🌤",
        "Maghrib": "🌇",
        "Isha": "🌙"
    }

    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        prayer_times_table = soup.find('table')
        if not prayer_times_table:
            raise ValueError("Prayer times table not found on the page")

        rows = prayer_times_table.find_all('tr')

        prayer_times_dict = {}

        # Inside the scrape_prayer_times function
        for row in rows:
            columns = row.find_all('td')
            if len(columns) > 3 and 'prayer_time' in columns[0].get('class', []):
                prayer_name = columns[0].get_text().strip()
                start_time = columns[2].get_text().strip()
                jamat_time = columns[3].get_text().strip() if prayer_name.lower() not in ['sunrise', 'maghrib'] else ""
                emoji = prayer_emojis.get(prayer_name, "🕌")
                prayer_times_dict[prayer_name] = {'start': start_time, 'jamat': jamat_time, 'emoji': emoji}


        prayer_times = prayer_times_dict

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching prayer times from {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Error processing prayer times data: {e}")
        return None

    if prayer_times:
        # Create a DataFrame from the scraped data for CSV (without emojis)
        current_date = datetime.now().date()
        csv_data = {prayer: times['start'] for prayer, times in prayer_times.items()}
        new_data_df = pd.DataFrame([csv_data], columns=csv_data.keys())
        new_data_df.insert(0, 'date', current_date)

        # Read the existing CSV file
        try:
            existing_data_df = pd.read_csv('daily_prayer_times.csv')
        except FileNotFoundError:
            # If the file doesn't exist, just write the new data
            new_data_df.to_csv('daily_prayer_times.csv', index=False)
        else:
            # Check if there's a row with the current date
            if current_date in existing_data_df['date'].values:
                # Replace the existing row
                existing_data_df = existing_data_df[existing_data_df['date'] != current_date]
            updated_df = pd.concat([existing_data_df, new_data_df])

            # Write the updated DataFrame to the CSV
            updated_df.to_csv('daily_prayer_times.csv', index=False)

        # Continue with scheduling notifications
        schedule_prayer_notifications(jq)
    return prayer_times



def get_button_layout(chat_id):
    # Check subscription status
    is_subscribed = chat_id in subscribers

    # Prepare the buttons
    button_list = [
        [InlineKeyboardButton("Start", callback_data='start'),
         InlineKeyboardButton("Today's Prayer Times", callback_data='today')],
        [InlineKeyboardButton("Next Prayer", callback_data='nextprayer')]
    ]
    subscription_button = InlineKeyboardButton("Stop Notifications", callback_data='stop') if is_subscribed else InlineKeyboardButton("Notify Me!", callback_data='notify')
    button_list[1].append(subscription_button)

    return InlineKeyboardMarkup(button_list)

def get_next_prayer():
    global prayer_times
    now = datetime.now()
    min_diff = timedelta(days=1)
    next_prayer = None
    next_prayer_time = None
    next_prayer_jamat = None 

    if not prayer_times:
        return None, None, None

    for prayer, times in prayer_times.items():
        if prayer.lower() != 'sunrise':
            prayer_time_str = times['start']
            prayer_time = datetime.strptime(prayer_time_str, '%I:%M %p').replace(year=now.year, month=now.month, day=now.day)
            if now < prayer_time < now + min_diff:
                min_diff = prayer_time - now
                next_prayer = prayer
                next_prayer_time = prayer_time
                next_prayer_jamat = times['jamat'] 

    return next_prayer, next_prayer_time, next_prayer_jamat

    

def format_timedelta(td):
    total_seconds = int(td.total_seconds())
    # Round up to the next minute if there are any remaining seconds
    if total_seconds % 60 > 0:
        total_seconds += 60

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0 and minutes > 0:
        return f"{hours} hour{'s' if hours > 1 else ''} and {minutes} min{'s' if minutes > 1 else ''}"
    elif hours > 0:
        return f"{hours} hour{'s' if hours > 1 else ''}"
    else:
        return f"{minutes} min{'s' if minutes > 1 else ''}"
    
def start(update: Update, context: CallbackContext):
    is_callback_query = update.callback_query is not None
    chat_id = update.callback_query.message.chat_id if is_callback_query else update.message.chat_id
    
    log_command_usage('start', chat_id)
    
    send_image(update, context)
    
    if is_callback_query:
        update.callback_query.answer()

    # Get the button layout
    reply_markup = get_button_layout(chat_id)
    
    welcome_message = (
        "Assalamu Aleykum! 🌙\n\n"
        "Welcome to the Prayer Times Bot! 🕌\n\n"
        "This bot provides prayer times from Green Lane Masjid. You can easily interact with me using the buttons below. Here are some things you can do:\n\n"
        "👉 Check today's prayer times\n"
        "👉 Find out the time remaining until the next prayer\n"
        "👉 Subscribe or unsubscribe from real-time prayer notifications\n\n"
        "Just tap on one of the buttons below to get started! 🤖"
    )
    
    # Use the appropriate method to send the reply
    reply_method = update.callback_query.message.reply_text if is_callback_query else update.message.reply_text
    reply_method(welcome_message, reply_markup=reply_markup)
    


def send_image(update: Update, context: CallbackContext):
    # Determine if the update is from a callback query
    is_callback_query = update.callback_query is not None

    # Get the chat_id based on the type of update
    chat_id = update.callback_query.message.chat_id if is_callback_query else update.message.chat_id

    # The rest of your function remains the same
    image_url = 'https://images.app.goo.gl/JaxSeAuX8GBLY9aH6'
    context.bot.send_photo(chat_id=chat_id, photo=image_url)


def next_prayer(update: Update, context: CallbackContext):
    is_callback_query = update.callback_query is not None
    chat_id = update.callback_query.message.chat_id if is_callback_query else update.message.chat_id
    
    log_command_usage('nextprayer', chat_id)
    
    if is_callback_query:
        update.callback_query.answer()

    global prayer_times
    
    next_prayer, prayer_time, jamat_time  = get_next_prayer()
    now = datetime.now()
    
    if not prayer_times:
        reply_method = update.callback_query.message.reply_text if is_callback_query else update.message.reply_text
        reply_method("Sorry, I'm unable to fetch prayer times right now. Please try again later.")
        return

    if next_prayer:
        emoji = prayer_times[next_prayer]['emoji']
        time_remaining = prayer_time - datetime.now()
        formatted_time_remaining = format_timedelta(time_remaining)
        message = f"{emoji} Next prayer is {next_prayer} at {prayer_time.strftime('%I:%M %p')}. Time remaining: {formatted_time_remaining}"
        if next_prayer.lower() != 'maghrib' and jamat_time:  # Add Jamat time for non-Maghrib prayers
            message += f"\n\nJamat is at {jamat_time}."
    
    else:
        # Calculate time until Fajr of the next day
        fajr_time_str = prayer_times['Fajr']['start']
        fajr_time_tomorrow = datetime.strptime(fajr_time_str, '%I:%M %p') + timedelta(days=1)
        fajr_time_tomorrow = fajr_time_tomorrow.replace(year=now.year, month=now.month, day=now.day + 1)
        time_until_fajr = fajr_time_tomorrow - datetime.now()
        formatted_time_until_fajr = format_timedelta(time_until_fajr)
        message = f"🌙 The start time for Isha has passed. \nNext is Fajr in roughly {formatted_time_until_fajr}. See you tomorrow for Fajr, in shaa Allah!"
    
    # Get the button layout
    reply_markup = get_button_layout(chat_id)
    
    # Use the appropriate method to send the reply
    reply_method = update.callback_query.message.reply_text if is_callback_query else update.message.reply_text
    reply_method(message, reply_markup=reply_markup)



def today_prayers(update: Update, context: CallbackContext):
    is_callback_query = update.callback_query is not None
    chat_id = update.callback_query.message.chat_id if is_callback_query else update.message.chat_id
    log_command_usage('today', chat_id)
    
    if is_callback_query:
        update.callback_query.answer()

    global prayer_times
    message = "<b>Today's Prayer Times:</b>\n\n"
    
    if not prayer_times:
        update.message.reply_text("Sorry, I'm unable to fetch today's prayer times right now. Please try again later.")
        return
    
    for prayer, details in prayer_times.items():
        if prayer.lower() != 'sunrise':  # Exclude sunrise if needed
            emoji = details['emoji']
            start_time = details['start']
            jamat_time = details['jamat']
            message += f"<b>{emoji} {prayer}:</b>\n   Start: {start_time}\n"
            if jamat_time:
                message += f"   Jamat: {jamat_time}\n"
            message += "\n"
            
    # Get the button layout
    reply_markup = get_button_layout(chat_id)
    
    # Use the appropriate method to send the reply
    reply_method = update.callback_query.message.reply_text if is_callback_query else update.message.reply_text
    reply_method(message, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

    
def log_subscriber_change(action, chat_id):
    now = datetime.now()
    date_today = now.date()  # Get the current date
    df = pd.DataFrame([[date_today, action, chat_id]])
    df.to_csv('subscriber_changes.csv', mode='a', header=False, index=False)
    
    
def subscribe(update: Update, context: CallbackContext):
    # Determine if the function is called from a command or a button press
    is_callback_query = update.callback_query is not None

    # Get the chat_id depending on the type of update
    chat_id = update.callback_query.message.chat_id if is_callback_query else update.message.chat_id

    global subscribers
    
    if chat_id not in subscribers:
        df = pd.DataFrame([chat_id])
        df.to_csv('subscribers.csv', mode='a', header=False, index=False)
        subscribers.add(chat_id)
        reply_text = "📣✅🕌 - You've subscribed to prayer time notifications."
    else:
        reply_text = "You are already subscribed."
        
    # Get the button layout
    reply_markup = get_button_layout(chat_id)

    # Reply using the appropriate method
    if is_callback_query:
        update.callback_query.message.reply_text(reply_text, reply_markup = reply_markup)
    else:
        update.message.reply_text(reply_text, reply_markup = reply_markup)
        
    log_subscriber_change('subscribe', chat_id)



def stop(update: Update, context: CallbackContext):
    # Determine if the function is called from a command or a button press
    is_callback_query = update.callback_query is not None

    # Get the chat_id depending on the type of update
    chat_id = update.callback_query.message.chat_id if is_callback_query else update.message.chat_id

    global subscribers
    
    if chat_id in subscribers:
        df = pd.read_csv('subscribers.csv', header=None)
        df = df[df[0] != chat_id]
        df.to_csv('subscribers.csv', index=False, header=False)
        subscribers.remove(chat_id)
        reply_text = "🔕❌🕌 - You've unsubscribed from prayer time notifications."
    else:
        reply_text = "You are not currently subscribed."
    
    # Get the button layout
    reply_markup = get_button_layout(chat_id)
    
    # Reply using the appropriate method
    reply_method = update.callback_query.message.reply_text if is_callback_query else update.message.reply_text
    reply_method(reply_text, reply_markup=reply_markup)
    
    log_subscriber_change('unsubscribe', chat_id)


    
def check_prayer_times(context: CallbackContext):
    global subscribers
    prayer_name = context.job.context['prayer_name']
    emoji = context.job.context['emoji']
    jamat_time = context.job.context['jamat']
    message = f"It's time for {prayer_name} prayer. {emoji}"

     # Append Jamat time if it's not Maghrib
    if prayer_name.lower() != 'maghrib' and jamat_time:
        message += f" Jamat at {jamat_time}."

    for chat_id in subscribers:
        reply_markup = get_button_layout(chat_id)
        context.bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)

        
def schedule_prayer_notifications(jq: JobQueue):
    # Clear existing jobs related to prayer notifications
    for job in jq.jobs():
        if job.name == 'prayer_notification':
            job.remove()

    # Schedule new notifications
    global prayer_times
    now = datetime.now()
    for prayer, times in prayer_times.items():
        if prayer.lower() != 'sunrise':
            prayer_time_str = times['start']
            emoji = times['emoji']
            jamat_time = times['jamat']
            prayer_time = datetime.strptime(prayer_time_str, '%I:%M %p').replace(year=now.year, month=now.month, day=now.day)
            if prayer_time > now:
                jq.run_once(check_prayer_times, prayer_time, context={'prayer_name': prayer, 'prayer_time': prayer_time, 'emoji': emoji, 'jamat': jamat_time}, name='prayer_notification')




                
def setup_scheduler(jq: JobQueue):
    # Set the scheduler to use UK time
    uk_timezone = timezone('Europe/London')
    scheduler = BackgroundScheduler(timezone=uk_timezone)

    scheduler.add_job(lambda: scrape_prayer_times(jq), 'cron', hour=0, minute=1)
    scheduler.add_job(lambda: scrape_prayer_times(jq), 'cron', hour=1, minute=30)
    scheduler.start()
    
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()  # Acknowledge the callback query

    logging.info(f"Button pressed: {query.data}")  # Debugging log

    # Check the callback_data and call the appropriate function
    if query.data == 'start':
        start(update, context)
    elif query.data == 'today':
        today_prayers(update, context)
    elif query.data == 'nextprayer':
        next_prayer(update, context)
    elif query.data == 'notify':
        subscribe(update, context)
    elif query.data == 'stop':
        stop(update, context)
    else:
        logging.warning(f"Unhandled callback data: {query.data}")  # Debugging log for unhandled cases


def main():
    updater = Updater(telegram_bot_token, use_context=True)
    dp = updater.dispatcher
    jq = updater.job_queue
    scrape_prayer_times(jq)  # Initial scraping
    setup_scheduler(jq)  # Pass the job queue to the scheduler setup
    
    # Register handlers
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("nextprayer", next_prayer))
    dp.add_handler(CommandHandler("today", today_prayers))
    dp.add_handler(CommandHandler("notify", subscribe))
    dp.add_handler(CommandHandler("stop", stop))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()