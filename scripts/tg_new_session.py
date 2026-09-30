#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выпуск новой строки сессии Telegram (TG_SESSION) для конвейера Atlas.

ЗАЧЕМ. Конвейер читает 46 ПУБЛИЧНЫХ телеграм-каналов через MTProto. Для этого
ему нужна строка сессии: она заменяет вход по телефону и коду. Строка сессии
это полный доступ к аккаунту без пароля и без второго фактора, поэтому её
нельзя показать повторно и нельзя восстановить: если старая потеряна или
скомпрометирована, выпускается новая.

ГДЕ ЗАПУСКАТЬ. Только на своей машине. В GitHub Actions этот скрипт работать
не может и не должен: вход требует кода из Telegram.

ЧТО НУЖНО:
    python3 -m pip install telethon
    TG_API_ID и TG_API_HASH с my.telegram.org, раздел API development tools

ЗАПУСК:
    TG_API_ID=... TG_API_HASH=... python3 scripts/tg_new_session.py

    Скрипт спросит номер телефона, код из Telegram и, если включена
    двухфакторная защита, облачный пароль. Ничего из этого никуда не
    отправляется, кроме серверов Telegram.

ПОСЛЕ ЗАПУСКА:
    1. Скопировать выведенную строку СРАЗУ в GitHub:
       Settings -> Environments -> ingest -> Add environment secret -> TG_SESSION
    2. Удалить TG_SESSION с уровня репозитория.
    3. Очистить окно терминала (команда clear или закрыть окно).
    4. В Telegram: Настройки -> Устройства -> завершить все прочие сеансы.
       Это убивает старую строку сессии окончательно.

    Строку НЕ сохранять в файлы, заметки, мессенджеры и переписку.

ОТДЕЛЬНАЯ РЕКОМЕНДАЦИЯ. Все 46 каналов конвейера публичные, читать их может
любой аккаунт. Поэтому правильнее завести ОТДЕЛЬНЫЙ телеграм-аккаунт на
отдельный номер и использовать его сессию. Тогда утечка строки означает потерю
служебного аккаунта-читателя, а не личного.
"""
import os
import sys


def main():
    api_id = os.environ.get('TG_API_ID', '').strip()
    api_hash = os.environ.get('TG_API_HASH', '').strip()
    if not api_id or not api_hash:
        print('Нужны TG_API_ID и TG_API_HASH в окружении. Пример запуска:\n'
              '  TG_API_ID=123456 TG_API_HASH=abcdef... python3 scripts/tg_new_session.py',
              file=sys.stderr)
        return 2
    try:
        api_id = int(api_id)
    except ValueError:
        print('TG_API_ID должен быть числом.', file=sys.stderr)
        return 2

    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print('Не установлен telethon. Поставьте его:\n'
              '  python3 -m pip install telethon', file=sys.stderr)
        return 2

    print('Вход в Telegram. Номер вводится в международном формате, например +79001234567.')
    print('Код придёт в сам Telegram, не в SMS.\n')

    # StringSession() без аргумента = новая пустая сессия: на диске ничего
    # не создаётся, строка существует только в памяти процесса.
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = client.get_me()
        who = getattr(me, 'username', None) or getattr(me, 'phone', None) or '?'
        session = client.session.save()

    print('\n' + '=' * 70)
    print('Вошли как: %s' % who)
    print('=' * 70)
    print('\nСТРОКА СЕССИИ (значение секрета TG_SESSION):\n')
    print(session)
    print('\n' + '=' * 70)
    print('Скопируйте её в GitHub: Settings -> Environments -> ingest ->')
    print('Add environment secret -> имя TG_SESSION.')
    print('Затем удалите TG_SESSION с уровня репозитория,')
    print('очистите окно терминала и завершите прочие сеансы в Telegram.')
    print('=' * 70)
    return 0


if __name__ == '__main__':
    sys.exit(main())
