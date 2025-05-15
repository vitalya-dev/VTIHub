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
    InlineKeyboardMarkup
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
}

TARGET_CHANNEL_ID = "-1002558046400" # Or e.g., -1001234567890


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
    If debug mode is active, bypasses membership check.
    Otherwise, checks if the user is an active member of the target channel.
    If access is granted (either by debug mode or membership), shows the main keyboard.
    If NO (and not in debug mode), sends a message denying access.
    """
    user = update.effective_user
    user_id = user.id
    user_info_log = f"{user_id} ({user.username or user.full_name})" # For clearer logs

    # --- DEBUG OPTION CHANGE for start_command ---
    debug_mode = context.bot_data.get('debug_mode', False)
    user_can_access = False # Assume no access initially
    # Default denial message, only used if not in debug mode and access is denied
    denial_reason = "Извините, этот бот доступен только для сотрудников ВТИ, являющихся участниками рабочего канала."

    if debug_mode:
        logger.info(f"DEBUG MODE ACTIVE for /start command by {user_info_log}. Bypassing membership check.")
        user_can_access = True
        # Optionally, notify the user that the check was bypassed due to debug mode
        await update.message.reply_text(
            "⚙️ *Режим отладки АКТИВЕН*: Проверка членства в канале пропущена.",
        )
    else:
        if not TARGET_CHANNEL_ID:
            logger.error("TARGET_CHANNEL_ID is not configured in the bot code. Denying access.")
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
                    # user_can_access remains False, denial_reason is the default one already set

            except error.BadRequest as e:
                if "user not found" in e.message.lower():
                    logger.warning(f"Access DENIED for user {user_info_log}. User not found in channel {TARGET_CHANNEL_ID}.")
                    # user_can_access remains False, denial_reason is the default one already set
                else:
                    logger.error(f"BadRequest when checking membership for user {user_info_log} in channel {TARGET_CHANNEL_ID}: {e}")
                    denial_reason = "⚠️ Не удалось проверить ваше членство в канале из-за ошибки. Пожалуйста, попробуйте позже или свяжитесь с администратором."
                    # user_can_access remains False
            except error.TelegramError as e:
                logger.error(f"TelegramError checking membership for {user_info_log} in {TARGET_CHANNEL_ID}: {e}")
                denial_reason = "⚠️ Произошла ошибка при проверке доступа. Пожалуйста, попробуйте позже или свяжитесь с администратором."

    # --- Grant Access or Deny ---
    if user_can_access:
        # User is verified (either by debug mode or membership), show the main keyboard
        kb = [
            [KeyboardButton("📄 Новая Заявка", web_app=WebAppInfo(url=WEB_APP_URLS["ticket"]))]
        ]
        main_message = "🐶" # Your standard welcome message/emoji
        await update.message.reply_text(
            main_message,
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
    else:
        # This block is only reached if NOT in debug_mode AND access was denied.
        await update.message.reply_text(
            denial_reason, # denial_reason would have been set appropriately in the !debug_mode block
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
        current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    except pytz.UnknownTimeZoneError:
        logger.warning("Unknown timezone 'Europe/Moscow', falling back to UTC.")
        current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

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

    data_marker = "Encoded Data:"
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
                f"📞 Телефон: {phone_link_html} (Поиск: {search_hints})\n"
                f"📝 Описание: {description}\n\n"
                f"{data_marker} {base64_encoded_json}\n\n"
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
            f"{data_marker} {base64_encoded_json}\n\n"
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
LOGO_SIZE_MM = 12
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

# Data Extraction
DATA_MARKER = "Encoded Data:"

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
    match = re.search(rf"^{re.escape(DATA_MARKER)}\s+(.*)$", message_text, re.MULTILINE)
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


def _generate_label_image(ticket: Dict[str, Any]) -> Optional[Image.Image]:
    try:
        fonts = _load_fonts()
    except IOError:
        return None
    img = Image.new("RGB", (LABEL_WIDTH_PX, LABEL_HEIGHT_PX), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)
    margin_px = mm2px(MARGIN_MM)
    current_y = margin_px
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo_size_px = mm2px(LOGO_SIZE_MM)
        img.paste(logo, (margin_px, margin_px), logo)
        header_text_x = margin_px + logo_size_px + mm2px(1)
        header_y = margin_px
        header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
        header_y = _draw_text_line(draw, "ул Советская 26, г. Керчь", fonts["body"], header_text_x, header_y)
        header_y = _draw_text_line(draw, "+7 (978) 762‑8967", fonts["body"], header_text_x, header_y)
        header_y = _draw_text_line(draw, "+7 (978) 010‑4949", fonts["body"], header_text_x, header_y)
        banner_height = max(margin_px + logo_size_px, header_y) + mm2px(1)
        draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
        current_y = banner_height + mm2px(2)
    except FileNotFoundError:
        logger.warning(f"Logo file not found at {LOGO_PATH}. Skipping logo.")
        header_text_x = margin_px
        header_y = margin_px
        header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
        banner_height = header_y + mm2px(1)
        draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
        current_y = banner_height + mm2px(2)
    except Exception as e:
        logger.error(f"Error drawing header: {e}")

    body_x = margin_px
    body_y = current_y
    raw_identifier = ticket.get('s', 'N/A')
    display_identifier = format_identifier_partial(raw_identifier, keep_chars=6)
    body_y = _draw_text_line(draw, f"Принял(а): {display_identifier}", fonts["ticket_details"], body_x, body_y)
    body_y = _draw_text_line(draw, f"Телефон: {ticket.get('p', 'N/A')}", fonts["ticket_details"], body_x, body_y, underline=True, underline_thickness=2)
    body_y = _draw_text_line(draw, f"Время: {ticket.get('t', 'N/A')}", fonts["ticket_details"], body_x, body_y)

    ascii_bulb = """
     :
 '.  _  .'
-=  (~)  =-
 .'  #  '.
    """.splitlines()
    if ascii_bulb:
        try:
            max_w = max(fonts["body"].getlength(line) for line in ascii_bulb)
            art_x = LABEL_WIDTH_PX - margin_px - int(max_w)
            art_y = current_y
            ascent, _ = fonts["body"].getmetrics()
            line_h = ascent
            for line in ascii_bulb:
                draw.text((art_x, art_y), line, font=fonts["body"], fill=TEXT_COLOR)
                art_y += line_h
        except Exception as e:
            logger.warning(f"Could not draw ASCII art: {e}")

    desc_y = body_y + mm2px(1)
    desc_y = _draw_text_line(draw, "Описание:", fonts["ticket_details"], body_x, desc_y)
    description = ticket.get("d", "")
    wrap_width = 28
    for line in textwrap.wrap(description, width=wrap_width):
        desc_y = _draw_text_line(draw, line, fonts["small"], body_x, desc_y)

    box_w_px = mm2px(COMMENT_BOX_WIDTH_MM)
    box_h_px = mm2px(COMMENT_BOX_HEIGHT_MM)
    box_x = LABEL_WIDTH_PX - margin_px - box_w_px
    box_y = LABEL_HEIGHT_PX - margin_px - box_h_px
    box_radius = mm2px(COMMENT_BOX_RADIUS_MM)
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_w_px, box_y + box_h_px],
        radius=box_radius,
        outline=BORDER_COLOR,
        width=BORDER_WIDTH
    )
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

async def handle_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        logger.warning("Received callback query without query or message.")
        return
    await query.answer()
    temp_msg = await query.message.reply_text(MSG_GENERATING) #pyright: ignore
    try:
        ticket_data = _parse_ticket_data(query.message.text) #pyright: ignore
        if ticket_data is None:
            await query.message.reply_text(MSG_ERR_NO_DATA if not query.message.text or DATA_MARKER not in query.message.text else MSG_ERR_DECODE) #pyright: ignore
            return
        label_image = _generate_label_image(ticket_data)
        if label_image is None:
            await query.message.reply_text(MSG_ERR_GENERIC) #pyright: ignore
            return
        file_path = _save_label_image(label_image, ticket_data)
        if file_path is None:
            await query.message.reply_text("❌ Failed to save the label image.") #pyright: ignore
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
    except Exception as e:
        logger.exception("Unhandled error in handle_print_callback")
        await query.message.reply_text(MSG_ERR_GENERIC) #pyright: ignore
    finally:
        if temp_msg:
            try:
                await context.bot.delete_message(
                    chat_id=temp_msg.chat.id,
                    message_id=temp_msg.message_id
                )
            except Exception as e:
                logger.warning(f"Failed to delete temporary message: {e}")


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
        default=None,
        help='Specify the network printer name for IrfanView printing (e.g., "MyPrinter" or "\\\\Server\\PrinterShare")'
    )
    parser.add_argument('--log-file', help='Path to log file (logs to console if omitted)')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Set the logging level')
    # --- DEBUG OPTION CHANGE ---
    parser.add_argument('--debug', action='store_true', help='Enable debug mode (no channel messages)')
    args = parser.parse_args()

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

    logger.info("Starting bot...")

    application = Application.builder() \
        .token(args.token) \
        .post_init(post_init) \
        .build()

    # --- DEBUG OPTION CHANGE ---
    # Store the debug mode in bot_data so handlers can access it
    application.bot_data['debug_mode'] = args.debug
    if args.debug:
        logger.info("Debug mode ENABLED. Bot will not send messages to the channel.")
    else:
        logger.info("Debug mode DISABLED. Bot will send messages to the channel as configured.")


    if args.print:
        logger.info(f"Printer name specified: '{args.print}'. Printing will be enabled.")
        application.bot_data['printer_name'] = args.print
    else:
        logger.info("No printer name specified. Printing via IrfanView is disabled.")
        application.bot_data['printer_name'] = None

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    application.add_handler(CallbackQueryHandler(handle_print_callback, pattern="^print:parse_encoded$"))

    logger.info("Bot started and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)