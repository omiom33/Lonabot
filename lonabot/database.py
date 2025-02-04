import collections
import sqlite3
import threading

import pytz

from dataclasses import dataclass

DB_VERSION = 8


Reminder = collections.namedtuple(
    'Reminder', 'id chat_id due text reply_to creator_id file_type file_id')

Birthday = collections.namedtuple(
    'Birthday', 'id creator_id month day person_id person_name year_reminded remind_stage')

@dataclass
class TimeDelta:
    delta: int
    time_zone: str

    def pytz(self):
        if self.time_zone is None:
            return None
        return pytz.timezone(self.time_zone)

class Database:
    def __init__(self, filename):
        self._filename = filename
        self._conns = {}

        c = self._cursor()
        c.execute("SELECT name FROM sqlite_master "
                  "WHERE type='table' AND name='Version'")

        if c.fetchone():
            c.execute('SELECT Version FROM Version')
            version = c.fetchone()[0]
            if version != DB_VERSION:
                self._set_version(c, drop=True)
                self._upgrade_database(old=version)
                self._save()
        else:
            self._set_version(c, drop=False)

            c.execute('CREATE TABLE TimeDelta('
                      'UserID INTEGER PRIMARY KEY,'
                      'Delta INTEGER NOT NULL,'
                      'TimeZone TEXT NULL)')

            c.execute('CREATE TABLE Later('
                      'UserID INTEGER PRIMARY KEY,'
                      'Delta INTEGER NOT NULL)')

            c.execute('CREATE TABLE Reminders('
                      'ID INTEGER PRIMARY KEY AUTOINCREMENT,'
                      'ChatID INTEGER NOT NULL,'
                      'Due TIMESTAMP NOT NULL,'
                      'Text TEXT NOT NULL,'
                      'ReplyTo INTEGER,'
                      'CreatorID INTEGER NOT NULL,'
                      'FileType TEXT,'
                      'FileID TEXT)')

            c.execute('CREATE TABLE Birthdays('
                      'ID INTEGER PRIMARY KEY AUTOINCREMENT,'
                      'CreatorID INTEGER NOT NULL,'
                      'Month INTEGER NOT NULL,'
                      'Day INTEGER NOT NULL,'
                      'PersonID INTEGER,'
                      'PersonName TEXT,'
                      'YearReminded INTEGER,'  # last year we reminded this (to not remind something twice)
                      'RemindStage INTEGER)')  # what did we remind on this year? (pre-day, or current day)

            self._save()
        c.close()

    @staticmethod
    def _set_version(c, *, drop):
        if drop:
            c.execute('DROP TABLE Version')

        c.execute('CREATE TABLE Version (Version INTEGER)')
        c.execute('INSERT INTO Version VALUES (?)', (DB_VERSION,))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for conn in self._conns.values():
            try:
                conn.close()
            except:
                pass
        self._conns.clear()

    def _save(self):
        conn = self._conns.get(threading.get_ident())
        if conn:
            conn.commit()

    def _cursor(self):
        conn = self._conns.get(threading.get_ident())
        if conn is None:
            self._conns[threading.get_ident()] = conn =\
                sqlite3.connect(self._filename)
        return conn.cursor()

    def _upgrade_database(self, old):
        c = self._cursor()
        if old == 1:
            c.execute('ALTER TABLE Reminders ADD ReplyTo INTEGER')
            old = 2
        if old == 2:
            c.execute('ALTER TABLE Reminders ADD CreatorID INTEGER '
                      'NOT NULL DEFAULT 0')
            old = 3
        if old == 3:
            c.execute('CREATE TABLE Birthdays('
                      'ID INTEGER PRIMARY KEY AUTOINCREMENT,'
                      'CreatorID INTEGER NOT NULL,'
                      'Month INTEGER NOT NULL,'
                      'Day INTEGER NOT NULL,'
                      'PersonID INTEGER,'
                      'PersonName TEXT)')
            old = 4
        if old == 4:
            c.execute('ALTER TABLE Reminders ADD FileType TEXT')
            c.execute('ALTER TABLE Reminders ADD FileID TEXT')
            old = 5
        if old == 5:
            c.execute('ALTER TABLE Birthdays ADD YearReminded INTEGER')
            c.execute('ALTER TABLE Birthdays ADD RemindStage INTEGER')
            old = 6
        if old == 6:
            c.execute('ALTER TABLE TimeDelta ADD TimeZone TEXT NULL')
            old = 7
        if old == 7:
            c.execute('CREATE TABLE Later('
                      'UserID INTEGER PRIMARY KEY,'
                      'Delta INTEGER NOT NULL)')

        c.close()

    def add_reminder(self, *, update, due, text, file_type, file_id, reply_id):
        c = self._cursor()
        m = update.message
        c.execute(
            'INSERT INTO Reminders '
            '(ChatID, CreatorID, Due, Text, FileType, FileID, ReplyTo) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (m.chat.id, m.from_.id, due, text.strip(), file_type, file_id, reply_id)
        )
        new_id = c.lastrowid
        c.close()
        self._save()
        return new_id

    def get_reminder_count(self, from_id):
        c = self._cursor()
        c.execute('SELECT COUNT(*) FROM Reminders WHERE CreatorID = ?',
                  (from_id,))
        count = c.fetchone()[0]
        c.close()
        return count

    def clear_reminders(self, chat_id, from_id):
        c = self._cursor()
        c.execute('DELETE FROM Reminders WHERE '
                  'ChatID = ? AND CreatorID = ?', (chat_id, from_id))
        c.close()
        self._save()

    def clear_nth_reminder(self, chat_id, from_id, n):
        c = self._cursor()
        c.execute('SELECT * FROM Reminders WHERE ChatID = ? AND '
                  'CreatorID = ? ORDER BY Due ASC', (chat_id, from_id))
        row = c.fetchone()
        while row and n:
            n -= 1
            row = c.fetchone()

        if row:
            c.execute('DELETE FROM Reminders WHERE ID = ?', (row[0],))

        c.close()
        self._save()
        return row is not None

    def get_nth_reminder(self, chat_id, from_id, n):
        c = self._cursor()
        c.execute('SELECT * FROM Reminders WHERE ChatID = ? AND '
                  'CreatorID = ? ORDER BY Due ASC', (chat_id, from_id))
        row = c.fetchone()

        while row and n:
            n -= 1
            row = c.fetchone()

        result = None
        if row:
            result = Reminder(*row)

        c.close()

        return result

    def iter_reminders(self, chat_id=None, from_id=None):
        c = self._cursor()
        if chat_id:
            if not from_id:
                raise ValueError('from_id must be given if chat_id is')

            c.execute('SELECT * FROM Reminders WHERE ChatID = ? AND '
                      'CreatorID = ? ORDER BY Due ASC', (chat_id, from_id))
        else:
            c.execute('SELECT * FROM Reminders ORDER BY Due ASC')

        row = c.fetchone()
        while row:
            yield Reminder(*row)
            row = c.fetchone()

        c.close()

    def set_time_delta(self, user_id, delta, zone=None):
        c = self._cursor()
        c.execute(
            'INSERT OR REPLACE INTO TimeDelta '
            '(UserID, Delta, TimeZone) VALUES (?, ?, ?)',
            (user_id, delta, zone)
        )
        c.close()
        self._save()

    def get_time_delta(self, user_id):
        c = self._cursor()
        c.execute('SELECT Delta, TimeZone FROM TimeDelta WHERE UserID = ?', (user_id,))
        delta, time_zone = (c.fetchone() or (None, None))
        return TimeDelta(delta, time_zone) if delta is not None else None

    def set_later(self, user_id, delta):
        c = self._cursor()
        c.execute(
            'INSERT OR REPLACE INTO Later '
            '(UserID, Delta) VALUES (?, ?)',
            (user_id, delta)
        )
        c.close()
        self._save()

    def get_later(self, user_id):
        c = self._cursor()
        c.execute('SELECT Delta FROM Later WHERE UserID = ?', (user_id,))
        result = c.fetchone()
        return result[0] if result is not None else None

    def pop_reminder(self, reminder_id):
        c = self._cursor()
        c.execute('SELECT * FROM Reminders WHERE ID = ?', (reminder_id,))
        row = c.fetchone()
        c.execute('DELETE FROM Reminders WHERE ID = ?', (reminder_id,))
        c.close()
        self._save()
        return Reminder(*row) if row else None

    def add_birthday(self, creator_id, month, day, person_id, person_name):
        c = self._cursor()
        c.execute(
            'INSERT INTO Birthdays '
            '(CreatorID, Month, Day, PersonID, PersonName) VALUES (?, ?, ?, ?, ?)',
            (creator_id, month, day, person_id, (person_name or '(no name)')[:16])
        )
        c.close()
        self._save()

    # TODO Factor these out (similar to get_reminder_count and iter_reminders)
    def get_birthday_count(self, creator_id):
        c = self._cursor()
        c.execute('SELECT COUNT(*) FROM Birthdays WHERE CreatorID = ?',
                  (creator_id,))
        count = c.fetchone()[0]
        c.close()
        return count

    def iter_birthdays(self, creator_id=None, month=None, day=None):
        c = self._cursor()

        where = []
        params = []
        if creator_id:
            where.append('CreatorID = ?')
            params.append(creator_id)

        if month:
            where.append('Month = ?')
            params.append(month)

        if day:
            where.append('Day = ?')
            params.append(day)

        params = tuple(params)
        if where:
            where = 'WHERE ' + ' AND '.join(where)
        else:
            where = ''

        c.execute(f'SELECT * FROM Birthdays {where} '
                  'ORDER BY Month ASC, Day ASC', params)

        row = c.fetchone()
        while row:
            yield Birthday(*row)
            row = c.fetchone()

        c.close()

    def set_birthday_stage(self, birthday_id, year, stage):
        c = self._cursor()
        c.execute('UPDATE Birthdays '
                  'SET YearReminded = ?, RemindStage = ? '
                  'WHERE ID = ?',
                  (year, stage, birthday_id))
        c.close()
        self._save()
        return stage

    def has_birthday_stage(self, birthday_id, year, stage):
        c = self._cursor()
        c.execute('SELECT * FROM Birthdays WHERE '
                  'ID = ? AND YearReminded = ? AND RemindStage = ?',
                  (birthday_id, year, stage))

        has = c.fetchone() is not None
        c.close()
        return has

    def stats(self):
        c = self._cursor()
        c.execute('SELECT COUNT(DISTINCT CreatorID) FROM Reminders')
        people_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM Reminders')
        reminder_count = c.fetchone()[0]
        c.close()

        return people_count, reminder_count

    def delete_birthday(self, birthday_id):
        c = self._cursor()
        c.execute('DELETE FROM Birthdays WHERE ID = ?', (birthday_id,))
        c.close()
        self._save()
