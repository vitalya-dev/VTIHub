import logging
import json
import argparse
import pytz
import base64
import re
import io
import os
import subprocess

from typing import Optional, Dict, Any, Tuple

from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import textwrap

import sqlite3
import time # For handling potential rapid file changes if needed
import telegram
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import asyncio # For thread-safe operations with PTB

# Import necessary components from python-telegram-bot
from telegram import (
	error,
	Update,
	BotCommand,
	MenuButtonCommands,
	WebAppInfo,
	ReplyKeyboardMarkup, # Use ReplyKeyboardMarkup for the main app selection
	KeyboardButton,      # Buttons within the ReplyKeyboard
	ReplyKeyboardRemove, # To potentially hide the custom keyboard later
	InlineKeyboardButton,
	InlineKeyboardMarkup,
	Message
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import (
	Application,
	CommandHandler,
	ContextTypes,
	MessageHandler,
	CallbackQueryHandler,
	filters,
)
# Import logging constants correctly
from logging import DEBUG, INFO

# --- Configuration ---
# Define URLs for potentially multiple Web Apps
WEB_APP_URLS = {
	"ticket": "https://vitalya-dev.github.io/VTIHub/ticket_app.html",
	"calculator": "https://vitalya-dev.github.io/VTIHub/calculator_app.html"
}

DEBUG_WEB_APP_URLS = {
	"ticket": "https://vitalya-dev.github.io/VTIHub/ticket_app_debug.html",
}

TARGET_CHANNEL_ID = "-1002558046400" # Or e.g., -1001234567890

TICKETS_DATA_MARKER = "Encoded Data:" # For tickets
CALC_DATA_MARKER = "Calculator Encoded Data:" # For calculator data

logger = logging.getLogger(__name__)


# --- Database Monitoring Configuration ---
DB_FILE_PATH = None # To be set by command-line argument
LAST_KNOWN_DB_CASE_ID_KEY = "last_known_db_case_id"
DB_ID_STORAGE_FILENAME_TEMPLATE = "last_processed_id_{db_name}.txt" # Template for the ID storage file
# How often to check the database after a modification event is detected (in seconds)
DB_PROCESSING_DELAY = 2

# --- Bot Setup Functions ---

async def setup_commands(application: Application):
	"""Set bot commands menu."""
	await application.bot.set_my_commands([
		BotCommand("start", "Показать панель управления"),
	])
	logger.info("Bot commands set.")

async def setup_menu_button(application: Application):
	"""Set the persistent menu button to show commands."""
	await application.bot.set_chat_menu_button(
		menu_button=MenuButtonCommands()
	)
	logger.info("Menu button set to show commands.")

async def post_init(application: Application):
	"""Run setup tasks after the application is initialized."""
	await setup_commands(application)
	await setup_menu_button(application)
	logger.info("Post-initialization setup complete.")


# --- Command Handlers ---


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""
	Handles the /start command.
	Shows the main keyboard with buttons for Web Apps.
	"""
	user = update.effective_user
	user_id = user.id
	user_info_log = f"{user_id} ({user.username or user.full_name})"

	debug_mode = context.bot_data.get('debug_mode', False)
	user_can_access = False
	denial_reason = "Извините, этот бот доступен только для сотрудников ВТИ, являющихся участниками рабочего канала."

	if debug_mode:
		logger.info(f"DEBUG MODE ACTIVE for /start command by {user_info_log}. Bypassing membership check.")
		user_can_access = True
		await update.message.reply_text(
			"⚙️ *Режим отладки АКТИВЕН*: Проверка членства в канале пропущена.",
		)
	else:
		if not TARGET_CHANNEL_ID:
			logger.error("TARGET_CHANNEL_ID is not configured. Denying access.")
			denial_reason = "⚠️ Ошибка конфигурации бота. Доступ запрещен."
		else:
			try:
				chat_member = await context.bot.get_chat_member(chat_id=TARGET_CHANNEL_ID, user_id=user_id)
				allowed_statuses = [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]

				if chat_member.status in allowed_statuses:
					user_can_access = True
					logger.info(f"Access GRANTED for user {user_info_log}. Status: {chat_member.status} in channel {TARGET_CHANNEL_ID}.")
				else:
					logger.warning(f"Access DENIED for user {user_info_log}. Status: {chat_member.status} in channel {TARGET_CHANNEL_ID}.")
			except error.BadRequest as e:
				if "user not found" in e.message.lower():
					logger.warning(f"Access DENIED for user {user_info_log}. User not found in channel {TARGET_CHANNEL_ID}.")
				else:
					logger.error(f"BadRequest when checking membership for user {user_info_log} in channel {TARGET_CHANNEL_ID}: {e}")
					denial_reason = "⚠️ Не удалось проверить ваше членство в канале из-за ошибки. Пожалуйста, попробуйте позже или свяжитесь с администратором."
			except error.TelegramError as e:
				logger.error(f"TelegramError checking membership for {user_info_log} in {TARGET_CHANNEL_ID}: {e}")
				denial_reason = "⚠️ Произошла ошибка при проверке доступа. Пожалуйста, попробуйте позже или свяжитесь с администратором."

	if user_can_access:
		current_ticket_urls = DEBUG_WEB_APP_URLS if debug_mode else WEB_APP_URLS
		
		ticket_app_url = current_ticket_urls.get("ticket", WEB_APP_URLS["ticket"]) # Fallback to default if not in debug set
		calculator_app_url = current_ticket_urls.get("calculator", WEB_APP_URLS.get("calculator"))

		if not calculator_app_url:
			logger.error("Calculator app URL is not defined!")
			# Optionally notify user or handle this error
		
		if debug_mode:
			logger.info(f"DEBUG MODE: Using debug ticket app URL: {ticket_app_url}")
			if calculator_app_url: # only log if it exists
				logger.info(f"DEBUG MODE: Using debug calculator app URL: {calculator_app_url}")


		keyboard_buttons = [
			[KeyboardButton("📄 Новая Заявка", web_app=WebAppInfo(url=ticket_app_url))]
		]
		
		# Only add calculator button if URL is present
		if calculator_app_url:
			keyboard_buttons.append(
				[KeyboardButton("🛍️ Калькулятор", web_app=WebAppInfo(url=calculator_app_url))]
			)

		main_message = "🐶"
		await update.message.reply_text(
			main_message,
			reply_markup=ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)
		)
	else:
		await update.message.reply_text(
			denial_reason,
			reply_markup=ReplyKeyboardRemove()
		)


def format_phone_number_display(phone_str: str) -> str:
	"""
	Formats a raw phone number string into a more readable format,
	assuming input is either +7xxxxxxxxxx or 8xxxxxxxxxx.
	e.g., "+71234567890" -> "+7 (123) 456-78-90"
	e.g., "81234567890" -> "8 (123) 456-78-90"
	"""
	if not phone_str or phone_str == 'N/A':
		return 'N/A'

	# Remove common non-numeric characters except '+' if it's leading.
	# This is a safety net; ideally, inputs are already clean.
	cleaned_phone = re.sub(r'[^\d+]', '', phone_str)

	if cleaned_phone.startswith('+7') and len(cleaned_phone) == 12:
		# Format: +7 (XXX) XXX-XX-XX
		return f"{cleaned_phone[:2]} ({cleaned_phone[2:5]}) {cleaned_phone[5:8]}-{cleaned_phone[8:10]}-{cleaned_phone[10:12]}"
	elif cleaned_phone.startswith('8') and len(cleaned_phone) == 11:
		# Format: 8 (XXX) XXX-XX-XX
		return f"{cleaned_phone[0]} ({cleaned_phone[1:4]}) {cleaned_phone[4:7]}-{cleaned_phone[7:9]}-{cleaned_phone[9:11]}"
	else:
		# Fallback: If the format is unexpectedly different, return the cleaned version
		# or the original. This helps prevent errors if an odd format slips through.
		# Given your strict input rules, this path should ideally not be taken.
		return phone_str

# --- Data Processing Functions ---

async def process_ticket_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
	"""Processes data specifically from the Ticket Web App."""
	logger.info(f"Processing 'ticket_app' data: {data}")
	user = update.effective_user
	user_identifier = user.first_name
	if user.username:
		user_identifier = f"@{user.username}"
	elif user.full_name:
		user_identifier = user.full_name

	# --- DEBUG OPTION CHANGE ---
	# Retrieve debug mode status from bot_data
	debug_mode = context.bot_data.get('debug_mode', False)
	if debug_mode:
		logger.info("DEBUG MODE ACTIVE: Channel messages will be suppressed.")

	# Get current time
	try:
		tz = pytz.timezone('Europe/Moscow')
		current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
	except pytz.UnknownTimeZoneError:
		logger.warning("Unknown timezone 'Europe/Moscow', falling back to UTC.")
		current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

	# Extract web app data
	raw_phone = data.get('phone', 'N/A') # Get the raw phone number
	description = data.get('description', 'No description provided.')

	# Format phone for display
	formatted_phone_display = format_phone_number_display(raw_phone) # Use the new function

# --- Format phone number for tel: link ---
	phone_link_html = f'<b><code>{formatted_phone_display}</code></b>' # Default: just display formatted number

	if raw_phone and raw_phone != 'N/A':
		tel_url_number = None
		if raw_phone.startswith('+7') and len(raw_phone) == 12:
			tel_url_number = raw_phone  # Already in +7xxxxxxxxxx format
		elif raw_phone.startswith('8') and len(raw_phone) == 11:
			tel_url_number = '+7' + raw_phone[1:] # Convert 8xxxxxxxxxx to +7xxxxxxxxxx
		if tel_url_number:
			tel_url = f"tel:{tel_url_number}"
			phone_link_html = f'<a href="{tel_url}"><b><code>{formatted_phone_display}</code></b></a>'


	# Prepare ticket data for encoding (use raw_phone here as it's for data storage)
	ticket_details_to_encode = {
		'p': raw_phone, # Store the original/raw phone for data integrity
		'd': description,
		's': user_identifier,
		't': current_time
	}

	try:
		json_string = json.dumps(ticket_details_to_encode, separators=(',', ':'))
		base64_encoded_json = base64.b64encode(json_string.encode('utf-8')).decode('utf-8')
	except Exception as e:
		logger.error(f"Failed to encode ticket details: {e}", exc_info=True)
		await update.message.reply_text("Sorry, there was an error preparing your ticket data.")
		return

	print_button = InlineKeyboardButton("🖨️ Print", callback_data="print:parse_encoded")
	keyboard = InlineKeyboardMarkup([[print_button]])

	message_link = None # Will store the link to the channel message

	# --- STEP 1: Send the message to the Channel first (if not in debug mode) ---
	# --- DEBUG OPTION CHANGE ---
	if not debug_mode and TARGET_CHANNEL_ID:
		try:
			channel_message_text = (
				f"✅ Заявка создана!\n\n"
				f"👤 Отправил(а): {user_identifier}\n"
				f"🕒 Время: {current_time}\n"
				f"--- Детали заявки ---\n"
				f"📞 Телефон: {phone_link_html}\n"
				f"📝 Описание: {description}\n\n"
				f"{TICKETS_DATA_MARKER} {base64_encoded_json}\n\n"
			)
			sent_message = await context.bot.send_message(
				chat_id=TARGET_CHANNEL_ID,
				text=channel_message_text,
				reply_markup=keyboard,
				parse_mode=ParseMode.HTML,
				disable_web_page_preview=True
			)
			logger.info(f"Ticket posted to channel {TARGET_CHANNEL_ID}")
			internal_channel_id = str(TARGET_CHANNEL_ID)[4:]
			channel_message_id = sent_message.message_id
			message_link = f"https://t.me/c/{internal_channel_id}/{channel_message_id}"

		except Exception as e_channel:
			logger.error(f"Failed to send message TO CHANNEL {TARGET_CHANNEL_ID}: {e_channel}", exc_info=True)
			await update.message.reply_text("Sorry, there was an error posting your ticket to the channel.")
			return # Stop processing if channel message fails
	elif debug_mode:
		logger.info("DEBUG MODE: Suppressed message to channel.")
	elif not TARGET_CHANNEL_ID: # Should be caught by initial checks, but good to have
		logger.warning("TARGET_CHANNEL_ID is not set. Cannot send to channel.")
		await update.message.reply_text("Ticket channel is not configured. Cannot post.")
		return


	# --- STEP 2: After posting to channel (or skipping in debug), send user a full message ---
	try:
		user_message_text_parts = [
			f"✅ Заявка создана!\n\n"
			f"👤 Отправил(а): {user_identifier}\n"
			f"🕒 Время: {current_time}\n"
			f"--- Детали заявки ---\n"
			f"📞 Телефон: {phone_link_html}\n"
			f"📝 Описание: {description}\n\n"
			f"{TICKETS_DATA_MARKER} {base64_encoded_json}\n\n"
		]

		# --- DEBUG OPTION CHANGE ---
		if message_link: # Only add if message_link was successfully created (i.e., not in debug and channel send worked)
			user_message_text_parts.append(f"🔗 [Посмотреть вашу заявку в канале]({message_link})\n\n")
		elif debug_mode:
			user_message_text_parts.append("⚙️ *Режим отладки АКТИВЕН*: Сообщение в канал не отправлено.\n\n")

		user_message_text = "".join(user_message_text_parts)

		await update.message.reply_text(
			text=user_message_text,
			reply_markup=keyboard,
			parse_mode=ParseMode.HTML,
			disable_web_page_preview=True
		)
		logger.info(f"Confirmation message sent to user {user_identifier} ({user.id})")

	except Exception as e_user:
		logger.error(f"Error sending confirmation to user {user_identifier} ({user.id}): {e_user}", exc_info=True)
		try:
			await update.message.reply_text("Sorry, failed to send your ticket confirmation message.")
		except Exception as e_notify:
			logger.error(f"Failed even to notify user {user_identifier} ({user.id}): {e_notify}")


async def process_calculator_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
	"""Processes data specifically from the Calculator Web App and offers printing."""
	logger.info(f"Processing 'calculator_app' data: {data}")
	user = update.effective_user
	user_identifier = user.first_name
	if user.username:
		user_identifier = f"@{user.username}"
	elif user.full_name:
		user_identifier = user.full_name

	items = data.get('items', [])
	total_amount = data.get('total', 0.0)

	if not items:
		await update.message.reply_text("Калькулятор не вернул никаких товаров.")
		logger.warning(f"Calculator data from {user_identifier} ({user.id}) had no items.")
		return

	# Prepare message for the user
	message_parts = ["🛒 <b>Список товаров:</b>"]
	for item in items:
		item_name = item.get('name', 'Неизвестный товар')
		item_price = item.get('price', 0.0)
		safe_item_name = item_name.replace('<', '&lt;').replace('>', '&gt;')
		message_parts.append(f"- {safe_item_name}: <code>{item_price:.2f}</code>")
	
	message_parts.append(f"\n🟰 <b>Итого:</b> <code>{total_amount:.2f}</code>")
	
	# Prepare data for printing payload
	try:
		tz = pytz.timezone('Europe/Moscow')
		current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
	except pytz.UnknownTimeZoneError:
		logger.warning("Unknown timezone 'Europe/Moscow', falling back to UTC for calculator print data.")
		current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

	print_data_payload = {
		'app_type': 'calculator',
		'items': items,
		'total': total_amount,
		's': user_identifier,
		't': current_time
	}

	base64_encoded_json_for_message = ""
	print_button = None
	try:
		json_string = json.dumps(print_data_payload, separators=(',', ':'))
		base64_encoded_json_for_message = base64.b64encode(json_string.encode('utf-8')).decode('utf-8')
		# Add the encoded data to the message text, prefixed by the new marker
		message_parts.append(f"\n\n{CALC_DATA_MARKER} {base64_encoded_json_for_message}") # Add to message
		
		print_button = InlineKeyboardButton(
			"🖨️ Print", 
			callback_data="print:parse_calculator_encoded" # Simple callback data
		)
			
	except Exception as e:
		logger.error(f"Failed to encode calculator data for printing: {e}", exc_info=True)
		# No button if encoding fails, data won't be in message either unless we add it before this block

	response_message = "\n".join(message_parts) # Join after potentially adding encoded data

	keyboard_buttons = []
	if print_button:
		keyboard_buttons.append([print_button])
	
	reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None

	try:
		await update.message.reply_text(
			text=response_message,
			parse_mode=ParseMode.HTML,
			reply_markup=reply_markup,
			disable_web_page_preview=True # Consistent with ticket app
		)
		logger.info(f"Calculator summary sent to user {user_identifier} ({user.id})")
	except Exception as e:
		logger.error(f"Error sending calculator summary to user {user_identifier} ({user.id}): {e}", exc_info=True)
		try:
			await update.message.reply_text("Извините, не удалось отобразить итоги калькулятора.")
		except Exception as e_notify:
			logger.error(f"Failed even to notify user {user_identifier} ({user.id}) about calculator error: {e_notify}")

def _parse_calculator_data(message_text: Optional[str]) -> Optional[dict[str, Any]]:
	"""Parses base64 encoded JSON calculator data from message text using CALC_DATA_MARKER."""
	if not message_text:
		return None
	# Use CALC_DATA_MARKER here
	match = re.search(rf"^{re.escape(CALC_DATA_MARKER)}\s+(.*)$", message_text, re.MULTILINE)
	if not match:
		logger.debug(f"CALC_DATA_MARKER not found in message text for parsing.")
		return None
	try:
		payload_b64 = match.group(1)
		payload_bytes = base64.b64decode(payload_b64)
		payload_str = payload_bytes.decode("utf-8")
		calculator_data = json.loads(payload_str)
		logger.debug(f"Successfully parsed calculator data: {calculator_data}")
		return calculator_data
	except Exception as e:
		logger.error(f"Failed to parse calculator data from message: {e}", exc_info=True)
		return None




# Label Dimensions & Resolution
MM2IN = 1.0 / 25.4
DPI = 203
LABEL_WIDTH_MM = 58
LABEL_HEIGHT_MM = 40
LABEL_WIDTH_PX = int(LABEL_WIDTH_MM * MM2IN * DPI)
LABEL_HEIGHT_PX = int(LABEL_HEIGHT_MM * MM2IN * DPI)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) # Get directory where the script is
FONT_DIR = os.path.join(SCRIPT_DIR, "fonts", "terminus-ttf-4.49.3")
FONT_BOLD_PATH = os.path.join(FONT_DIR, "TerminusTTF-Bold-4.49.3.ttf")
LOGO_PATH = os.path.join(SCRIPT_DIR, "logo.png")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "labels")
IRFANVIEW_EXECUTABLE_NAME = "i_view64.exe"
IRFANVIEW_ABS_PATH = os.path.join(SCRIPT_DIR, IRFANVIEW_EXECUTABLE_NAME)


# Font Sizes
FONT_SIZE_HEADER = 28
FONT_SIZE_BODY = 18
FONT_SIZE_TICKET_DETAILS = 24
FONT_SIZE_SMALL = 16


# Layout & Styling
MARGIN_MM = 2
LOGO_SIZE_MM = 13
COMMENT_BOX_WIDTH_MM = 20
COMMENT_BOX_HEIGHT_MM = 10
COMMENT_BOX_RADIUS_MM = 1.5
LINE_SPACING = 4 # Extra pixels between lines
TEXT_COLOR = "black"
BACKGROUND_COLOR = "white"
BORDER_COLOR = "black"
BORDER_WIDTH = 2

# Telegram Messages
MSG_GENERATING = "🖨️"
MSG_SUCCESS = "✅ Этикетка сгенерирована!" # Changed from "printed" as it only generates
MSG_ERR_NO_DATA = "❌ Ticket data not found in the message."
MSG_ERR_DECODE = "❌ Failed to decode ticket data."
MSG_ERR_GENERIC = "❌ An error occurred while generating the label."


def mm2px(mm: float) -> int:
	"""Converts millimeters to pixels based on DPI."""
	return int(mm * MM2IN * DPI)

def _load_fonts() -> Dict[str, ImageFont.FreeTypeFont]:
	"""Loads required fonts."""
	try:
		return {
			"header": ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_HEADER),
			"body": ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_BODY),
			"ticket_details": ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_TICKET_DETAILS), 
			"small": ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_SMALL),
		}
	except IOError as e:
		logger.error(f"Failed to load font: {e}")
		raise


def _draw_text_line(
	draw: ImageDraw.ImageDraw,
	text: str,
	font: ImageFont.FreeTypeFont,
	x: int,
	y: int,
	color: str = TEXT_COLOR,
	underline: bool = False,         # New parameter: True to underline
	underline_offset: int = 2,       # New parameter: Pixels below text baseline for underline
	underline_thickness: int = 1     # New parameter: Thickness of the underline
) -> int:
	"""Draws a line of text and returns the Y position for the next line."""
	# Draw the main text
	draw.text((x, y), text, font=font, fill=color)

	# Get font metrics
	ascent, descent = font.getmetrics() # ascent is height above baseline, descent is depth below

	if underline:
		# Calculate text width using getbbox for better accuracy
		# bbox is (left, top, right, bottom) relative to (0,0) if text drawn at origin
		# We only need its width (right - left)
		text_bbox = font.getbbox(text)
		text_width = text_bbox[2] - text_bbox[0]

		# Calculate Y position for the underline
		# y is the top of the text. Baseline is at y + ascent.
		y_underline = y + ascent + underline_offset

		# Draw the underline
		draw.line(
			[(x, y_underline), (x + text_width, y_underline)],
			fill=color,  # Use the same color as the text, or you can specify another
			width=underline_thickness
		)

	# Calculate the total height of the line for positioning the next line
	line_height = ascent + descent
	return y + line_height + LINE_SPACING

def _parse_ticket_data(message_text: Optional[str]) -> Optional[dict[str, Any]]:
	"""Parses base64 encoded JSON ticket data from message text."""
	if not message_text:
		return None
	match = re.search(rf"^{re.escape(TICKETS_DATA_MARKER)}\s+(.*)$", message_text, re.MULTILINE)
	if not match:
		return None
	try:
		payload_b64 = match.group(1)
		payload_bytes = base64.b64decode(payload_b64)
		payload_str = payload_bytes.decode("utf-8")
		ticket_data = json.loads(payload_str)
		return ticket_data
	except Exception as e:
		logger.error(f"Failed to parse ticket data: {e}")
		return None

def format_identifier_partial(identifier: str, keep_chars: int = 4, remove_leading_at: bool = False) -> str:
	if not identifier or identifier == 'N/A':
		return 'N/A'
	display_name = str(identifier)
	if remove_leading_at and display_name.startswith('@'):
		display_name = display_name[1:]
	if len(display_name) > keep_chars:
		return display_name[:keep_chars] + '...'
	else:
		return display_name


def _generate_ticket_label_image(ticket: Dict[str, Any]) -> Optional[Image.Image]:
	try:
		fonts = _load_fonts()
	except IOError:
		return None
	img = Image.new("RGB", (LABEL_WIDTH_PX, LABEL_HEIGHT_PX), BACKGROUND_COLOR)
	draw = ImageDraw.Draw(img)
	margin_px = mm2px(MARGIN_MM)
	current_y = margin_px # This is the Y position for the start of the header

	# --- Header Section ---
	try:
		logo = Image.open(LOGO_PATH).convert("RGBA")
		logo_size_px = mm2px(LOGO_SIZE_MM)
		img.paste(logo, (margin_px, margin_px), logo)
		header_text_x = margin_px + logo_size_px + mm2px(1)
		header_y = margin_px # Start header text at the top margin
		header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "ул Советская 26, г. Керчь", fonts["body"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "+7 (978) 762‑8967", fonts["body"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "+7 (978) 010‑4949", fonts["body"], header_text_x, header_y)
		banner_height = max(margin_px + logo_size_px, header_y) + mm2px(1) # Calculate banner position
		draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
		current_y = banner_height + mm2px(2) # Update current_y to be below the header banner
	except FileNotFoundError:
		logger.warning(f"Logo file not found at {LOGO_PATH}. Skipping logo.")
		header_text_x = margin_px
		header_y = margin_px # Start header text at the top margin
		header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
		# Note: If logo is not found, the address and company phones are not drawn in the current code.
		banner_height = header_y + mm2px(1) # Calculate banner position
		draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
		current_y = banner_height + mm2px(2) # Update current_y to be below the header banner
	except Exception as e:
		logger.error(f"Error drawing header: {e}")

	# --- Ticket Details Section ---
	body_x = margin_px
	body_y = current_y # Start ticket details below the header
	
	raw_identifier = ticket.get('s', 'N/A')
	display_identifier = format_identifier_partial(raw_identifier, keep_chars=6)

	body_y = _draw_text_line(draw, f"Принял(а): {display_identifier}", fonts["ticket_details"], body_x, body_y)
	raw_phone_for_label = ticket.get('p', 'N/A')
	formatted_phone_for_label = format_phone_number_display(raw_phone_for_label) # Use the new function
	body_y = _draw_text_line(draw, f"Телефон: {formatted_phone_for_label}", fonts["ticket_details"], body_x, body_y) 
	body_y = _draw_text_line(draw, f"Время: {ticket.get('t', 'N/A')}", fonts["ticket_details"], body_x, body_y)


	# --- Description Section ---
	# desc_y calculation is based on body_y, which is the Y position after "Время:"
	desc_y = body_y + mm2px(1) 
	desc_y = _draw_text_line(draw, "Описание:", fonts["ticket_details"], body_x, desc_y)
	
	description = ticket.get("d", "")
	wrap_width = 46
	wrapped_lines = textwrap.wrap(description, width=wrap_width) # Get all wrapped lines

	lines_to_draw = []
	if len(wrapped_lines) > 2:
		lines_to_draw.append(wrapped_lines[0]) # Keep the first line as is
		# For the second line, ensure it fits and add "..."
		# We need to make sure the "..." doesn't make the line too long.
		# A simple approach is to take a substring of the second line and append "..."
		# A more precise way would be to check the width with the font, but for fixed-width textwrap, this should be okay.
		second_line_text = wrapped_lines[1]
		if len(second_line_text) > wrap_width - 3: # Check if space is needed for "..."
			lines_to_draw.append(second_line_text[:wrap_width-3] + "...")
		else:
			lines_to_draw.append(second_line_text + "...") # Or just append if there's space (less likely with textwrap)

	elif len(wrapped_lines) > 0: # If 1 or 2 lines
		lines_to_draw = wrapped_lines[:2] # Take the first two lines or the only line

	for line_text in lines_to_draw:
		desc_y = _draw_text_line(draw, line_text, fonts["body"], body_x, desc_y)

	return img




MAX_ITEMS_ON_LABEL = 14 # Adjust based on testing and desired font size

def _generate_calculator_label_image(calc_data: Dict[str, Any]) -> Optional[Image.Image]:
	try:
		fonts = _load_fonts()
	except IOError:
		logger.error("Failed to load fonts for calculator label.")
		return None

	img = Image.new("RGB", (LABEL_WIDTH_PX, LABEL_HEIGHT_PX), BACKGROUND_COLOR)
	draw = ImageDraw.Draw(img)
	margin_px = mm2px(MARGIN_MM)
	current_y = margin_px

	# --- Header Section (Copied and adapted from _generate_ticket_label_image) ---
	try:
		logo = Image.open(LOGO_PATH).convert("RGBA")
		logo_size_px = mm2px(LOGO_SIZE_MM)
		# Calculate position to center logo horizontally if desired, or keep left aligned
		# For simplicity, keeping it left-aligned similar to ticket
		img.paste(logo, (margin_px, margin_px), logo) # Pasting logo at top-left margin
		header_text_x = margin_px + logo_size_px + mm2px(1) # Text starts after logo
		header_y = margin_px # Start header text at the top margin

		# Draw header text lines
		header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "ул Советская 26, г. Керчь", fonts["body"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "+7 (978) 762‑8967", fonts["body"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "+7 (978) 010‑4949", fonts["body"], header_text_x, header_y)

		# Calculate banner position based on the taller of logo or text block
		banner_height = max(margin_px + logo_size_px, header_y) + mm2px(1)
		draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
		current_y = banner_height + mm2px(2) # Update current_y to be below the header banner
	except FileNotFoundError:
		logger.warning(f"Logo file not found at {LOGO_PATH}. Skipping logo for calculator label.")
		# Fallback if logo is not found - draw text starting from margin
		header_text_x = margin_px
		header_y = margin_px
		header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "ул Советская 26, г. Керчь", fonts["body"], header_text_x, header_y)
		header_y = _draw_text_line(draw, "+7 (978) 762‑8967", fonts["body"], header_text_x, header_y)
		
		banner_height = header_y + mm2px(1) # Calculate banner position without logo
		draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
		current_y = banner_height + mm2px(2)
	except Exception as e:
		logger.error(f"Error drawing header for calculator label: {e}")
		# If header fails, we might want to return None or continue without a header
		# For now, let's assume we start drawing items from the top margin if header fails catastrophically
		current_y = margin_px


	# --- Calculator Details Section ---
	body_x = margin_px
	# current_y is already set after the header

	items_list = calc_data.get('items', [])
	total_amount = calc_data.get('total', 0.0)

	# --- Items List ---
	price_area_width_px = mm2px(15) # Reserve space for price on the right

	# Use "body" font for items
	item_font = fonts["body"] # CHANGED FONT HERE
	item_font_small = fonts["small"] # For truncation message


	# Estimate height needed for total and separator
	total_label_text = "Итого:" # Text for measuring
	total_line_height_estimate = item_font.getmetrics()[0] + item_font.getmetrics()[1] + LINE_SPACING + mm2px(2) # Height of total line
	separator_height = mm2px(1) + LINE_SPACING # Height of separator line + spacing
	# Check if there's enough space for the current item and the total line
	# This needs to be more dynamic based on actual text height
	ascent, descent = item_font.getmetrics()
	item_line_height = ascent + descent + LINE_SPACING // 2

	for i, item_data in enumerate(items_list):
		if current_y + item_line_height + separator_height + total_line_height_estimate > LABEL_HEIGHT_PX - margin_px:
			# Not enough space for this item AND the total section
			# Draw truncation message if not all items are displayed
			if i < len(items_list):
				trunc_msg = f"...и еще {len(items_list) - i} товар(ов)"
				# Check if truncation message fits
				if current_y + item_font_small.getmetrics()[0] + item_font_small.getmetrics()[1] < LABEL_HEIGHT_PX - margin_px - total_line_height_estimate - separator_height:
					_draw_text_line(draw, trunc_msg, item_font_small, body_x, current_y)
					current_y += item_font_small.getmetrics()[0] + item_font_small.getmetrics()[1] + LINE_SPACING // 2
			break # Stop adding items

		if i >= MAX_ITEMS_ON_LABEL : # Fallback hard limit
			# This check might be redundant if the height check above is effective
			available_height_for_trunc_msg = LABEL_HEIGHT_PX - current_y - margin_px - total_line_height_estimate - separator_height
			if item_font_small.getbbox("...и еще")[3] < available_height_for_trunc_msg:
				_draw_text_line(draw, f"...и еще {len(items_list) - MAX_ITEMS_ON_LABEL} товар(ов)", item_font_small, body_x, current_y)
				current_y += item_font_small.getmetrics()[0] + item_font_small.getmetrics()[1] + LINE_SPACING // 2
			break


		item_name = item_data.get('name', 'N/A')
		item_price = item_data.get('price', 0.0)
		
		max_name_width = LABEL_WIDTH_PX - (2 * margin_px) - price_area_width_px - mm2px(2)
		
		# Truncate item name if it's too long
		# This truncation logic might need refinement with the new font
		display_name = item_name
		# Check actual width of the text
		if item_font.getlength(display_name) > max_name_width:
			# Simple character based truncation, might need to be smarter
			avg_char_width_approx = item_font.getlength("X") # Approximate
			if avg_char_width_approx > 0:
				max_chars = int(max_name_width / avg_char_width_approx)
				if len(display_name) > max_chars :
					display_name = display_name[:max_chars - 3] + "..." if max_chars > 3 else display_name[:max_chars]


		# Draw item name (left aligned)
		draw.text((body_x, current_y), display_name, font=item_font, fill=TEXT_COLOR)
		
		# Draw item price (right aligned)
		price_text = f"{item_price:.2f}"
		price_text_length = item_font.getlength(price_text)
		price_x = LABEL_WIDTH_PX - margin_px - price_text_length
		draw.text((price_x, current_y), price_text, font=item_font, fill=TEXT_COLOR)

		current_y += item_line_height
		
		# This inner break condition might be too aggressive or needs re-evaluation with new font.


	# --- Total Amount ---
	# Ensure there's a little space before the separator line, if not already handled by item loop break
	if current_y + separator_height + total_line_height_estimate < LABEL_HEIGHT_PX - margin_px:
		current_y += mm2px(1) # Small space before total separator
		_draw_text_line(draw, "----------------------------------", fonts["body"], body_x, current_y, color=BORDER_COLOR) # Separator line
		current_y += mm2px(1) # Space after separator, before total text. Adjust LINE_SPACING in _draw_text_line if this is too much
	
		total_str = f"Итого: {total_amount:.2f}"
		total_text_length = item_font.getlength(total_str) # Use item_font or a specific total_font
		total_x = LABEL_WIDTH_PX - margin_px - total_text_length # Right align total
		
		_draw_text_line(draw, total_str, item_font, int(total_x), current_y, color=TEXT_COLOR)
	else:
		logger.warning("Not enough space to draw the total amount on calculator label.")

	return img


def _save_label_image(
	image: Image.Image,
	ticket: Dict[str, Any],
	output_dir: str = OUTPUT_DIR,
	dpi: Tuple[int, int] = (DPI, DPI)
) -> Optional[str]:
	os.makedirs(output_dir, exist_ok=True)
	user_identifier = ticket.get("s", "unknown_user")
	timestamp = ticket.get("t", "unknown_time")
	safe_user = re.sub(r"\W+", "_", user_identifier)
	timestamp_safe = timestamp.replace(" ", "_").replace(":", "-")
	file_name = f"label_{safe_user}_{timestamp_safe}.png"
	relative_file_path = os.path.join(output_dir, file_name)
	absolute_file_path = os.path.abspath(relative_file_path)
	try:
		image.save(absolute_file_path, format="PNG", dpi=dpi)
		logger.info(f"Label saved to disk at {absolute_file_path}")
		return absolute_file_path
	except Exception as e:
		logger.error(f"Failed to save image to {absolute_file_path}: {e}")
		return None

async def handle_ticket_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	query = update.callback_query
	if not query:
		logger.warning("Received callback event without query object.")
		return

	await query.answer() # Acknowledge the callback

	# Check if the message associated with the callback is accessible
	if not query.message or not isinstance(query.message, Message):
		logger.warning("Ticket print callback: Original message is not accessible or not a standard message.")
		# Optionally, try to send a message to the user who pressed the button if possible
		try:
			await context.bot.send_message(
				chat_id=query.from_user.id, 
				text="Не удалось обработать ваш запрос: исходное сообщение для печати заявки недоступно."
			)
		except Exception as e:
			logger.error(f"Failed to notify user about inaccessible message: {e}")
		return

	temp_msg = await query.message.reply_text(MSG_GENERATING) 
	try:
		ticket_data = _parse_ticket_data(query.message.text) 
		if ticket_data is None:
			await query.message.reply_text(MSG_ERR_NO_DATA if not query.message.text or TICKETS_DATA_MARKER not in query.message.text else MSG_ERR_DECODE)
			return
		label_image = _generate_ticket_label_image(ticket_data)
		if label_image is None:
			await query.message.reply_text(MSG_ERR_GENERIC)
			return
		file_path = _save_label_image(label_image, ticket_data, output_dir=os.path.join(OUTPUT_DIR, "ticket_labels"))
		if file_path is None:
			await query.message.reply_text("❌ Failed to save the label image.") 
			return
		printer_name = context.bot_data.get('printer_name')
		if printer_name and file_path:
			logger.info(f"Printer name '{printer_name}' provided. Attempting to print {file_path}")
			print_command = [
					IRFANVIEW_ABS_PATH,
					file_path,
					f'/print="{printer_name}"',
					f'/dpi="({DPI},{DPI})"',
					f'/ini="{SCRIPT_DIR}"'
				]
			logger.info(f"Executing print command: {' '.join(print_command)}")
			result = subprocess.run(
						' '.join(print_command),
						shell=True,
						check=False,
						capture_output=True,
						text=True,
						timeout=30,
					)
			if result.returncode == 0:
				logger.info(f"IrfanView print command successful for {file_path}. Output: {result.stdout}")
			else:
				logger.error(f"IrfanView print command failed for {file_path}. Return Code: {result.returncode}. Stderr: {result.stderr}. Stdout: {result.stdout}")
		else:
			 # If no printer_name, just confirm generation and provide path
			await query.message.reply_photo(photo=open(file_path, 'rb'), caption=MSG_SUCCESS) 
	except Exception as e:
		logger.exception("Unhandled error in handle_ticket_print_callback")
		await query.message.reply_text(MSG_ERR_GENERIC) 
	finally:
		if temp_msg:
			try:
				await context.bot.delete_message(
					chat_id=temp_msg.chat.id,
					message_id=temp_msg.message_id
				)
			except Exception as e:
				logger.warning(f"Failed to delete temporary message: {e}")


async def handle_calculator_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	query = update.callback_query
	if not query:
		logger.warning("Received callback event without query object.")
		return

	await query.answer() # Acknowledge the callback

	# Check if the message associated with the callback is accessible
	if not query.message or not isinstance(query.message, Message):
		logger.warning("Calculator print callback: Original message is not accessible or not a standard message.")
		# Optionally, try to send a message to the user who pressed the button if possible
		try:
			await context.bot.send_message(
				chat_id=query.from_user.id, 
				text="Не удалось обработать ваш запрос: исходное сообщение для печати чека недоступно."
			)
		except Exception as e:
			logger.error(f"Failed to notify user about inaccessible message: {e}")
		return
	
	# Parse data from the message text using the new parser
	calculator_data = _parse_calculator_data(query.message.text) # Parse from query.message.text

	if calculator_data is None:
		# Determine if it was a parsing error or marker not found
		err_msg = MSG_ERR_DECODE if query.message.text and CALC_DATA_MARKER in query.message.text else MSG_ERR_NO_DATA
		logger.warning(f"Failed to get calculator_data from message. Text was: '{query.message.text[:100]}...'") # Log snippet
		await query.message.reply_text(err_msg)
		return
		
	temp_msg = await query.message.reply_text(MSG_GENERATING)

	try:
		# The 'app_type': 'calculator' check is still good if present in parsed data
		if calculator_data.get('app_type') != 'calculator':
			await query.message.reply_text("Данные не от калькулятора.")
			logger.warning("Calculator print callback received non-calculator data after parsing.")
			if temp_msg: await context.bot.delete_message(chat_id=temp_msg.chat.id, message_id=temp_msg.message_id)
			return

		label_image = _generate_calculator_label_image(calculator_data) # This function remains the same
		if label_image is None:
			await query.message.reply_text(MSG_ERR_GENERIC)
			if temp_msg: await context.bot.delete_message(chat_id=temp_msg.chat.id, message_id=temp_msg.message_id)
			return

		file_path = _save_label_image(label_image, calculator_data, output_dir=os.path.join(OUTPUT_DIR, "calculator_labels"))
		if file_path is None:
			await query.message.reply_text("❌ Не удалось сохранить изображение чека.")
			if temp_msg: await context.bot.delete_message(chat_id=temp_msg.chat.id, message_id=temp_msg.message_id)
			return

		printer_name = context.bot_data.get('printer_name')
		if printer_name:
			logger.info(f"Printer name '{printer_name}' provided. Attempting to print calculator label {file_path}")
			print_command = [
				IRFANVIEW_ABS_PATH, file_path, f'/print="{printer_name}"',
				f'/dpi="({DPI},{DPI})"', f'/ini="{SCRIPT_DIR}"'
			]
			logger.info(f"Executing print command: {' '.join(print_command)}")
			result = subprocess.run(
				' '.join(print_command), shell=True, check=False,
				capture_output=True, text=True, timeout=30
			)
			if result.returncode == 0:
				logger.info(f"IrfanView print command successful for {file_path}. Output: {result.stdout}")
			else:
				logger.error(f"IrfanView print command failed for {file_path}. RC: {result.returncode}. Err: {result.stderr}. Out: {result.stdout}")
				await query.message.reply_text(f"⚠️ Ошибка печати чека. Код: {result.returncode}. Проверьте логи.")
		else:
			await query.message.reply_photo(photo=open(file_path, 'rb'), caption="✅ Чек сгенерирован!")

	except Exception as e:
		logger.exception("Unhandled error in handle_calculator_print_callback")
		await query.message.reply_text(MSG_ERR_GENERIC)
	finally:
		if temp_msg:
			try:
				await context.bot.delete_message(chat_id=temp_msg.chat.id, message_id=temp_msg.message_id)
			except Exception as e_del:
				logger.warning(f"Failed to delete temporary message: {e_del}")



async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if not update.effective_message or not update.effective_message.web_app_data:
		logger.warning("Received update without effective_message or web_app_data")
		return
	logger.info("Received data from a Web App.")
	raw_data = update.effective_message.web_app_data.data
	try:
		data = json.loads(raw_data)
		logger.log(DEBUG, f"Web App data received: {data}")
		app_origin = data.get('app_origin')
		if app_origin == 'ticket_app':
			await process_ticket_app_data(update, context, data)
		elif app_origin == 'calculator_app':
			await process_calculator_app_data(update, context, data) #
		else:
			logger.warning(f"Received data from unknown or missing app_origin: {app_origin}. Data: {data}")
			await update.message.reply_text(
				"Received data, but could not determine its origin or type. Use /start to try again."
			)
	except json.JSONDecodeError:
		logger.error(f"Failed to decode JSON data from Web App: {raw_data}")
		await update.message.reply_text(
			"⚠️ There was an error processing the data structure from the web app. Please try again via /start."
		)
	except Exception as e:
		logger.exception(f"An unexpected error occurred processing Web App data: {e}")
		await update.message.reply_text(
			"⚠️ An unexpected error occurred. Please try again later via /start."
		)


def connect_db(db_path: str) -> Optional[sqlite3.Connection]:
	"""Establishes a connection to the SQLite database."""
	try:
		conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) # Read-only connection
		conn.row_factory = sqlite3.Row # Access columns by name
		logger.info(f"Successfully connected to database (read-only): {db_path}")
		return conn
	except sqlite3.Error as e:
		logger.error(f"Error connecting to database {db_path}: {e}")
		return None

def get_initial_max_case_id(db_path: str) -> int:
	"""Gets the maximum primkey_case from the database on startup."""
	conn = connect_db(db_path)
	max_id = 0
	if conn:
		try:
			cursor = conn.cursor()
			cursor.execute("SELECT MAX(primkey_case) FROM cases")
			result = cursor.fetchone()
			if result and result[0] is not None:
				max_id = int(result[0])
				logger.info(f"Initial maximum primkey_case from DB: {max_id}")
			else:
				logger.info("No cases found in DB or primkey_case is NULL, starting with 0.")
		except sqlite3.Error as e:
			logger.error(f"Error fetching max primkey_case: {e}")
		finally:
			conn.close()
	return max_id

def get_new_cases_from_db(db_path: str, last_id: int) -> list[sqlite3.Row]:
	"""Fetches new cases from the database with primkey_case > last_id,
	   including fellow's nickname."""
	conn = connect_db(db_path)
	new_cases = []
	if conn:
		try:
			cursor = conn.cursor()
			# Updated query to LEFT JOIN with fellows table
			query = """
				SELECT 
					c.primkey_case, c.case_number, c.department, c.type, c.manufacturer, 
					c.model, c.serial, c.reason, c.equipment, c.defects, c.condition, 
					c.fellow, c.client, c.phone, c.dp_phone, c.date_input, 
					c.note_output, c.client_text,
					f.fellow_nickname, f.fellow_name 
				FROM cases c
				LEFT JOIN fellows f ON c.fellow = f.primkey_fellow
				WHERE c.primkey_case > ?
				ORDER BY c.primkey_case ASC
			"""
			cursor.execute(query, (last_id,))
			new_cases = cursor.fetchall()
			if new_cases:
				logger.info(f"Fetched {len(new_cases)} new case(s) from DB since ID {last_id} (with fellow info).")
		except sqlite3.Error as e:
			logger.error(f"Error fetching new cases from DB (with fellow info): {e}")
		finally:
			conn.close()
	return new_cases

def format_unix_timestamp(ts: Optional[int]) -> str:
	"""Formats a Unix timestamp into a human-readable string (Moscow time)."""
	if ts is None:
		return "N/A"
	try:
		tz = pytz.timezone('Europe/Moscow')
		return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
	except pytz.UnknownTimeZoneError:
		logger.warning("Unknown timezone 'Europe/Moscow' for DB timestamp, falling back to UTC.")
		return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M (UTC)")
	except Exception:
		return str(ts) # Fallback


# --- Add these helper functions ---
def load_last_known_id_from_file(file_path: str) -> Optional[int]:
	"""Loads the last known processed database case ID from a file."""
	try:
		if os.path.exists(file_path):
			with open(file_path, 'r') as f:
				content = f.read().strip()
				if content.isdigit():
					logger.info(f"Successfully read last known ID {content} from {file_path}")
					return int(content)
				else:
					logger.warning(f"Invalid content in {file_path}: '{content}'. Expected an integer.")
					return None
		else:
			logger.info(f"Last known ID file {file_path} not found.")
			return None
	except Exception as e:
		logger.error(f"Error loading last known ID from {file_path}: {e}", exc_info=True)
		return None

def save_last_known_id_to_file(file_path: str, last_id: int) -> None:
	"""Saves the last known processed database case ID to a file."""
	try:
		os.makedirs(os.path.dirname(file_path), exist_ok=True) # Ensure directory exists
		with open(file_path, 'w') as f:
			f.write(str(last_id))
		logger.info(f"Successfully saved last known DB case ID {last_id} to {file_path}")
	except Exception as e:
		logger.error(f"Error saving last known ID {last_id} to {file_path}: {e}", exc_info=True)





async def process_and_send_db_case(
	bot: telegram.Bot,
	case_data: sqlite3.Row,
	context: ContextTypes.DEFAULT_TYPE
) -> None:
	"""
	Processes a single case from the database and sends it as a ticket message,
	formatted similarly to web app tickets using a concatenated f-string style.
	"""
	logger.info(f"Processing database case ID: {case_data['primkey_case']}")

	# --- User Identifier ---
	submitter_id = case_data['fellow']
	fellow_nickname = case_data['fellow_nickname']
	fellow_name = case_data['fellow_name']
	user_identifier = "DB Sync" # Default if no fellow info
	if fellow_nickname:
		user_identifier = fellow_nickname
	elif fellow_name:
		user_identifier = fellow_name
	elif submitter_id: # Fallback to fellow ID if name/nickname missing
		user_identifier = f"Сотрудник ID: {submitter_id}"

	# --- Time ---
	formatted_time = format_unix_timestamp(case_data['date_input'])

	# --- Phone Number ---
	raw_phone_db = case_data['phone'] if case_data['phone'] else case_data['dp_phone']
	raw_phone_str = str(raw_phone_db) if raw_phone_db is not None else 'N/A'
	formatted_phone_display = format_phone_number_display(raw_phone_str)
	phone_link_html = f'<b><code>{formatted_phone_display}</code></b>'
	if raw_phone_str and raw_phone_str != 'N/A':
		tel_url_number = None
		if raw_phone_str.startswith('+7') and len(raw_phone_str) == 12:
			tel_url_number = raw_phone_str
		elif (raw_phone_str.startswith('8') or raw_phone_str.startswith('7')) and len(raw_phone_str) == 11:
			if raw_phone_str.startswith('8'):
				tel_url_number = '+7' + raw_phone_str[1:]
			elif raw_phone_str.startswith('7'): # Handle if phone is like 7978...
				tel_url_number = '+' + raw_phone_str
		if tel_url_number:
			tel_url = f"tel:{tel_url_number}"
			phone_link_html = f'<a href="{tel_url}"><b><code>{formatted_phone_display}</code></b></a>'

	# --- Description Logic (constructs 'description_from_db_fields' variable) ---
	db_device_model_parts = []
	if case_data['type']: db_device_model_parts.append(case_data['type'])
	if case_data['manufacturer']: db_device_model_parts.append(case_data['manufacturer'])
	if case_data['model']: db_device_model_parts.append(case_data['model'])
	if case_data['serial']: db_device_model_parts.append(f"(S/N: {case_data['serial']})")
	html_form_deviceModel_db = " ".join(filter(None, db_device_model_parts)).strip()
	if not html_form_deviceModel_db:
		html_form_deviceModel_db = "Модель техники не указана"

	db_issue_desc_parts = []
	if case_data['reason']: db_issue_desc_parts.append(case_data['reason'])
	html_form_issueDescription_db = ". ".join(filter(None, db_issue_desc_parts)).strip()
	if not html_form_issueDescription_db:
		html_form_issueDescription_db = "Характер неисправности не указан"

	html_form_accessories_db = (case_data['equipment'] or "").strip()

	description_from_db_fields = f"{html_form_deviceModel_db}. {html_form_issueDescription_db}"
	if html_form_accessories_db:
		description_from_db_fields += f". {html_form_accessories_db}"

	description_from_db_fields = re.sub(r'\s*\.\s*', '. ', description_from_db_fields)
	description_from_db_fields = re.sub(r'\.{2,}', '.', description_from_db_fields)
	description_from_db_fields = description_from_db_fields.strip().rstrip('.')
	if description_from_db_fields:
		description_from_db_fields += "."
	if not description_from_db_fields or description_from_db_fields == ".":
		description_from_db_fields = "Детальное описание по заявке из БД отсутствует."
	# --- End of Description Logic ---

	# --- Prepare data for encoding (for potential "Print" button) ---
	ticket_details_to_encode = {
		'p': raw_phone_str,
		'd': description_from_db_fields,
		's': user_identifier,
		't': formatted_time,
	}

	base64_encoded_json = "" # Will be empty if encoding fails
	try:
		json_string = json.dumps(ticket_details_to_encode, separators=(',', ':'), ensure_ascii=False)
		base64_encoded_json = base64.b64encode(json_string.encode('utf-8')).decode('utf-8')
	except Exception as e:
		logger.error(f"Failed to encode DB ticket details (ID: {case_data['primkey_case']}): {e}", exc_info=True)

	print_button = InlineKeyboardButton("🖨️ Print", callback_data="print:parse_encoded")
	keyboard = InlineKeyboardMarkup([[print_button]])

	# --- Assemble the message text in the desired style ---
	case_id_display = case_data['case_number'] or case_data['primkey_case']
	client_name_db = case_data['client'] or 'N/A'

	# Prepare conditional string parts first
	client_info_str_part = ""
	if client_name_db != 'N/A':
		client_info_str_part = f"👤 Клиент: {client_name_db}\n\n"
	else:
		# If no client, ensure a newline for consistent spacing before encoded data
		client_info_str_part = "\n"

	encoded_data_str_part = ""
	if base64_encoded_json: # True if base64_encoded_json is not an empty string
		encoded_data_str_part = f"{TICKETS_DATA_MARKER} {base64_encoded_json}\n\n"

	# Construct the message using a list with one main concatenated f-string,
	# similar to the process_ticket_app_data example.
	# This list will contain a single string element due to Python's adjacent string literal concatenation.
	db_message_constructor_list = [
		f"✅ Заявка создана! (Детали из БД, № {case_id_display})\n\n"
		f"👤 Отправил(а): {user_identifier}\n"
		f"🕒 Время: {formatted_time}\n"
		f"--- Детали заявки ---\n"
		f"📞 Телефон: {phone_link_html}\n"
		f"📝 Описание: {description_from_db_fields}\n"  # Description ends with one newline
		f"{client_info_str_part}"  # Appends client info (with its own newlines) or just a newline
		f"{encoded_data_str_part}" # Appends encoded data (with its newlines) or nothing
	]

	message_text = "".join(db_message_constructor_list) # Effectively gets the single concatenated string

	# --- Sending logic ---
	debug_mode = context.bot_data.get('debug_mode', False)
	if not debug_mode and TARGET_CHANNEL_ID:
		try:
			await bot.send_message(
				chat_id=TARGET_CHANNEL_ID,
				text=message_text,
				reply_markup=keyboard,
				parse_mode=ParseMode.HTML,
				disable_web_page_preview=True
			)
			logger.info(f"DB Ticket (Case ID: {case_data['primkey_case']}) posted to channel {TARGET_CHANNEL_ID}")
		except Exception as e_channel:
			logger.error(f"Failed to send DB Ticket (Case ID: {case_data['primkey_case']}) TO CHANNEL {TARGET_CHANNEL_ID}: {e_channel}", exc_info=True)
	elif debug_mode:
		logger.info(f"DEBUG MODE: Suppressed message to channel for DB Ticket (Case ID: {case_data['primkey_case']}). Content:\n{message_text}")
	elif not TARGET_CHANNEL_ID:
		logger.warning("TARGET_CHANNEL_ID is not set. Cannot send DB ticket to channel.")


class DatabaseChangeHandler(FileSystemEventHandler):
	def __init__(self, application: Application, db_path: str, db_id_storage_file_path: str): # MODIFIED
		super().__init__()
		self.application = application
		self.db_path = db_path
		self.db_id_storage_file_path = db_id_storage_file_path # ADDED: Store path to ID file
		self._last_processed_event_time = 0
		self._processing_lock = asyncio.Lock()

	def on_modified(self, event):
		if event.is_directory or os.path.realpath(event.src_path) != os.path.realpath(self.db_path):
			return

		current_time = time.time()
		if current_time - self._last_processed_event_time < DB_PROCESSING_DELAY:
			logger.debug(f"DB modification event for {event.src_path} too soon, skipping.")
			return
		self._last_processed_event_time = current_time
		
		logger.info(f"Database file {event.src_path} has been modified. Scheduling check.")
		
		self.application.job_queue.run_once(
			self.process_db_changes_callback,
			when=DB_PROCESSING_DELAY,
			name=f"db_check_{current_time}"
		)

	async def process_db_changes_callback(self, context: ContextTypes.DEFAULT_TYPE) -> None:
		"""Callback executed by JobQueue to process DB changes."""
		async with self._processing_lock:
			logger.info("JobQueue: Checking for new database entries...")
			# This ID comes from bot_data, which was initialized from file or DB at startup
			last_known_id = context.bot_data.get(LAST_KNOWN_DB_CASE_ID_KEY, 0)
			
			try:
				new_cases = await asyncio.to_thread(get_new_cases_from_db, self.db_path, last_known_id)
			except Exception as e:
				logger.error(f"Error in asyncio.to_thread(get_new_cases_from_db): {e}")
				return

			if not new_cases:
				logger.info("JobQueue: No new cases found in the database.")
				return

			max_id_in_batch = last_known_id
			successfully_processed_any_case = False # To track if any case in the batch was sent
			for case_row in new_cases:
				try:
					await process_and_send_db_case(self.application.bot, case_row, context)
					if case_row['primkey_case'] > max_id_in_batch:
						max_id_in_batch = case_row['primkey_case']
					successfully_processed_any_case = True # Mark success
				except Exception as e:
					logger.error(f"Error processing DB case ID {case_row['primkey_case']}: {e}", exc_info=True)
			
			# Update the last known ID in bot_data and save to file
			# Only update if new cases were processed successfully
			if successfully_processed_any_case and max_id_in_batch > last_known_id:
				context.bot_data[LAST_KNOWN_DB_CASE_ID_KEY] = max_id_in_batch
				logger.info(f"JobQueue: Updated last known DB case ID in bot_data to {max_id_in_batch}")
				# Save to the persistent file
				save_last_known_id_to_file(self.db_id_storage_file_path, max_id_in_batch)
			elif not successfully_processed_any_case and new_cases:
				logger.warning("JobQueue: New cases were fetched, but none were successfully processed. Last known ID file not updated.")
			elif max_id_in_batch <= last_known_id and new_cases: # Should not happen if get_new_cases_from_db works correctly
				logger.info("JobQueue: Processed cases but max_id_in_batch did not advance. ID file not updated.")

# --- Main Execution ---

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Telegram Multi-WebApp Bot (ReplyKeyboard Menu)")
	parser.add_argument('--token', required=True, help='Telegram Bot Token')
	parser.add_argument(
		'--print',
		dest='printer_name', 
		default=None,
		help='Specify the network printer name for IrfanView printing (e.g., "MyPrinter" or "\\\\Server\\PrinterShare")'
	)
	parser.add_argument('--log-file', help='Path to log file (logs to console if omitted)')
	parser.add_argument('--log-level', default='INFO',
						choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
						help='Set the logging level')
	parser.add_argument('--debug', action='store_true', help='Enable debug mode (no channel messages, potentially different web app URLs)')
	# New argument for database path
	parser.add_argument('--db-file', help='Path to the SQLite database file to monitor.')

	args = parser.parse_args()

	# --- Logging Setup (remains the same) ---
	if args.log_file:
		log_path = os.path.join(SCRIPT_DIR, args.log_file)
		os.makedirs(os.path.dirname(log_path), exist_ok=True)
	else:
		log_path = None 

	logging.basicConfig(
		filename=log_path, 
		format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
		level=getattr(logging, args.log_level.upper())
	)
	logging.getLogger("httpx").setLevel(logging.WARNING) 
	logging.getLogger("watchdog").setLevel(logging.INFO) # Adjust watchdog's own logger if too verbose

	logger.info("Starting bot...")

	application = Application.builder() \
		.token(args.token) \
		.post_init(post_init) \
		.build()

	application.bot_data['debug_mode'] = args.debug
	if args.debug:
		logger.info("Debug mode ENABLED.")
	else:
		logger.info("Debug mode DISABLED.")

	if args.printer_name:
		logger.info(f"Printer name specified: '{args.printer_name}'. Printing will be enabled.")
		application.bot_data['printer_name'] = args.printer_name
	else:
		logger.info("No printer name specified. Printing via IrfanView is disabled.")
		application.bot_data['printer_name'] = None

  	# --- Database Monitoring Setup ---
	observer = None
	db_id_storage_file_path = None 
	event_handler = None # Initialize event_handler

	if args.db_file:
		DB_FILE_PATH = os.path.realpath(args.db_file)
		db_name_for_file = os.path.splitext(os.path.basename(DB_FILE_PATH))[0]
		id_storage_filename = DB_ID_STORAGE_FILENAME_TEMPLATE.format(db_name=db_name_for_file)
		db_id_storage_file_path = os.path.join(SCRIPT_DIR, "bot_data", id_storage_filename)

		if os.path.exists(DB_FILE_PATH):
			logger.info(f"Database monitoring ENABLED for: {DB_FILE_PATH}")
			logger.info(f"Last processed ID will be managed in: {db_id_storage_file_path}")
			
			os.makedirs(os.path.dirname(db_id_storage_file_path), exist_ok=True)

			current_last_known_id = load_last_known_id_from_file(db_id_storage_file_path)

			if current_last_known_id is None:
				logger.info(f"No valid last known ID found in {db_id_storage_file_path} or file doesn't exist. Fetching from DB as a fallback.")
				current_last_known_id = get_initial_max_case_id(DB_FILE_PATH)
				logger.info(f"Initial max primkey_case from DB: {current_last_known_id}. Saving this to file.")
				save_last_known_id_to_file(db_id_storage_file_path, current_last_known_id)
			else:
				logger.info(f"Loaded last known DB case ID from file: {current_last_known_id}")

			application.bot_data[LAST_KNOWN_DB_CASE_ID_KEY] = current_last_known_id
			logger.info(f"Set initial last known DB case ID for this session to: {current_last_known_id}")

			# Create the event handler instance
			event_handler = DatabaseChangeHandler(application, DB_FILE_PATH, db_id_storage_file_path)
			observer = Observer()
			# Watch the directory containing the DB file
			observer.schedule(event_handler, os.path.dirname(DB_FILE_PATH), recursive=False)
			
			# --- MODIFICATION FOR IMMEDIATE SYNC ---
			# Schedule an immediate check using the handler's processing method
			application.job_queue.run_once(
				event_handler.process_db_changes_callback, 
				when=0,  # Run immediately
				name="initial_db_sync",
				job_kwargs={'misfire_grace_time': 30}
			)
			logger.info("Scheduled an immediate database synchronization check.")
			# --- END OF MODIFICATION ---

			observer.start()
			logger.info(f"Watchdog observer started for directory: {os.path.dirname(DB_FILE_PATH)}")
		else:
			logger.error(f"Database file not found at: {DB_FILE_PATH}. Monitoring disabled.")
			DB_FILE_PATH = None 
			db_id_storage_file_path = None
	else:
		logger.info("No database file specified. Database monitoring DISABLED.")



	# --- Handlers (remain the same) ---
	application.add_handler(CommandHandler("start", start_command))
	application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
	application.add_handler(CallbackQueryHandler(handle_ticket_print_callback, pattern="^print:parse_encoded$"))
	application.add_handler(CallbackQueryHandler(handle_calculator_print_callback, pattern="^print:parse_calculator_encoded$"))

	logger.info("Bot started and polling for updates...")
	try:
		application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
	except KeyboardInterrupt:
		logger.info("Bot stopped by user (KeyboardInterrupt).")
	except Exception as e:
		logger.error(f"Critical error during bot execution: {e}", exc_info=True)
	finally:
		if observer:
			observer.stop()
			observer.join()
			logger.info("Watchdog observer stopped.")
		logger.info("Bot shutdown complete.")