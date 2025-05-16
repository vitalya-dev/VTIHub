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
	phone = data.get('phone', 'N/A')
	description = data.get('description', 'No description provided.')

	# --- Format phone number for tel: link and display ---
	phone_link_html = phone # Default to original text if sanitization fails or N/A
	if phone and phone != 'N/A':
		sanitized_phone_for_link = re.sub(r'\D', '', phone)
		if sanitized_phone_for_link:
			if len(sanitized_phone_for_link) == 11 and sanitized_phone_for_link.startswith('8'):
				sanitized_phone_for_link = '7' + sanitized_phone_for_link[1:]
			if not sanitized_phone_for_link.startswith('+'):
				sanitized_phone_for_link = '+' + sanitized_phone_for_link
			tel_url = f"tel:{sanitized_phone_for_link}"
			phone_link_html = f'<a href="{tel_url}"><b><code>{phone}</code></b></a>'

	# Generate search hints
	search_hints = ""
	if phone and phone != 'N/A':
		numeric_phone = re.sub(r'\D', '', phone)
		hints_list = []
		if len(numeric_phone) >= 2: hints_list.append(numeric_phone[-2:])
		if len(numeric_phone) >= 3: hints_list.append(numeric_phone[-3:])
		if len(numeric_phone) >= 4: hints_list.append(numeric_phone[-4:])
		search_hints = " ".join(sorted(set(hints_list), key=len))

	# Prepare ticket data for encoding
	ticket_details_to_encode = {
		'p': phone,
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

	print_button = InlineKeyboardButton("🖨️ Print", callback_data="print:parse_ticket_encoded")
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
				f"📞 Телефон: {phone_link_html} (Поиск: {search_hints})\n"
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
			f"📞 Телефон: {phone_link_html} (Поиск: {search_hints})\n"
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
			"🖨️ Распечатать чек", 
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
	body_y = _draw_text_line(draw, f"Телефон: {ticket.get('p', 'N/A')}", fonts["ticket_details"], body_x, body_y) 
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
		fonts = _load_fonts() # Reuse existing font loading
	except IOError:
		return None

	img = Image.new("RGB", (LABEL_WIDTH_PX, LABEL_HEIGHT_PX), BACKGROUND_COLOR)
	draw = ImageDraw.Draw(img)
	margin_px = mm2px(MARGIN_MM)
	current_y = margin_px

	# --- Calculator Details Section ---
	body_x = margin_px
	
	items_list = calc_data.get('items', [])
	total_amount = calc_data.get('total', 0.0)

	# --- Items List ---
	price_area_width_px = mm2px(15) # Reserve space for price on the right

	for i, item_data in enumerate(items_list):
		if i >= MAX_ITEMS_ON_LABEL:
			available_height_for_trunc_msg = LABEL_HEIGHT_PX - current_y - margin_px - fonts["small"].getbbox("Итого:")[3] - mm2px(2)
			if fonts["small"].getbbox("...и еще")[3] < available_height_for_trunc_msg: # Check if truncation message fits
				_draw_text_line(draw, f"...и еще {len(items_list) - MAX_ITEMS_ON_LABEL} товар(ов)", fonts["small"], body_x, current_y)
			break

		item_name = item_data.get('name', 'N/A')
		item_price = item_data.get('price', 0.0)
		
		# Wrap item name if too long
		# Pillow's textwrap is not available directly, so manual check or simple split
		# For simplicity, we'll truncate with ellipsis if it's too wide.
		# A more robust way would be to check textsize().
		
		max_name_width = LABEL_WIDTH_PX - (2 * margin_px) - price_area_width_px - mm2px(2) # available width for name
		
		# Truncate item name if it's too long
		avg_char_width = fonts["small"].getbbox("x")[2] # Approximate width of one character
		if avg_char_width > 0 and len(item_name) * avg_char_width > max_name_width:
			max_chars = int(max_name_width / avg_char_width) - 3 # -3 for "..."
			display_name = item_name[:max_chars] + "..."
		else:
			display_name = item_name

		# Draw item name (left aligned)
		draw.text((body_x, current_y), display_name, font=fonts["small"], fill=TEXT_COLOR)
		
		# Draw item price (right aligned)
		price_text = f"{item_price:.2f}"
		price_text_width = fonts["small"].getbbox(price_text)[2] 
		price_x = LABEL_WIDTH_PX - margin_px - price_text_width
		draw.text((price_x, current_y), price_text, font=fonts["small"], fill=TEXT_COLOR)

		ascent, descent = fonts["small"].getmetrics()
		current_y += ascent + descent + LINE_SPACING // 2 # Smaller line spacing for items
		
		if current_y + fonts["small"].getbbox("Итого:")[3] + mm2px(2) > LABEL_HEIGHT_PX - margin_px: # Check if total will fit
			if i < len(items_list) -1: # if there are more items not drawn
				 # Overwrite the last drawn item with a truncation message if it was not the actual last item
				current_y -= (ascent + descent + LINE_SPACING // 2) # backtrack
				draw.rectangle( # Clear previous text line
					(body_x, current_y, LABEL_WIDTH_PX - margin_px, current_y + ascent + descent + LINE_SPACING // 2),
					fill=BACKGROUND_COLOR
				)
				available_height_for_trunc_msg = LABEL_HEIGHT_PX - current_y - margin_px - fonts["small"].getbbox("Итого:")[3] - mm2px(2)
				if fonts["small"].getbbox("...и еще")[3] < available_height_for_trunc_msg:
					_draw_text_line(draw, f"...и еще {len(items_list) - i} товар(ов)", fonts["small"], body_x, current_y)
			break


	current_y += mm2px(2) # Space before total

	# --- Total Amount ---
	# Ensure total is not drawn off label
	total_text_height = fonts["small"].getbbox(f"Итого: {total_amount:.2f}")[3]
	if current_y + total_text_height > LABEL_HEIGHT_PX - margin_px:
		# If total doesn't fit, try to move it up a bit, or it might be cut off.
		# This part needs careful adjustment based on actual label space.
		# For now, we draw it; if it's cut, layout or MAX_ITEMS_ON_LABEL needs tuning.
		pass

	_draw_text_line(draw, "----------------------------------", fonts["body"], body_x, current_y, color=BORDER_COLOR) # Separator line
	current_y += mm2px(1)
	
	total_str = f"Итого: {total_amount:.2f}"
	total_text_bbox = fonts["small"].getbbox(total_str)
	total_width = total_text_bbox[2] - total_text_bbox[0]
	total_x = LABEL_WIDTH_PX - margin_px - total_width # Right align total
	
	_draw_text_line(draw, total_str, fonts["small"], int(total_x), current_y, color=TEXT_COLOR)

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
			await query.message.reply_text(MSG_ERR_NO_DATA if not query.message.text or DATA_MARKER not in query.message.text else MSG_ERR_DECODE)
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
				await query.message.reply_text(f"✅ Чек отправлен на принтер: {printer_name}")
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

# --- Main Execution ---

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Telegram Multi-WebApp Bot (ReplyKeyboard Menu)")
	parser.add_argument('--token', required=True, help='Telegram Bot Token')
	parser.add_argument(
		'--print',
		dest='printer_name', # Changed dest to avoid conflict with builtin print
		default=None,
		help='Specify the network printer name for IrfanView printing (e.g., "MyPrinter" or "\\\\Server\\PrinterShare")'
	)
	parser.add_argument('--log-file', help='Path to log file (logs to console if omitted)')
	parser.add_argument('--log-level', default='INFO',
						choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
						help='Set the logging level')
	parser.add_argument('--debug', action='store_true', help='Enable debug mode (no channel messages, potentially different web app URLs)')
	args = parser.parse_args()

	if args.log_file:
		# Ensure the log directory exists, using SCRIPT_DIR for relative paths
		log_path = os.path.join(SCRIPT_DIR, args.log_file)
		os.makedirs(os.path.dirname(log_path), exist_ok=True)
	else:
		log_path = None # Log to console

	logging.basicConfig(
		filename=log_path, # None here means console
		format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
		level=getattr(logging, args.log_level.upper())
	)
	logging.getLogger("httpx").setLevel(logging.WARNING) # Quieten httpx library

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


	if args.printer_name: # Use the new dest name
		logger.info(f"Printer name specified: '{args.printer_name}'. Printing will be enabled.")
		application.bot_data['printer_name'] = args.printer_name
	else:
		logger.info("No printer name specified. Printing via IrfanView is disabled.")
		application.bot_data['printer_name'] = None

	application.add_handler(CommandHandler("start", start_command))
	application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
	application.add_handler(CallbackQueryHandler(handle_ticket_print_callback, pattern="^print:parse_ticket_encoded$"))
	application.add_handler(CallbackQueryHandler(handle_calculator_print_callback, pattern="^print:parse_calculator_encoded$"))

	logger.info("Bot started and polling for updates...")
	application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)