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
from telegram.constants import ChatMemberStatus
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
    Checks if the user is an active member (member, admin, owner) of the target channel.
    If YES, shows the main keyboard with the Web App button.
    If NO, sends a message denying access and does NOT show the keyboard.
    """
    user = update.effective_user
    user_id = user.id
    user_info_log = f"{user_id} ({user.username or user.full_name})" # For clearer logs

    user_can_access = False # Assume no access initially
    denial_reason = "Извините, этот бот доступен только для сотрудников ВТИ, являющихся участниками рабочего канала." # Default denial message

    if not TARGET_CHANNEL_ID:
        logger.error("TARGET_CHANNEL_ID is not configured in the bot code. Denying access.")
        denial_reason = "⚠️ Ошибка конфигурации бота. Доступ запрещен." # Specific error for config issue
    else:
        try:
            # Check the user's status in the target channel
            chat_member = await context.bot.get_chat_member(chat_id=TARGET_CHANNEL_ID, user_id=user_id)

            # Define allowed statuses for access
            # Use the constants consistent with your import style:
            # allowed_statuses = [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
            allowed_statuses = [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]

            if chat_member.status in allowed_statuses:
                user_can_access = True
                logger.info(f"Access GRANTED for user {user_info_log}. Status: {chat_member.status} in channel {TARGET_CHANNEL_ID}.")
            else:
                # User has a status like LEFT or KICKED, which doesn't grant access.
                logger.warning(f"Access DENIED for user {user_info_log}. Status: {chat_member.status} in channel {TARGET_CHANNEL_ID}.")
                # Keep the default denial_reason

        except error.BadRequest as e:
            # This specific error often means the user is not in the chat at all
            if "user not found" in e.message.lower():
                logger.warning(f"Access DENIED for user {user_info_log}. User not found in channel {TARGET_CHANNEL_ID}.")
                # Keep the default denial_reason
            else:
                # Log other Bad Request errors (e.g., chat not found, bot permissions issue)
                logger.error(f"BadRequest when checking membership for user {user_info_log} in channel {TARGET_CHANNEL_ID}: {e}")
                denial_reason = "⚠️ Не удалось проверить ваше членство в канале из-за ошибки. Пожалуйста, попробуйте позже или свяжитесь с администратором."
        except error.TelegramError as e:
            # Catch other potential Telegram API errors
            logger.error(f"TelegramError checking membership for {user_info_log} in {TARGET_CHANNEL_ID}: {e}")
            denial_reason = "⚠️ Произошла ошибка при проверке доступа. Пожалуйста, попробуйте позже или свяжитесь с администратором."

    # --- Grant Access or Deny ---
    if user_can_access:
        # User is verified, show the main keyboard
        kb = [
            [KeyboardButton("📄 Новая Заявка", web_app=WebAppInfo(url=WEB_APP_URLS["ticket"]))]
        ]
        main_message = "🐶" # Your standard welcome message/emoji for authorized users
        await update.message.reply_text(
            main_message,
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
    else:
        # User is not authorized, send the denial message and DO NOT show the keyboard
        await update.message.reply_text(
            denial_reason,
            reply_markup=ReplyKeyboardRemove()
        )


# --- Data Processing Functions ---
# (These remain the same as the previous multi-app version using InlineKeyboard)

async def process_ticket_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """Processes data specifically from the Ticket Web App."""
    logger.info(f"Processing 'ticket_app' data: {data}")
    user = update.effective_user
    user_identifier = user.first_name
    if user.username:
        user_identifier = f"@{user.username}"
    elif user.full_name:
        user_identifier = user.full_name

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

    # Encode the ticket data
    try:
        json_string = json.dumps(ticket_details_to_encode, separators=(',', ':'))
        base64_encoded_json = base64.b64encode(json_string.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to encode ticket details: {e}", exc_info=True)
        await update.message.reply_text("Sorry, there was an error preparing your ticket data.")
        return

    data_marker = "Encoded Data:"

    # Inline "Print" button (still needed)
    print_button = InlineKeyboardButton(
        "🖨️ Print",
        callback_data="print:parse_encoded"
    )
    keyboard = InlineKeyboardMarkup([[print_button]])

    # --- STEP 1: Send the message to the Channel first ---
    if TARGET_CHANNEL_ID:
        try:
            channel_message_text = (
                f"✅ Заявка создана!\n\n"
                f"👤 Отправил(а): {user_identifier}\n"
                f"🕒 Время: {current_time}\n"
                f"--- Детали заявки ---\n"
                f"📞 Телефон: {phone} (Поиск: {search_hints})\n"
                f"📝 Описание: {description}\n\n"
                f"{data_marker} {base64_encoded_json}\n\n"
            )

            sent_message = await context.bot.send_message(
                chat_id=TARGET_CHANNEL_ID,
                text=channel_message_text,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
            logger.info(f"Ticket posted to channel {TARGET_CHANNEL_ID}")

            # Build link to the channel message
            internal_channel_id = str(TARGET_CHANNEL_ID)[4:]  # remove "-100"
            channel_message_id = sent_message.message_id
            message_link = f"https://t.me/c/{internal_channel_id}/{channel_message_id}"

        except Exception as e_channel:
            logger.error(f"Failed to send message TO CHANNEL {TARGET_CHANNEL_ID}: {e_channel}", exc_info=True)
            await update.message.reply_text("Sorry, there was an error posting your ticket to the channel.")
            return

    else:
        logger.warning("TARGET_CHANNEL_ID is not set.")
        await update.message.reply_text("Ticket channel is not configured.")
        return

    # --- STEP 2: After posting to channel, send user a full message ---
    try:
        user_message_text = (
            f"✅ Заявка создана!\n\n"
            f"👤 Отправил(а): {user_identifier}\n"
            f"🕒 Время: {current_time}\n"
            f"--- Детали заявки ---\n"
            f"📞 Телефон: {phone} (Поиск: {search_hints})\n"
            f"📝 Описание: {description}\n\n"
            f"{data_marker} {base64_encoded_json}\n\n"
            f"🔗 [Посмотреть вашу заявку в канале]({message_link})\n\n"
        )
        await update.message.reply_text(
            text=user_message_text,
            reply_markup=keyboard,
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
            "small": ImageFont.truetype(FONT_BOLD_PATH, FONT_SIZE_SMALL),
        }
    except IOError as e:
        logger.error(f"Failed to load font: {e}")
        raise  # Re-raise to signal critical failure


def _draw_text_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    color: str = TEXT_COLOR
) -> int:
    """Draws a line of text and returns the Y position for the next line."""
    draw.text((x, y), text, font=font, fill=color)
    ascent, descent = font.getmetrics()
    # Use font.getbbox for more accurate height in newer Pillow versions if needed
    # bbox = font.getbbox(text)
    # line_height = bbox[3] - bbox[1]
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

def _generate_label_image(ticket: Dict[str, Any]) -> Optional[Image.Image]:
    """Generates the label image based on ticket data."""
    try:
        fonts = _load_fonts()
    except IOError:
        return None # Font loading failed

    img = Image.new("RGB", (LABEL_WIDTH_PX, LABEL_HEIGHT_PX), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    margin_px = mm2px(MARGIN_MM)
    current_y = margin_px

    # 1. Header
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo_size_px = mm2px(LOGO_SIZE_MM)
        # Optional: Resize logo if needed, maintaining aspect ratio
        # logo.thumbnail((logo_size_px, logo_size_px), Image.Resampling.LANCZOS)
        img.paste(logo, (margin_px, margin_px), logo) # Use logo as mask for transparency
        # Draw border around logo area (optional)
        # draw.rectangle([margin_px, margin_px, margin_px + logo_size_px, margin_px + logo_size_px], outline=BORDER_COLOR, width=BORDER_WIDTH)

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
        # Adjust layout if logo is missing? Or draw placeholder?
        # For now, just continue without it. Header text will start at margin_px.
        header_text_x = margin_px
        header_y = margin_px
        header_y = _draw_text_line(draw, "ООО «ВТИ»", fonts["header"], header_text_x, header_y)
        # ... rest of header lines ...
        banner_height = header_y + mm2px(1) # Adjust banner height
        draw.line((0, banner_height, LABEL_WIDTH_PX, banner_height), fill=BORDER_COLOR, width=BORDER_WIDTH)
        current_y = banner_height + mm2px(2)
    except Exception as e:
        logger.error(f"Error drawing header: {e}")
        # Decide how to proceed: maybe draw a simpler header or return None

    # 2. Body
    body_x = margin_px
    body_y = current_y
    body_y = _draw_text_line(draw, f"Принял(а): {ticket.get('s', 'N/A')}", fonts["body"], body_x, body_y)
    body_y = _draw_text_line(draw, f"Телефон: {ticket.get('p', 'N/A')}", fonts["body"], body_x, body_y)
    body_y = _draw_text_line(draw, f"Время: {ticket.get('t', 'N/A')}", fonts["body"], body_x, body_y)

    # 3. ASCII Art (Optional) - Consider removing if not essential or making configurable
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
            art_y = current_y # Align with top of body text
            ascent, _ = fonts["body"].getmetrics()
            line_h = ascent # Use ascent for line height approx
            for line in ascii_bulb:
                # Center align text within its calculated max width? Or keep right align?
                # current_line_width = fonts["body"].getlength(line)
                # draw.text((art_x + (max_w - current_line_width) / 2, art_y), line, font=fonts["body"], fill=TEXT_COLOR)
                draw.text((art_x, art_y), line, font=fonts["body"], fill=TEXT_COLOR)
                art_y += line_h # Tighter spacing for art
        except Exception as e:
            logger.warning(f"Could not draw ASCII art: {e}")


    # 4. Description
    desc_y = body_y + mm2px(1) # Start description below body text
    desc_y = _draw_text_line(draw, "Описание:", fonts["body"], body_x, desc_y)
    description = ticket.get("d", "")
    # Adjust wrap width based on available space and font size
    # Approx chars = (Width_px - 2*margin_px) / avg_char_width
    # avg_char_width for Terminus Bold 16 might be around 9-10px?
    # wrap_width = (LABEL_WIDTH_PX - 2 * margin_px) // 9
    wrap_width = 28 # Adjust this based on visual testing
    for line in textwrap.wrap(description, width=wrap_width):
        desc_y = _draw_text_line(draw, line, fonts["small"], body_x, desc_y)

    # 5. Commentary Box
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
    """Saves the image to a file and returns the path."""
    os.makedirs(output_dir, exist_ok=True)

    user_identifier = ticket.get("s", "unknown_user")
    timestamp = ticket.get("t", "unknown_time")

    safe_user = re.sub(r"\W+", "_", user_identifier)
    timestamp_safe = timestamp.replace(" ", "_").replace(":", "-")
    file_name = f"label_{safe_user}_{timestamp_safe}.png"
    # Use os.path.join for cross-platform path construction
    relative_file_path = os.path.join(output_dir, file_name)
    # Get absolute path immediately for consistency
    absolute_file_path = os.path.abspath(relative_file_path)


    try:
        image.save(absolute_file_path, format="PNG", dpi=dpi)
        logger.info(f"Label saved to disk at {absolute_file_path}")
        return absolute_file_path
    except Exception as e:
        logger.error(f"Failed to save image to {absolute_file_path}: {e}")
        return None

async def handle_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the 'Print' button callback to generate and save a label image.
    """
    query = update.callback_query
    if not query or not query.message:
        logger.warning("Received callback query without query or message.")
        return

    # Acknowledge callback quickly
    await query.answer()

    temp_msg = await query.message.reply_text(MSG_GENERATING) #pyright: ignore

    try:
        # 1. Parse Data
        ticket_data = _parse_ticket_data(query.message.text) #pyright: ignore
        if ticket_data is None:
            await query.message.reply_text(MSG_ERR_NO_DATA if not query.message.text or DATA_MARKER not in query.message.text else MSG_ERR_DECODE) #pyright: ignore
            return

        # 2. Generate Image
        label_image = _generate_label_image(ticket_data)
        if label_image is None:
            await query.message.reply_text(MSG_ERR_GENERIC) #pyright: ignore
            return

        # 3. Save Image (Optional: Send directly without saving)
        file_path = _save_label_image(label_image, ticket_data)
        if file_path is None:
            await query.message.reply_text("❌ Failed to save the label image.") #pyright: ignore
             # Optionally still send the image even if saving failed:
             # bio = BytesIO()
             # bio.name = 'label.png'
             # label_image.save(bio, 'PNG')
             # bio.seek(0)
             # await query.message.reply_photo(photo=bio, caption=MSG_SUCCESS)
            return

         # --- NEW: Attempt Printing ---
        printer_name = context.bot_data.get('printer_name')
        if printer_name and file_path:
            logger.info(f"Printer name '{printer_name}' provided. Attempting to print {file_path}")
            print_command = [
                    IRFANVIEW_ABS_PATH,
                    file_path,
                    f'/print={printer_name}',
                    f'/dpi=({DPI},{DPI})',
                    f'/ini={SCRIPT_DIR}'
                ]
            logger.info(f"Executing print command: {' '.join(print_command)}")
            result = subprocess.run(
                        print_command,
                        check=False,         # Raise error if IrfanView returns non-zero exit code
                        capture_output=True,# Capture stdout/stderr
                        text=True,          # Decode stdout/stderr as text
                        timeout=30,          # Add a timeout (e.g., 30 seconds)
                    )
            if result.returncode == 0:
                logger.info(f"IrfanView print command successful for {file_path}. Output: {result.stdout}")
            else:
                logger.error(f"IrfanView print command failed for {file_path}. Return Code: {result.returncode}. Stderr: {result.stderr}. Stdout: {result.stdout}")

    except Exception as e:
        logger.exception("Unhandled error in handle_print_callback") # Log full traceback
        await query.message.reply_text(MSG_ERR_GENERIC) #pyright: ignore
    finally:
        # Clean up the "Generating" message
        if temp_msg:
            try:
                await context.bot.delete_message(
                    chat_id=temp_msg.chat.id,
                    message_id=temp_msg.message_id
                )
            except Exception as e:
                logger.warning(f"Failed to delete temporary message: {e}")



async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle data received from any Web App and dispatch to the correct processor."""
    # This handler remains identical - it doesn't care how the app was launched
    if not update.effective_message or not update.effective_message.web_app_data:
        logger.warning("Received update without effective_message or web_app_data")
        return

    logger.info("Received data from a Web App.")
    raw_data = update.effective_message.web_app_data.data

    try:
        data = json.loads(raw_data)
        logger.log(DEBUG, f"Web App data received: {data}")

        # --- Data Dispatching (Requires 'app_origin' from web app) ---
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
        default=None, # Default is no printing
        help='Specify the network printer name for IrfanView printing (e.g., "MyPrinter" or "\\\\Server\\PrinterShare")'
    )
    parser.add_argument('--log-file', help='Path to log file (logs to console if omitted)')
    parser.add_argument('--log-level', default='INFO', 
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Set the logging level')
    args = parser.parse_args()

     # --- Configure Log Path ---
    if args.log_file:
        # Create full path relative to script directory
        log_path = os.path.join(SCRIPT_DIR, args.log_file)
        # Create parent directories if needed
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    else:
        log_path = None

    # --- Logging Setup ---
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

    if args.print:
        logger.info(f"Printer name specified: '{args.print}'. Printing will be enabled.")
        application.bot_data['printer_name'] = args.print
    else:
        logger.info("No printer name specified. Printing via IrfanView is disabled.")
        application.bot_data['printer_name'] = None # Explicitly set to None

    # --- Add Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    application.add_handler(CallbackQueryHandler(handle_print_callback, pattern="^print:parse_encoded$"))

    logger.info("Bot started and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
