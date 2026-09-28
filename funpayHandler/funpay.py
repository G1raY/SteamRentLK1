# Standard library imports
import random
import time
import asyncio
import sqlite3
import threading
import re
from datetime import datetime, timedelta

# Third-party imports
from FunPayAPI import Account, Runner, types, enums, events

# Project-specific imports
from config import FUNPAY_GOLDEN_KEY, ADMIN_ID, HOURS_FOR_REVIEW, MULTI_ACCOUNT_RENTAL_ENABLED

from databaseHandler.databaseSetup import SQLiteDB
from steamHandler.SteamGuard import get_steam_guard_code
from steamHandler.changePassword import changeSteamPassword
from logger import logger
from pytz import timezone


TOKEN = FUNPAY_GOLDEN_KEY
REFRESH_INTERVAL = 1300  # 30 minutes in seconds

feedbackGiven = []

moscow_tz = timezone("Europe/Moscow")

db = SQLiteDB()


def refresh_session():
    global acc, runner
    logger.info("Refreshing FunPay session...")
    acc = Account(TOKEN).get()
    runner = Runner(acc)
    logger.info("FunPay session refreshed successfully.")


def check_rental_expiration():
    """Checks for expired rentals and changes passwords every minute"""
    logger.info("Starting rental expiration checker...")
    invalid_accs = []
    while True:
        try:
            # Используем существующее подключение из db вместо создания нового
            cursor = db.conn.cursor()

            current_time = datetime.now(tz=moscow_tz)

            # Get all active rentals with their maFile paths
            cursor.execute(
                """
                SELECT a.ID, a.owner, a.rental_start, a.rental_duration, a.path_to_maFile, a.password
                FROM accounts a
                WHERE a.owner IS NOT NULL 
                AND a.rental_start IS NOT NULL
                """
            )

            accounts_data = cursor.fetchall()

            for row in accounts_data:
                account_id, owner, start_time, duration, mafile_path, password = row
                logger.debug(f"Processing account ID: {account_id}, Owner: {owner}")

                start_datetime = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                start_datetime = moscow_tz.localize(start_datetime)
                expiry_time = start_datetime + timedelta(hours=int(duration))
                
                # Calculate time remaining
                time_remaining = expiry_time - current_time
                hours_remaining = time_remaining.total_seconds() / 3600

                logger.debug(
                    f"Start time: {start_datetime}, Expiry time: {expiry_time}, Current time: {current_time}, Hours remaining: {hours_remaining:.2f}"
                )

                # Send warning notifications
                # Предупреждаем за 10 минут до истечения (6-12 минут, чтобы захватить точно 10 минут)
                # Проверка происходит каждую минуту
                if 0.1 <= hours_remaining <= 0.2:  # 6-12 minutes remaining (10 minutes ±2)
                    try:
                        send_message_to_admin(
                            f"ПРЕДУПРЕЖДЕНИЕ ОБ ИСТЕЧЕНИИ!\n\n"
                            f"ID аккаунта: {account_id}\n"
                            f"Владелец: {owner}\n"
                            f"Осталось времени: {hours_remaining:.1f} часа (~{int(hours_remaining * 60)} минут)\n"
                            f"Совет: Пользователь скоро потеряет доступ!"
                        )
                        
                        send_message_by_owner(
                            owner,
                            f"ВНИМАНИЕ! Время аренды истекает через ~10 минут!\n\n"
                            f"Аккаунт ID: {account_id}\n"
                            f"Осталось времени: ~{int(hours_remaining * 60)} минут\n"
                            f"СРОЧНО: Оставьте отзыв, чтобы продлить аренду на +{HOURS_FOR_REVIEW} час!\n\n"
                            f"Как продлить:\n"
                            f"• Оставьте отзыв на FunPay\n"
                            f"• Или купите продление\n\n"
                            f"Время истечения: {expiry_time.strftime('%H:%M:%S')}"
                        )
                        logger.info(f"Warning notification sent to {owner} for account {account_id} - {hours_remaining:.1f} hours remaining")
                    except Exception as e:
                        logger.error(f"Failed to send warning notification: {str(e)}")

                # Check if expired
                if current_time >= expiry_time and account_id not in invalid_accs:
                    logger.info(
                        f"Account {account_id} rental expired. Time difference: {current_time - expiry_time}"
                    )
                    try:
                        new_password = asyncio.run(
                            changeSteamPassword(
                                path_to_maFile=mafile_path,
                                password=password,
                            )
                        )
                        logger.info(
                            f"Password changed successfully for account {account_id}. New password: {new_password}"
                        )
                        from botHandler.bot import send_message_to_admin

                        send_message_to_admin(
                            f"АРЕНДА ИСТЕКЛА\n\n"
                            f"ID аккаунта: {account_id}\n"
                            f"Владелец: {owner}\n"
                            f"Новый пароль: {new_password}\n"
                            f"Время истечения: {expiry_time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )

                        # Update password and nullify all accounts with the same login
                        logger.debug(f"Updating database for account {account_id}...")
                        cursor.execute(
                            """
                            UPDATE accounts
                            SET password = ?, owner = NULL, rental_start = NULL
                            WHERE login = (
                                SELECT login
                                FROM accounts
                                WHERE ID = ?
                            )
                            """,
                            (new_password, account_id),
                        )
                        logger.info(f"Database updated for account {account_id}.")

                        try:
                            send_message_by_owner(
                                owner,
                                f"Срок аренды истек!\n\n"
                                f"Аккаунт ID: {account_id}\n"
                                f"Доступ прекращен\n\n"
                                f"Не забудьте подтвердить заказ на FunPay!\n"
                                f"Оставьте отзыв для будущих покупок!\n\n"
                                f"Спасибо за использование нашего сервиса!"
                            )
                            logger.info(
                                f"Expiration notification sent to user {owner}."
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to send expiration notification: {str(e)}"
                            )

                    except Exception as e:
                        logger.error(
                            f"Failed to change password for account {account_id}: {str(e)}"
                        )
                        invalid_accs.append(account_id)

                        continue

            db.conn.commit()

        except Exception as e:
            logger.error(f"Error in rental expiration checker: {str(e)}")

        time.sleep(60)  # Check every 60 seconds (1 minute)


def send_message_by_owner(owner, message):
    """Send message to FunPay user by username"""
    try:
        chat = acc.get_chat_by_name(owner, True)
        acc.send_message(chat.id, message)
    except Exception as e:
        logger.error(f"Failed to send message to {owner}: {str(e)}")


def startFunpay():
    global acc, runner

    logger.info("Starting FunPay bot...")
    acc = Account(TOKEN).get()
    runner = Runner(acc)
    logger.info("FunPay account and runner initialized.")
    last_refresh = time.time()

    logger.info("Starting rental expiration checker thread...")

    timerChecker_thread = threading.Thread(target=check_rental_expiration)
    timerChecker_thread.daemon = True
    timerChecker_thread.start()

    for event in runner.listen(requests_delay=8):
        try:
            current_time = time.time()
            if current_time - last_refresh >= REFRESH_INTERVAL:
                logger.info("Refreshing session due to interval timeout...")
                refresh_session()
                last_refresh = current_time

            if event.type is events.EventTypes.NEW_ORDER:
                logger.info("Processing new order event...")

                accounts = db.get_unowned_accounts()

                acc = Account(TOKEN).get()
                chat = acc.get_chat_by_name(event.order.buyer_username, True)

                all_accounts = db.get_all_account_names()

                order_name = event.order.description
                number_of_orders = event.order.amount

                logger.info(f"Original order name: {order_name}")

                cleaned_order_name = re.sub(r"[^\w\s]", " ", order_name)
                cleaned_order_name = " ".join(cleaned_order_name.split())
                logger.info(f"Cleaned order name: {cleaned_order_name}")

                matched_account = None
                max_similarity = 0

                for account in all_accounts:
                    cleaned_account = re.sub(r"[^\w\s]", " ", account)
                    cleaned_account = " ".join(cleaned_account.split())

                    if cleaned_account.lower() in cleaned_order_name.lower():
                        similarity = len(cleaned_account)
                        if similarity > max_similarity:
                            max_similarity = similarity
                            matched_account = account

                if matched_account:
                    order_name = matched_account
                    logger.info(f"Matched order name: {order_name}")
                else:
                    logger.warning(f"No matching account found for order: {order_name}")
                    continue

                if order_name in all_accounts:
                    logger.info(f"New order: {order_name}")

                    # Проверяем настройку множественной выдачи
                    if not MULTI_ACCOUNT_RENTAL_ENABLED:
                        # Множественная выдача отключена - выдаем только 1 аккаунт
                        logger.info(f"Multi-account rental disabled. Issuing only 1 account to {event.order.buyer_username}")
                        
                        if number_of_orders > 1:
                            acc.send_message(
                                chat.id,
                                f"ВНИМАНИЕ!\n\n"
                                f"Вы заказали {number_of_orders} аккаунтов типа '{order_name}', но множественная выдача отключена.\n\n"
                                f"Вам будет выдан 1 аккаунт на стандартное время лота.\n"
                                f"Остальные средства будут возвращены.\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                            )
                            logger.info(f"User {event.order.buyer_username} ordered {number_of_orders} accounts but will receive only 1 (multi-account disabled)")
                        
                        # Ищем один доступный аккаунт
                        available_accounts = db.get_available_accounts_by_name(order_name, 1)
                        
                        if not available_accounts:
                            logger.error(f"No available accounts found for '{order_name}'")
                            acc.send_message(
                                chat.id,
                                f"К сожалению, аккаунты типа '{order_name}' временно недоступны.\n"
                                f"Попробуйте позже или выберите другой тип аккаунта."
                            )
                            acc.refund(event.order.id)
                            continue
                        
                        # Выдаем один аккаунт
                        account = available_accounts[0]
                        rental_duration = account['rental_duration']
                        
                        success = db.set_account_owner_with_duration(
                            account["id"], event.order.buyer_username, rental_duration, str(event.order.id)
                        )
                        
                        if not success:
                            logger.error(f"Failed to assign account {account['id']} to user {event.order.buyer_username}")
                            continue
                        
                        from botHandler.bot import send_message_to_admin

                        send_message_to_admin(
                            f"НОВЫЙ АККАУНТ ВЫДАН (ОДИНОЧНАЯ ВЫДАЧА)\n\n"
                            f"Покупатель: {event.order.buyer_username}\n"
                            f"ID: {account['id']}\n"
                            f"Имя аккаунта: {account['account_name']}\n"
                            f"Логин: {account['login']}\n"
                            f"Пароль: {account['password']}\n"
                            f"Цена: {event.order.price} ₽\n"
                            f"Заказано: {number_of_orders} шт.\n"
                            f"Выдано: 1 шт. (множественная выдача отключена)\n"
                            f"Время аренды: {rental_duration} часа\n"
                            f"ID заказа: {event.order.id}"
                        )

                        acc.send_message(
                            chat.id,
                            text=f"Ваш аккаунт:\n"
                            f"Уникальный ID: {account['id']}\n"
                            f"Название: {account['account_name']}\n\n"
                            f"Срок аренды: {rental_duration} часа \n\n"
                            f"Логин: {account['login']}\n"
                            f"Пароль: {account['password']}\n\n"
                            f"Что-бы запросить код подтверждения, отправьте /code\n\n"
                            f"За отзыв - вы получите дополнительные {HOURS_FOR_REVIEW} час/часа аренды.\n"
                            f"ВНИМАНИЕ: Система предупредит вас за 10 минут до истечения!\n\n"
                            f"------------------------------------------------------------------------------\n\n"
                            "Если вы еще не прочитали инструкцию по входу в аккаунт, сделайте это прямо сейчас!\n"
                            "При возникновении проблем или вопросов позовите меня командой /question",
                        )
                        
                        # Подтверждаем заказ
                        acc.confirm(event.order.id)
                        
                    else:
                        # Множественная выдача включена - выдаем запрошенное количество аккаунтов
                        logger.info(f"Multi-account rental enabled. Issuing {number_of_orders} accounts to {event.order.buyer_username}")
                        
                        # Ищем доступные аккаунты по названию
                        available_accounts = db.get_available_accounts_by_name(order_name, number_of_orders)
                        
                        if not available_accounts:
                            # Аккаунты не найдены - возврат
                            logger.error(f"No available accounts found for '{order_name}'")
                            acc.send_message(
                                chat.id,
                                f"К сожалению, аккаунты типа '{order_name}' временно недоступны.\n"
                                f"Попробуйте позже или выберите другой тип аккаунта."
                            )
                            acc.refund(event.order.id)
                            continue
                        
                        # Проверяем, достаточно ли доступных аккаунтов
                        if len(available_accounts) < number_of_orders:
                            logger.warning(f"Not enough accounts available. Requested: {number_of_orders}, Available: {len(available_accounts)}")
                            acc.send_message(
                                chat.id,
                                f"ВНИМАНИЕ!\n\n"
                                f"Вы заказали {number_of_orders} аккаунтов типа '{order_name}', но доступно только {len(available_accounts)}.\n\n"
                                f"Вам будет выдано {len(available_accounts)} аккаунтов.\n"
                                f"Остальные средства будут возвращены.\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                            )
                            # Обновляем количество заказанных аккаунтов
                            number_of_orders = len(available_accounts)
                        
                        # Получаем ID аккаунтов для выдачи
                        account_ids = [acc['id'] for acc in available_accounts[:number_of_orders]]
                        rental_duration = available_accounts[0]['rental_duration']  # Все аккаунты одного типа имеют одинаковую длительность
                        
                        # Выдаем несколько аккаунтов пользователю
                        logger.info(f"Assigning {number_of_orders} accounts '{order_name}' to user {event.order.buyer_username} for {rental_duration} hours")
                        
                        # Устанавливаем владельца для всех аккаунтов
                        success = db.assign_multiple_accounts(
                            account_ids, event.order.buyer_username, str(event.order.id)
                        )
                        
                        if not success:
                            logger.error(f"Failed to assign accounts to user {event.order.buyer_username}")
                            acc.send_message(
                                chat.id,
                                f"Ошибка при выдаче аккаунтов. Обратитесь к администратору."
                            )
                            acc.refund(event.order.id)
                            continue
                        
                        from botHandler.bot import send_message_to_admin

                        # Уведомляем админа
                        send_message_to_admin(
                            f"НОВЫЕ АККАУНТЫ ВЫДАНЫ (МНОЖЕСТВЕННАЯ ВЫДАЧА)\n\n"
                            f"Покупатель: {event.order.buyer_username}\n"
                            f"Тип аккаунта: {order_name}\n"
                            f"Количество: {number_of_orders} шт.\n"
                            f"Цена: {event.order.price} ₽\n"
                            f"Время аренды: {rental_duration} часа\n"
                            f"ID заказа: {event.order.id}\n"
                            f"ID аккаунтов: {', '.join(map(str, account_ids))}"
                        )

                        # Отправляем пользователю информацию о выданных аккаунтах
                        acc.send_message(
                            chat.id,
                            f"Ваши аккаунты ({number_of_orders} шт.):\n"
                            f"Тип: {order_name}\n"
                            f"Срок аренды: {rental_duration} часа\n\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                        )
                        
                        # Отправляем детали каждого аккаунта
                        for i, account in enumerate(available_accounts[:number_of_orders], 1):
                            acc.send_message(
                                chat.id,
                                f"Аккаунт #{i}:\n"
                                f"ID: {account['id']}\n"
                                f"Логин: {account['login']}\n"
                                f"Пароль: {account['password']}\n\n"
                            )
                        
                        # Отправляем общую информацию
                        acc.send_message(
                            chat.id,
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Для запроса кода подтверждения отправьте /code\n\n"
                            f"За отзыв - вы получите дополнительные {HOURS_FOR_REVIEW} час/часа аренды.\n"
                            f"ВНИМАНИЕ: Система предупредит вас за 10 минут до истечения!\n\n"
                            f"Если вы еще не прочитали инструкцию по входу в аккаунт, сделайте это прямо сейчас!\n"
                            f"При возникновении проблем или вопросов позовите меня командой /question"
                        )
                        
                        # Подтверждаем заказ
                        acc.confirm(event.order.id)
                        
                else:
                    # Товар не найден в базе - это не аккаунт для аренды, пропускаем
                    logger.info(f"Товар '{order_name}' не найден в базе данных - это не аккаунт для аренды, пропускаем")
                    continue
                
                logger.info(f"New order processed successfully.")

            if event.type is events.EventTypes.NEW_MESSAGE:
                logger.info("Processing new message event...")

                chat = acc.get_chat_by_name(event.message.author, True)

                if event.message.author_id != acc.id:

                    logger.info(f"{event.message.author} : {event.message.text}")

                    if "/code" == event.message.text.strip():
                        try:
                            owner_data = db.get_owner_mafile(event.message.author)

                            logger.info(owner_data)

                            if owner_data:
                                # Группируем аккаунты по order_id для лучшего отображения
                                accounts_by_order = {}
                                for account in owner_data:
                                    (
                                        account_id,
                                        account_name,
                                        mafile_path,
                                        login,
                                        rental_duration,
                                    ) = account
                                    
                                    # Получаем order_id для группировки
                                    account_details = db.get_account_by_id(account_id)
                                    order_id = account_details.get('order_id', 'single') if account_details else 'single'
                                    
                                    if order_id not in accounts_by_order:
                                        accounts_by_order[order_id] = []
                                    accounts_by_order[order_id].append((account_id, account_name, mafile_path, login, rental_duration))
                                
                                # Отправляем коды для каждого заказа
                                for order_id, accounts in accounts_by_order.items():
                                    if len(accounts) > 1:
                                        acc.send_message(
                                            chat.id,
                                            f"Коды для заказа {order_id} ({len(accounts)} аккаунтов):"
                                        )
                                    
                                    for account in accounts:
                                        (
                                            account_id,
                                            account_name,
                                            mafile_path,
                                            login,
                                            rental_duration,
                                        ) = account
                                        guard_code = get_steam_guard_code(mafile_path)
                                        acc.send_message(
                                            chat.id,
                                            f"ID {account_id} ({account_name}) -> {guard_code}",
                                        )
                            else:
                                acc.send_message(chat.id, "Ошибка: аккаунт не найден")
                        except Exception as e:
                            acc.send_message(
                                chat.id, f"Ошибка при генерации кода: {str(e)}"
                            )

                    elif event.message.text == "/question":

                        acc.send_message(chat.id, "Оператор скоро ответит вам.")

                    elif "/stock" == event.message.text:

                        chatData = acc.get_chat(chat.id)

                        logger.info(chatData.looking_text)

                        lookingAccountName = [
                            p.strip() for p in chatData.looking_text.split(",")
                        ]

                        lookingAccountName = max(
                            lookingAccountName,
                            key=lambda x: (len(x), bool(re.search(r"[\W_]", x))),
                        )

                        logger.info(lookingAccountName)

                        # Get all account names from the database
                        accounts = db.get_all_account_names()

                        matching_accounts = [
                            account_name
                            for account_name in accounts
                            if account_name in lookingAccountName
                        ]

                        total_accounts = len(matching_accounts)

                        unowned_accounts = db.get_unowned_account_names()

                        matching_accounts = [
                            account_name
                            for account_name in unowned_accounts
                            if account_name in lookingAccountName
                        ]

                        logger.info(matching_accounts)

                        total_unwoned_accounts = len(matching_accounts)

                        # Send the count to the user
                        acc.send_message(
                            chat.id,
                            f"Вы смотрите аккаунт: {lookingAccountName}\n\n"
                            f"Свободные аккаунты: {total_unwoned_accounts}/{total_accounts}",
                        )

                    elif event.message.type == types.MessageTypes.NEW_FEEDBACK:
                        try:
                            conn = sqlite3.connect("database.db")
                            cursor = conn.cursor()

                            # Extract the owner's username from the feedback message
                            feedback_text = event.message.text
                            if "Покупатель" in feedback_text:
                                owner = feedback_text.split("Покупатель")[1].split()[0]
                            else:
                                logger.error(
                                    "Failed to extract owner from feedback message."
                                )
                                return

                            if owner not in feedbackGiven:
                                feedbackGiven.append(owner)

                                if owner in db.get_active_owners():
                                    # Check if the user has an active rental
                                    cursor.execute(
                                        """
                                        SELECT ID, rental_start, rental_duration, order_id
                                        FROM accounts
                                        WHERE owner = ?
                                        """,
                                        (owner,),
                                    )
                                    accounts = cursor.fetchall()

                                    # Группируем аккаунты по заказам для статистики
                                    orders_stats = {}
                                    for account in accounts:
                                        account_id, rental_start, rental_duration, order_id = account
                                        order_id = order_id or 'single'
                                        
                                        if order_id not in orders_stats:
                                            orders_stats[order_id] = 0
                                        orders_stats[order_id] += 1
                                        
                                        # Add extension hours to the duration (not to start time)
                                        new_duration = int(rental_duration) + HOURS_FOR_REVIEW
                                        
                                        # Update the rental duration in the database
                                        cursor.execute(
                                            """
                                            UPDATE accounts
                                            SET rental_duration = ?
                                            WHERE ID = ?
                                            """,
                                            (new_duration, account_id),
                                        )
                                        
                                        logger.info(
                                            f"Rental duration for account {account_id} extended from {rental_duration} to {new_duration} hours (+{HOURS_FOR_REVIEW})."
                                        )

                                    conn.commit()

                                    # Notify the user
                                    chat = acc.get_chat_by_name(owner, True)
                                    
                                    # Формируем сообщение с информацией о заказах
                                    orders_info = ""
                                    if len(orders_stats) > 1:
                                        orders_info = "\n\n📦 **Ваши заказы:**\n"
                                        for order_id, count in orders_stats.items():
                                            orders_info += f"• Заказ {order_id}: {count} аккаунт(ов)\n"
                                    
                                    acc.send_message(
                                        chat.id,
                                        f"Спасибо за ваш отзыв!\n\n"
                                        f"Время аренды продлено на +{HOURS_FOR_REVIEW} час!\n\n"
                                        f"Ваши активные аккаунты:\n"
                                        f"• Общее количество: {len(accounts)}\n"
                                        f"• Количество заказов: {len(orders_stats)}\n"
                                        f"• Новое время аренды: +{HOURS_FOR_REVIEW} часа\n"
                                        f"{orders_info}\n"
                                        f"Совет: Оставляйте отзывы заранее, чтобы получить максимальное продление!\n"
                                        f"Напоминание: Система предупредит вас за 10 минут до истечения!",
                                    )

                                    logger.info(
                                        f"Rental duration extended for {len(accounts)} accounts of user {owner} by +{HOURS_FOR_REVIEW} hours across {len(orders_stats)} orders."
                                    )

                        except Exception as e:
                            logger.error(f"Error handling NEW_FEEDBACK event: {str(e)}")
                        finally:
                            conn.close()

                logger.info("New message processed successfully.")

        except Exception as e:
            error_msg = str(e)
            if "Read timed out" in error_msg or "timeout" in error_msg.lower():
                logger.warning(f"FunPay connection timeout occurred: {error_msg}")
                # Try to refresh session on timeout
                try:
                    refresh_session()
                    logger.info("Session refreshed after timeout")
                except Exception as refresh_error:
                    logger.error(f"Failed to refresh session after timeout: {str(refresh_error)}")
            else:
                logger.error(f"An error occurred while processing event: {error_msg}")


def send_message_by_owner(owner, message):
    """Send a message to the specified owner."""
    try:
        chat = acc.get_chat_by_name(owner, True)
        acc.send_message(chat.id, message)
    except Exception as e:
        logger.error(f"Failed to send message to {owner}: {str(e)}")


# Ensure the function is available for import
__all__ = ["send_message_by_owner"]
