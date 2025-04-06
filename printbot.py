from telegram import Update, BotCommand, MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

async def setup_menu_button(application: Application):
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🖨 New Print Job",
            web_app=WebAppInfo(url="https://vitalya-dev.github.io/VTIHub/new_job.html")
        )
    )

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for any text messages"""
    await update.message.reply_text(
        "⚠️ Just click the '🖨 New Print Job' button below the text field to start!\n"
        "(If you don't see it, update your Telegram app)"
    )

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True, help='Telegram bot token')
    args = parser.parse_args()
    
    application = Application.builder().token(args.token).post_init(setup_menu_button).build()
    
    # Set commands for discovery
    # await application.bot.set_my_commands([
    #     BotCommand("start", "Open PrintBot interface"),
    #     BotCommand("help", "Get assistance")
    # ])
    
    # Handle any text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()