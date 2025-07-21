from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
import asyncio

# --- Конфигурация ---
TOKEN = "7828432792:AAE2zofhM85A-fC9i-xBpgLjR9YH-_3DJVA"
TARGET_CHANNEL_ID = "-1002558046400"
BOT_USERNAME = "VTIHub_bot"  # без @
START_PARAM = "start"  # необязательно
DEEPLINK_URL = f"https://t.me/{BOT_USERNAME}?start={START_PARAM}"

async def send_start_bot_button(app: Application):
	# Обновленная клавиатура с эмодзи робота и русским текстом
	keyboard = InlineKeyboardMarkup([
		[InlineKeyboardButton("Менеджер заявок", url=DEEPLINK_URL)]
	])

	# Используем "нулевой пробел", чтобы текст сообщения был невидимым
	message_text = "🤖"


	try:
		sent_message = await app.bot.send_message(
			chat_id=TARGET_CHANNEL_ID,
			text=message_text,
			reply_markup=keyboard
		)

		await app.bot.pin_chat_message(
			chat_id=TARGET_CHANNEL_ID,
			message_id=sent_message.message_id
		)
		print("Сообщение с кнопкой для запуска бота отправлено и закреплено.")
	except Exception as e:
		print(f"Ошибка: {e}")

async def main():
	app = Application.builder().token(TOKEN).build()
	await send_start_bot_button(app)

if __name__ == '__main__':
	asyncio.run(main())