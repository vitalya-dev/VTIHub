import logging
import json
import argparse
import pytz
import base64
import re

from datetime import datetime

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
    """Handles the 'Print' button callback by parsing encoded data from the message text."""
    query = update.callback_query
    await query.answer("Processing print request...")

    callback_data = query.data # Should be "print:parse_encoded"
    message = query.message
    message_text = message.text # Get the full text of the message #pyright: ignore

    logger.info(f"Received callback query '{callback_data}'. Parsing encoded data from message ID {message.message_id}")

    # --- Parse the message text to find and decode the embedded data ---
    ticket_data = None
    data_marker = "Encoded Data:" # Must match the marker used in process_ticket_app_data

    try:
        # Use regex to find the marker and capture the data string after it
        # `re.escape` handles potential special characters in the marker itself
        # `(.*)` captures the rest of the line after the marker (non-greedy `.*?` might be safer if marker could appear again)
        match = re.search(f"^{re.escape(data_marker)}\\s+(.*)$", message_text, re.MULTILINE)

        if match:
            encoded_data_string = match.group(1).strip()
            logger.debug(f"Found encoded data string: {encoded_data_string}")

            json_string_bytes = base64.b64decode(encoded_data_string)
            json_string = json_string_bytes.decode('utf-8')

            # Parse the JSON string
            ticket_data = json.loads(json_string)
            logger.info(f"Successfully parsed embedded data: {ticket_data}")

        else:
            logger.warning(f"Could not find marker '{data_marker}' in message text.")

    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from embedded data: {e}", exc_info=True)
        ticket_data = None # Ensure data is None on failure
    except Exception as e:
        logger.error(f"Error parsing encoded data from message: {e}", exc_info=True)
        ticket_data = None

    # --- Now use the extracted data (if found) ---
    if ticket_data:
        pass



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
