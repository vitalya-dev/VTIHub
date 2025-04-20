import logging
import json
import argparse
import pytz
import base64
import re
import io
import os

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

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
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



async def handle_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the 'Print' button:
      1. Parses base64 ticket data.
      2. Renders a 58×40 mm PNG label at 203 dpi with:
         – a header banner (small logo + company info)
         – full-width body (Submitted by / Phone / Time)
         – description at the bottom.
      3. Sends the resulting image back into the chat.
    """
    query = update.callback_query
    temp_msg = await query.message.reply_text("🖨️") #pyright: ignore

    # 1️⃣ Decode the ticket payload
    data_marker = "Encoded Data:"
    text = query.message.text or "" #pyright: ignore
    m = re.search(rf"^{re.escape(data_marker)}\s+(.*)$", text, re.MULTILINE)
    if not m:
        return await query.message.reply_text("❌ Ticket data not found.") #pyright: ignore
    try:
        payload = base64.b64decode(m.group(1)).decode("utf-8")
        ticket = json.loads(payload)
    except Exception:
        return await query.message.reply_text("❌ Failed to decode ticket data.")#pyright: ignore

    phone           = ticket.get("p", "")
    description     = ticket.get("d", "")
    user_identifier = ticket.get("s", "")
    timestamp       = ticket.get("t", "")

   # 2️⃣ Canvas setup
    MM2IN = 1.0 / 25.4
    DPI   = 203
    W_MM, H_MM = 58, 40
    W_PX = int(W_MM * MM2IN * DPI)
    H_PX = int(H_MM * MM2IN * DPI)
    img = Image.new("RGB", (W_PX, H_PX), "white")
    draw = ImageDraw.Draw(img)

    # 3️⃣ Load larger TTF fonts
    font_header = ImageFont.truetype("./fonts/Roboto/static/Roboto-Bold.ttf",   28)  # ↑ from 20 to 28
    font_body   = ImageFont.truetype("./fonts/Roboto/static/Roboto-Regular.ttf", 18)  # ↑ from 14 to 18
    font_small  = ImageFont.truetype("./fonts/Roboto/static/Roboto-Regular.ttf", 16)  # ↑ from 12 to 16

    # 4️⃣ Helpers (unchanged) …
    def mm2px(mm: float) -> int:
        return int(mm * MM2IN * DPI)
    def draw_line(txt: str, font, x: int, y: int) -> int:
        draw.text((x, y), txt, font=font, fill="black")
        a, d = font.getmetrics()
        return y + a + d + 4  # give a bit more leading

    # 5️⃣ Header banner
    margin = mm2px(2)
    icon_sz = mm2px(12)
    # — Load, resize and paste your logo instead of drawing a blank rectangle —
    logo = Image.open("./logo.png").convert("RGBA")
    logo = logo.resize((icon_sz, icon_sz))
    img.paste(logo, (margin, margin + mm2px(1)), logo)
    draw.rectangle([margin, margin + mm2px(1), margin+icon_sz, margin+mm2px(1)+icon_sz], outline="black", width=2)
    x0 = margin + icon_sz + mm2px(1)
    y0 = margin
    y0 = draw_line("ООО «ВТИ»", font_header, x0, y0)
    y0 = draw_line("ул Советская 26, г. Керчь",  font_body,   x0, y0)
    y0 = draw_line("+7 (978) 762‑8967",       font_body,   x0, y0)
    y0 = draw_line("+7 (978) 010‑4949",       font_body,   x0, y0)


    banner_h = max(margin + icon_sz, y0) + mm2px(1)
    draw.line((0, banner_h, W_PX, banner_h), fill="black", width=2)

    # 6️⃣ Full‑width body
    y1 = banner_h + mm2px(2)
    x1 = margin
    y1 = draw_line(f"Принял(а): {user_identifier}", font_body, x1, y1)
    y1 = draw_line(f"Телефон: {phone}",                font_body, x1, y1)
    y1 = draw_line(f"Время: {timestamp}",              font_body, x1, y1)

    # 7️⃣ Description
    y1 += mm2px(1)
    y1 = draw_line("Описание:", font_body, x1, y1)
    # wrap narrower now that text is larger
    for line in textwrap.wrap(description, width=28):
        y1 = draw_line(line, font_small, x1, y1)


    # ➡️ Empty commentary box at bottom‑right
    BOX_W_MM, BOX_H_MM = 20, 10
    box_w = mm2px(BOX_W_MM)
    box_h = mm2px(BOX_H_MM)

    # bottom‑right corner, inset by your margin
    x2 = W_PX - int(margin / 3) - box_w
    y2 = H_PX - int(margin / 3) - box_h

    # draw the empty rectangle
    draw.rounded_rectangle(
        [x2, y2, x2 + box_w, y2 + box_h],
        radius=mm2px(1.5),         # adjust corner roundness here
        outline="black",
        width=2
    )

    #img = img.resize((W_PX * 4, H_PX * 4), Image.Resampling.NEAREST)

    # — ensure output directory exists
    output_dir = "./labels"
    os.makedirs(output_dir, exist_ok=True)

    # — build a filename (you can customize this pattern)
    safe_user = re.sub(r"\W+", "_", user_identifier)  # make username filesystem-safe
    timestamp_safe = timestamp.replace(" ", "_").replace(":", "-")
    file_name = f"label_{safe_user}_{timestamp_safe}.png"
    file_path = os.path.join(output_dir, file_name)

    # — save the image
    img.save(file_path, format="PNG", dpi=(DPI, DPI))
    logger.info(f"Label saved to disk at {file_path}")

    #  ➡️ delete our “🖨️ Generating” message
    await context.bot.delete_message(
        chat_id=temp_msg.chat.id,
        message_id=temp_msg.message_id
    )
    # finally, send the print notification
    await query.answer("✅ Этикетка напечатана!")







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
    args = parser.parse_args()

    logger.info("Starting bot...")

    application = Application.builder() \
        .token(args.token) \
        .post_init(post_init) \
        .build()

    # --- Add Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    application.add_handler(CallbackQueryHandler(handle_print_callback, pattern="^print:parse_encoded$"))

    logger.info("Bot started and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
