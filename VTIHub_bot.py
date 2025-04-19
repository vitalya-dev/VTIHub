import logging
import json
import argparse
import pytz
import base64
import re
import io


from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import textwrap

# Import necessary components from python-telegram-bot
from telegram import (
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
        BotCommand("start", "Show available apps"),
        BotCommand("help", "Get assistance and instructions"),
        BotCommand("hide_menu", "Hide the custom action keyboard") # Command to remove the reply keyboard
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

# --- Command Handlers ---

async def channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /channel command, providing a link to the target channel."""
    if not TARGET_CHANNEL_ID:
        await update.message.reply_text("The target channel is not configured.")
        return

    try:
        # Extract the internal numeric ID (remove -100 prefix)
        # Ensure TARGET_CHANNEL_ID is treated as a string for slicing
        channel_url = f"https://t.me/c/2558046400/7" # Link format for private channels (for members)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Open VTIHub Channel", url=channel_url)]
        ])

        await update.message.reply_text(
            "Click the button below to open the VTIHub channel (requires membership):",
            reply_markup=keyboard
        )
        logger.info(f"Provided channel link to user {update.effective_user.id}")

    except Exception as e:
        logger.error(f"Error creating channel link for {TARGET_CHANNEL_ID}: {e}", exc_info=True)
        await update.message.reply_text("Sorry, couldn't generate the link to the channel.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command. Displays ReplyKeyboard buttons to launch different Web Apps."""
    user_name = update.effective_user.first_name

    # Create KeyboardButton objects, one for each web app
    keyboard_buttons_row1 = []
    keyboard_buttons_row2 = [] # Example for layout
    
    button_count = 0
    if "ticket" in WEB_APP_URLS:
        button = KeyboardButton(
                     "📄 New Ticket", # Descriptive text
                     web_app=WebAppInfo(url=WEB_APP_URLS["ticket"])
                 )
        if button_count % 2 == 0: # Simple 2-column layout
             keyboard_buttons_row1.append(button)
        else:
             keyboard_buttons_row2.append(button)
        button_count += 1
    # Combine rows - filter out empty rows if any
    keyboard_layout = [row for row in [keyboard_buttons_row1, keyboard_buttons_row2] if row]

    if keyboard_layout:
        # Create the ReplyKeyboardMarkup
        keyboard = ReplyKeyboardMarkup(
            keyboard=keyboard_layout,
            resize_keyboard=True, # Makes the keyboard fit better
            one_time_keyboard=False, # Keyboard persists until replaced or removed
            input_field_placeholder="Select an action from the menu below" # Helpful hint
        )
        await update.message.reply_text(
            f"Hello {user_name}! Please choose an action using the buttons below the text field.\n\n"
            f"Type /hide_menu to remove these buttons and return to the standard keyboard.",
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
             f"Hello {user_name}! Sorry, no actions are currently configured."
             # Optionally remove any existing custom keyboard if no actions available
             # reply_markup=ReplyKeyboardRemove()
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command. Provides instructions."""
    await update.message.reply_text(
        "ℹ️ How to use this bot:\n\n"
        "1. Use the /start command to show buttons for available actions below the text input area.\n"
        "2. Tap the button for the action you want. This will open the relevant web app.\n"
        "3. Fill out the details in the web app interface and submit.\n"
        "4. You'll receive a confirmation message back here in the chat.\n"
        "5. The action buttons will remain visible. Use /hide_menu to remove them and use the standard keyboard.\n\n",
        reply_markup=ReplyKeyboardRemove() # Remove keyboard when showing help
    )

async def hide_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /hide_menu command. Removes the custom ReplyKeyboard."""
    await update.message.reply_text(
        "Action menu hidden. Type /start to show it again.",
        reply_markup=ReplyKeyboardRemove() # This object removes the custom keyboard
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
                f"✅ Ticket Created!\n\n"
                f"👤 Submitted by: {user_identifier} (User ID: {user.id})\n"
                f"🕒 Time: {current_time}\n"
                f"--- Job Details ---\n"
                f"📞 Phone: {phone} (Search: {search_hints})\n"
                f"📝 Description: {description}\n\n"
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
            f"✅ Ticket Created!\n\n"
            f"👤 Submitted by: {user_identifier} (User ID: {user.id})\n"
            f"🕒 Time: {current_time}\n"
            f"--- Job Details ---\n"
            f"📞 Phone: {phone} (Search: {search_hints})\n"
            f"📝 Description: {description}\n\n"
            f"{data_marker} {base64_encoded_json}\n\n"
            f"🔗 [View Your Ticket in Channel]({message_link})\n\n"
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
    await query.answer("Generating label…")

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
    font_small  = ImageFont.truetype("./fonts/Roboto/static/Roboto-Regular.ttf", 14)  # ↑ from 12 to 16
    font_emoji   = ImageFont.truetype("./fonts/Noto_Color_Emoji/NotoColorEmoji-Regular.ttf", 16)

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
    #logo = logo.resize((icon_sz, icon_sz))
    img.paste(logo, (margin, margin), logo)
    draw.rectangle([margin, margin, margin+icon_sz, margin+icon_sz], outline="black", width=2)
    x0 = margin + icon_sz + mm2px(1)
    y0 = margin
    y0 = draw_line("My Company, Inc.",       font_header, x0, y0)
    y0 = draw_line("123 Main St., Hometown",  font_body,   x0, y0)
    y0 = draw_line("+1 (800) 555‑1234",       font_body,   x0, y0)

    banner_h = max(margin + icon_sz, y0) + mm2px(1)
    draw.line((0, banner_h, W_PX, banner_h), fill="black", width=2)

    # 6️⃣ Full‑width body
    y1 = banner_h + mm2px(2)
    x1 = margin
    y1 = draw_line(f"Submitted by: {user_identifier}", font_body, x1, y1)
    y1 = draw_line(f"Phone: {phone}",                 font_body, x1, y1)
    y1 = draw_line(f"Time: {timestamp}",              font_body, x1, y1)

    # 7️⃣ Description
    y1 += mm2px(1)
    y1 = draw_line("Description:", font_body, x1, y1)
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


    # # ➡️ Label it “💬 Commentary”
    # text_x = x2 + mm2px(1)
    # text_y = y2 + mm2px(1)
    # draw.text(
    #     (text_x, text_y),
    #     "Коментарии:",
    #     font=font_small,
    #     fill="black"
    # )

    # 8️⃣ Send image (unchanged)
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(DPI, DPI))
    buf.seek(0)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption="🖨️ Вот ваша этикетка."
    )





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

async def handle_other_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any other text messages that aren't commands or web app data."""
    # You might want this handler to be less intrusive if the user is typing
    # while the custom keyboard is open. For now, it just reminds them.
    await update.message.reply_text(
        "👋 Use the buttons below to start an action, or type /start to refresh the menu. Use /hide_menu to type normally, or /help for instructions."
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
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("channel", channel_command)) # Add handler for /channel
    application.add_handler(CommandHandler("hide_menu", hide_menu_command)) # Handler for removing the keyboard
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    # Make sure the text handler doesn't interfere with commands starting with /
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.WEB_APP_DATA, handle_other_messages))
    application.add_handler(CallbackQueryHandler(handle_print_callback, pattern="^print:parse_encoded$"))

    logger.info("Bot started and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
