from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton(
            "🖨 Open PrintBot",
            web_app=WebAppInfo(url="https://vitalya-dev.github.io/VTIHub/new_job.html")
        )]
    ]
    
    await update.message.reply_text(
        "PrintBot Menu:\n",
        reply_markup=InlineKeyboardMarkup(keyboard)
)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True, help='Telegram bot token')
    args = parser.parse_args()

    application = Application.builder().token(args.token).build()
    
    application.add_handler(CommandHandler("start", start))
    
    print("Bot is running...")
    application.run_polling()