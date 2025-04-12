from re import DEBUG
from telegram import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, MenuButtonWebApp, WebAppInfo, MenuButtonCommands, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import json
import logging

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)


async def setup_commands(application: Application):
    """Set bot commands menu (shown in /help)"""
    await application.bot.set_my_commands([
        BotCommand("show_print_interface", "Open PrintBot interface"),
        BotCommand("help", "Get assistance")
    ])

async def setup_menu_button(application: Application):
    """Set persistent web app button"""
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )

async def show_print_interface(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message with an inline button that opens the web app."""
    keyboard = ReplyKeyboardMarkup.from_button(
        KeyboardButton(
            text="🖨 Open PrintBot Interface",
            web_app=WebAppInfo(url="https://vitalya-dev.github.io/VTIHub/new_job.html")
        )
    )
    await update.message.reply_text(
        "Welcome to PrintBot! Please click the button below to start a new print job:",
        reply_markup=keyboard
    )


async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for any text messages"""
    await update.message.reply_text(
        "⚠️ Just click the '🖨 New Print Job' button below the text field to start!\n"
        "(If you don't see it, update your Telegram app)"
    )


async def post_init(application: Application):
    """Combine all setup tasks"""
    await setup_commands(application)
    await setup_menu_button(application)


async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle data from web app"""
    logger.log(DEBUG, "handle_web_app_data")
    data = json.loads(update.effective_message.web_app_data.data)
    await update.message.reply_text(
        f"📄 New print job created!\n"
        f"📞 Phone: {data['phone']}\n"
        f"📝 Description: {data['description']}"
    )

async def log_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"--- Full Update Received ---\n{update}\n--------------------------")
    # You might want to pass the update to the next handler if needed,
    # depending on your library version and handler group setup.
    # For simple logging, just printing might be enough for debugging.


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True, help='Telegram bot token')
    args = parser.parse_args()
    
    # Build application with post_init hook
    application = Application.builder() \
        .token(args.token) \
        .post_init(post_init) \
        .build()

    application.add_handler(MessageHandler(filters.ALL, log_all_updates), group=-1)
    
    #Add message handler
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages)
    )

    application.add_handler(CommandHandler("show_print_interface", show_print_interface))

    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))


    #application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    
    application.run_polling()