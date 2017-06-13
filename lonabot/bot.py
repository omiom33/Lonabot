#!/usr/bin/env python3.6
# -*- coding: utf-8 -*-
#
# Reminder bot
from telegram.ext import Updater, CommandHandler
from telegram.ext import MessageHandler, Filters
from telegram import ParseMode

from datetime import datetime, timedelta, time
import logging
import os
import re


# Constants
MAX_REMINDERS = 10
MAX_DATA_PER_REMINDER_BYTES = 256

REMINDIN_RE = re.compile(r'''
^
(?:  # First option

    (                # # MATCH 1: 'hh:mm:ss' or 'mm:ss'
        \d+          # Minutes or hours
        \s*:\s*      # ':'
        \d+          # Seconds or minutes
        (?:
            \s*:\s*  # ':'
            \d+      # Seconds, then the others are hours:minutes
        )?
    )

)
|
(?:  # Second option

    (                # # # MATCH 2: 'uu' for "units"
        \d+          # Which are a few digits
        (?:
            [,.]\d+  # Possibly capture a value after the decimal point
        )?
    )
    \s*              # Some people like to separate units from the number

    (                # # # MATCH 3: '(d|h|m|s).*' for days, hours, mins, secs
        d(?:ays?)?         # Either 'd', 'day' or 'days'
        |                  # or
        h(?:ours?)?        # Either 'h', 'hour' or 'hours'
        |                  # or
        m(?:in(?:ute)?s?)? # Either 'm', 'min', 'mins', 'minute' or 'minutes'
        |                  # or
        s(?:ec(?:ond)?s?)? # Either 's', 'sec', 'secs', 'second' or 'seconds'
    )?

)\b''', re.IGNORECASE | re.VERBOSE)


REMINDAT_RE = re.compile(r'''
^
(?:  # First option

    (                 # # MATCH 1: 'hh' or 'hh:mm' or 'hh:mm:ss'
        \d+           # Hours
        (?:
            :\d+      # Possibly minutes
            (?:
                :\d+  # Possibly seconds
            )?
        )?
    )
    \s*               # Some people like to separate am/pm from the time

    (?:               # Don't capture the whole am/pm stuff
        (?:           # Don't capture the p|a options

            (p)       # # MATCH 2: Only capture 'p', because that's when +12h
            |         # Either 'p' or
            a         # 'a'

        )
        m        # It is either 'am' or 'pm'
    )?

)\b''', re.IGNORECASE | re.VERBOSE)


# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)


# Utilities
def from_admin(update):
    """Is this update from the admin?"""
    return update.message.from_user.id == 10885151


def parsehour(text, reverse):
    """Small utility to parse hours (18:05), or optionally reversing
       it to first detect the seconds, then minutes, then hours.
    """
    parts = text.split(':')
    if len(parts) > 3:
        parts = parts[-3:]

    due = 0
    try:
        if reverse:
            for u, p in zip((1, 60, 3600), reversed(parts)):
                due += u * int(p)
        else:
            for u, p in zip((3600, 60, 1), parts):
                due += u * int(p)

        return due
    except ValueError:
        pass     


def format_time_diff(to_date):
    """Formats the time difference between now and to_date"""
    diff = str(to_date - datetime.now())
    if '.' in diff:
        diff = diff[:diff.index('.')]
    return diff


def get_user_dir(bot, chat_id):
    """Gets the directory for 'chat_id' and creates it if necessary."""
    directory = os.path.join(bot.username, str(chat_id))
    os.makedirs(directory, exist_ok=True)
    return directory


def queue_message(job_queue, due, chat_id, reminder_file):
    """Queues a message reminder on 'job_queue' which will be
       sent on 'due' at the specified 'chat_id', reading and
       deleting the given 'reminder_file' after sent.
    """
    context = {
        'chat_id': chat_id,
        'reminder_file': reminder_file
    }
    job_queue.run_once(notify, due, context=context)


def create_reminder(bot, job_queue, chat_id, due, text):
    """Creates a reminder for 'chat_id' with the desired 'text'
       and queues its message, or does nothing if the quota exceeded.
    """
    directory = get_user_dir(bot, chat_id)
    if len(os.listdir(directory)) >= MAX_REMINDERS or \
            len(text.encode('utf-8')) > MAX_DATA_PER_REMINDER_BYTES:
        bot.send_message(chat_id, text='Quota exceeded. You cannot set more!')
        return

    if (due - datetime.now()) < timedelta(seconds=5):
        bot.send_message(chat_id, text="Uhm… that's pretty much right now cx")
        return

    out = os.path.join(directory, str(int(due.timestamp())))
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text.strip())

    queue_message(job_queue, due, chat_id, reminder_file=out)
    diff = format_time_diff(due)
    bot.send_message(chat_id, text='I will remind you "{}" in {} :)'
                                   .format(text, diff))


def notify(bot, job):
    """Notifies by sending a message that a reminder due date is over"""
    chat_id = job.context['chat_id']
    reminder_file = job.context['reminder_file']

    with open(reminder_file) as f:
        text = f.read()

    if os.path.isfile(reminder_file):
        os.remove(job.context['reminder_file'])

    bot.send_message(chat_id, text=text if text else 'Time is over!')


def load_jobs(bot, job_queue):
    """Load all existing jobs (pending reminders) into the given
       'job_queue', and apologise if we missed any.
    """
    if not os.path.isdir(bot.username):
        return

    now = datetime.now()
    for chat_id in os.listdir(bot.username):
        apologise = False

        for reminder in os.listdir(get_user_dir(bot, chat_id)):
            reminder_file = os.path.join(bot.username, chat_id, reminder)
            reminder_date = datetime.fromtimestamp(int(reminder))

            if reminder_date > now:
                queue_message(job_queue, reminder_date,
                              int(chat_id), reminder_file)
            else:
                apologise = True
                os.remove(reminder_file)

        if apologise:
            bot.send_message(chat_id,
                text='Oops… looks like I missed some reminders. Sorry :(')


# Commands
def start(bot, update):
    update.message.reply_text('''Hi! I'm {} and running in "reminder" mode.

You can set reminders by using:
`/remindat 17:05 Optional text`
`/remindin    5m Optional text`

Or list those you have by using:
`/status`

Everyone is allowed to use {}KB per reminder, and {} reminders max. No more!

Made with love by @Lonami and hosted by Richard ❤️'''
    .format(bot.first_name.title(),
            MAX_DATA_PER_REMINDER_BYTES / 1024, MAX_REMINDERS),
    parse_mode=ParseMode.MARKDOWN)


def restart(bot, update):
    if not from_admin(update):
        return

    import os
    import time
    import sys
    update.message.reply_text('Restarting {}…'.format(bot.first_name.title()))
    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)  


def clear(bot, update, args, job_queue):
    chat_id = update.message.chat_id
    directory = get_user_dir(bot, chat_id)
    reminders = list(os.listdir(directory))
    if not reminders:
        update.message.reply_text('You have no reminders to clear dear :)')
        return

    if not args:
        update.message.reply_text(
            'Are you sure you want to clear {} reminders? Please type '
            '`/clear please` if you are totally sure!'
            .format(len(reminders)), parse_mode=ParseMode.MARKDOWN)
        return

    if args[0].lower() == 'please':
        for job in job_queue.jobs():
            if job.context['chat_id'] == chat_id:
                job.schedule_removal()

        for r in reminders:
            os.remove(os.path.join(directory, r))

        update.message.reply_text('You are now free! No more reminders :3')
    else:
        update.message.reply_text(
            '"{}" is not what I asked you to send xP'.format(args[0]))
        


def remindin(bot, update, args, job_queue):
    if not args:
        update.message.reply_text('In when? :p')
        return

    args = ' '.join(args)
    m = REMINDIN_RE.search(args)
    if m is None:
        update.message.reply_text(
            'Not sure what time you meant that to be! :s')
        return
    
    if m.group(1):
        due = parsehour(m.group(1), reverse=True)

    elif m.group(2):
        due = float(m.group(2))
        unit = m.group(3)[0].lower() if m.group(3) else 'm'
        due *= {'s': 1,
                'm': 60,
                'h': 3600,
                'd': 86400}.get(unit, 60)

    else:
        update.message.reply_text('Darn, my regex broke >.<')
        return

    due = datetime.now() + timedelta(seconds=due)
    text = args[m.end():].strip()

    create_reminder(bot, job_queue, update.message.chat_id, due, text)


def remindat(bot, update, args, job_queue):
    if not args:
        update.message.reply_text('At what time? :p')
        return

    args = ' '.join(args)
    m = REMINDAT_RE.search(args)
    if m is None:
        update.message.reply_text(
            'Not sure what time you meant that to be! :s')
        return

    if m.group(1):
        due = parsehour(m.group(1), reverse=False)
        if m.group(2) is not None:  # PM
            due += 43200  # 12h * 60m * 60s

    else:
        update.message.reply_text('Darn, my regex broke >.<')
        return

    m, s = divmod(due, 60)
    h, m = divmod(  m, 60)
    try:
        due = time(h, m, s)
        now = datetime.now()
        now_time = time(now.hour, now.minute, now.second)

        add_days = 1 if due < now_time else 0
        due = datetime(now.year, now.month, now.day + add_days,
                       due.hour, due.minute, due.second)

        text = ' '.join(args[1:])
        create_reminder(bot, job_queue, update.message.chat_id, due, text)
    except ValueError:
        update.message.reply_text('Some values are out of bounds :o')
        return


def status(bot, update):
    directory = get_user_dir(bot, update.message.chat_id)
    reminders = list(sorted(os.listdir(directory)))
    if not reminders:
        update.message.reply_text('You have no pending reminders. Hooray ^_^')
        return

    reminder = reminders[0]
    diff = format_time_diff(datetime.fromtimestamp(int(reminder)))

    with open(os.path.join(directory, reminder)) as f:
        text = f.read()

    text = ':\n' + text if text else '.'
    amount = ('{} reminders' if len(reminders) > 1 else '{} reminder')\
             .format(len(reminders))

    update.message.reply_text('{}. Next reminder in {}{}'
                              .format(amount, diff, text))


if __name__ == '__main__':
    token = '328334925:AAFuWZSNZJWP4QWyJM6q7iub5vp-A_wPDnI'
    updater = Updater(token)

    dp = updater.dispatcher
    dp.add_handler(CommandHandler(
        'start', start
    ))
    dp.add_handler(CommandHandler(
        'restart', restart
    ))
    dp.add_handler(CommandHandler(
        'clear', clear, pass_args=True, pass_job_queue=True
    ))
    dp.add_handler(CommandHandler(
        'status', status
    ))
    dp.add_handler(CommandHandler(
        'remindin', remindin, pass_args=True, pass_job_queue=True
    ))
    dp.add_handler(CommandHandler(
        'remindat', remindat, pass_args=True, pass_job_queue=True
    ))

    updater.bot.getMe()
    load_jobs(updater.bot, updater.job_queue)

    updater.start_polling()
    updater.idle()
