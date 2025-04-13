import logging
import json
import argparse
import pytz

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
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
# Import logging constants correctly
from logging import DEBUG, INFO

# --- Configuration ---
# Define URLs for potentially multiple Web Apps
WEB_APP_URLS = {
    "ticket": "https://vitalya-dev.github.io/VTIHub/ticket_app.html", 
}

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
    """Processes data specifically from the Print Job Web App."""
    logger.info(f"Processing 'ticket_app' data: {data}")
    user = update.effective_user
    user_identifier = user.first_name # Default to first name
    if user.username:
        user_identifier = f"@{user.username}" # Use username if available
    elif user.full_name:
         user_identifier = user.full_name # Use full name if no username

    
    # Get the current time in UTC, formatted as a string
    current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime("%Y-%m-%d %H:%M:%S %Z")

    # !! Assumes 'app_origin': 'print_job' is sent back by the web app !!
    phone = data.get('phone', 'N/A')
    description = data.get('description', 'No description provided.')
    
    # Send the confirmation message with user name and time included
    await update.message.reply_text(
        f"✅ Ticket Create!\n\n"
        f"👤 Submitted by: {user_identifier}\n"
        f"🕒 Time: {current_time}\n"
        f"--- Job Details ---\n"
        f"📞 Phone: {phone}\n"
        f"📝 Description: {description}\n\n"
        f"The action menu is still active below. Use /start to refresh or /hide_menu to remove it."
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
    application.add_handler(CommandHandler("hide_menu", hide_menu_command)) # Handler for removing the keyboard
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    # Make sure the text handler doesn't interfere with commands starting with /
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.WEB_APP_DATA, handle_other_messages))

    logger.info("Bot started and polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)