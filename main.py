# main.py (Final Definitive Version for Direct Execution)
import logging
import os
import re
import html
import json
import traceback
import asyncio
import random
from pymongo import MongoClient
from pymongo.errors import ConfigurationError

from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.error import Forbidden, BadRequest
from telegram.constants import ParseMode

# --- Web Server for Hosting ---
app = Flask('')
@app.route('/')
def home():
    return "I'm alive!"
def run_flask():
  # Gunicorn uses port 8000 by default, so we'll use that.
  app.run(host='0.0.0.0', port=8000)
def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- Basic Setup ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID"))
DEVELOPER_CHAT_ID = os.environ.get("DEVELOPER_CHAT_ID")

# --- Photo Links ---
PHOTO_LINKS = [
    "https://telegra.ph/file/a7e53093198114a383461.jpg", "https://telegra.ph/file/984b725c899c7595a1a14.jpg",
    "https://telegra.ph/file/01a4f475143a7593c6803.jpg", "https://telegra.ph/file/d0701c3a647e704689e47.jpg",
    "https://telegra.ph/file/d598379435b7e3f28d844.jpg", "https://telegra.ph/file/b083c7a76326c111c1d63.jpg",
    "https://telegra.ph/file/153d837651c640702c2e9.jpg", "https://telegra.ph/file/49c79237c444057863583.jpg"
]

# --- Conversation states ---
SELECT_CHANNEL, MAIN_MENU, CAPTION_MENU, WORDS_REMOVER_MENU, AWAITING_CAPTION, AWAITING_WORDS, CONFIRM_REMOVE = range(7)

# --- Database & Helper Functions ---
def get_db_collection():
    if not MONGO_DB_NAME: raise ValueError("MONGO_DB_NAME environment variable is not set.")
    try: client = MongoClient(MONGO_URI); db = client[MONGO_DB_NAME]; return db.channels
    except Exception as e: logger.error(f"Could not connect to MongoDB: {e}"); raise
channels_collection = get_db_collection(); channels_collection.create_index("admin_user_id")
def get_channel_settings(channel_id): return channels_collection.find_one({"_id": channel_id})
def get_user_channels(user_id):
    channels_cursor = channels_collection.find({"admin_user_id": user_id}); return [c["_id"] for c in channels_cursor]

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, BadRequest) and "message is not modified" in str(context.error).lower(): return
    if DEVELOPER_CHAT_ID:
        try:
            tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
            tb_string = "".join(tb_list); update_str = update.to_dict() if isinstance(update, Update) else str(update)
            message = (f"An exception was raised: {html.escape(str(context.error))}\n\n<b>Update:</b>\n<pre>{html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))[:1000]}</pre>\n\n<b>Traceback:</b>\n<pre>{html.escape(tb_string)[-2000:]}</pre>")
            await context.bot.send_message(chat_id=DEVELOPER_CHAT_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Failed to send error log to developer: {e}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user; bot_username = context.bot.username
    add_to_channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages"
    keyboard = [[InlineKeyboardButton("➕ Add Me to Your Channel ➕", url=add_to_channel_url)], [InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")], [InlineKeyboardButton("❓ Help", callback_data="help")], [InlineKeyboardButton("💬 Any Query?", url="https://t.me/RexonBlack")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption = f"Hey {user.mention_html()}!\n\nI am an Auto Caption Bot..."
    photo_url = random.choice(PHOTO_LINKS)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_media(media=InputMediaPhoto(media=photo_url, caption=caption, parse_mode='HTML'), reply_markup=reply_markup)
    else: 
        msg = await update.message.reply_photo(photo=photo_url, caption=caption, parse_mode='HTML', reply_markup=reply_markup)
        context.user_data['main_menu_message_id'] = msg.message_id
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = "<b>How to use me:</b>\n\n1️⃣ Add to Channel\n2️⃣ Configure via /settings\n3️⃣ Set Caption with placeholders\n4️⃣ Toggle Link Remover"
    keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="start_menu")]]
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_caption(caption=help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML', disable_web_page_preview=True)
    else: await update.message.reply_text(text=help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML', disable_web_page_preview=True)

# --- Conversation Flow ---
async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query: await query.answer()
    user_id = update.effective_user.id
    user_channels_ids = get_user_channels(user_id)
    keyboard = []
    for channel_id in user_channels_ids:
        try:
            chat = await context.bot.get_chat(channel_id)
            keyboard.append([InlineKeyboardButton(f"{chat.title}", callback_data=f"channel_{channel_id}")])
        except (Forbidden, BadRequest):
            logger.warning(f"Bot can't access channel {channel_id}. Removing from DB.")
            channels_collection.delete_one({"_id": channel_id, "admin_user_id": user_id})
    if not keyboard:
        text = "I'm not an admin in any of your channels yet. Add me to a channel first."
        if query: await query.message.edit_caption(caption=text, reply_markup=None)
        else: await update.message.reply_text(text)
        return ConversationHandler.END
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    text = "Choose a channel to manage its settings:"
    
    current_message = query.message if query else update.message
    if query and current_message.photo: await current_message.delete()
    if not query: await update.message.delete()

    msg = await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data['menu_message_id'] = msg.message_id
    
    return SELECT_CHANNEL

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if 'channel_' in query.data:
        context.user_data['current_channel_id'] = int(query.data.split('_')[1])
    channel_id = context.user_data['current_channel_id']
    settings = get_channel_settings(channel_id) or {}; link_remover_status = "ON ✔️" if settings.get('link_remover_on', False) else "OFF ❌"
    keyboard = [[InlineKeyboardButton("📝 Set Caption", callback_data="caption_menu")], [InlineKeyboardButton("🚫 Set Words Remover", callback_data="words_remover_menu")], [InlineKeyboardButton(f"✂️ Link Remover: {link_remover_status}", callback_data="toggle_link_remover")], [InlineKeyboardButton("🗑️ Remove Channel", callback_data="confirm_remove")], [InlineKeyboardButton("⬅️ Back", callback_data="settings_menu")]]
    await query.message.edit_text(f"Managing settings for: <b>{(await context.bot.get_chat(channel_id)).title}</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return MAIN_MENU

async def caption_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    settings = get_channel_settings(context.user_data['current_channel_id']) or {}
    caption_text = settings.get("caption_text", "Not Set")
    text = f"<b>Caption Settings</b>\n\nCurrent Caption:\n<pre>{html.escape(caption_text)}</pre>"
    keyboard = [[InlineKeyboardButton("✏️ Set Caption", callback_data="set_caption_prompt")], [InlineKeyboardButton("🗑️ Del Caption", callback_data="delete_caption")], [InlineKeyboardButton("⬅️ Back", callback_data="main_menu_back")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML', disable_web_page_preview=True)
    return CAPTION_MENU

async def set_caption_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    text = "Send me the new caption text..."
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="caption_menu")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAITING_CAPTION

async def save_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_id = context.user_data['current_channel_id']; new_caption_text = update.message.text
    channels_collection.update_one({"_id": channel_id}, {"$set": {"caption_text": new_caption_text}}, upsert=True)
    await update.message.delete()
    await caption_menu(update, context)
    return CAPTION_MENU

async def delete_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer("Caption deleted!")
    channels_collection.update_one({"_id": context.user_data['current_channel_id']}, {"$unset": {"caption_text": ""}})
    await caption_menu(update, context)
    return CAPTION_MENU

async def words_remover_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    settings = get_channel_settings(context.user_data['current_channel_id']) or {}
    banned_words = settings.get("banned_words", []); banned_words_text = ", ".join(banned_words) if banned_words else "No words blacklisted."
    text = f"<b>Words Remover Settings</b>\n\nThese words will be removed from filenames.\n\nCurrent Blacklist:\n<pre>{html.escape(banned_words_text)}</pre>"
    keyboard = [[InlineKeyboardButton("✏️ Set Blacklist", callback_data="set_words_remover_prompt")], [InlineKeyboardButton("🗑️ Del Blacklist", callback_data="delete_words_remover")], [InlineKeyboardButton("⬅️ Back", callback_data="main_menu_back")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return WORDS_REMOVER_MENU

async def set_words_remover_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    text = "Send words to remove from filenames, separated by commas."
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="words_remover_menu")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAITING_WORDS

async def save_words_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_id = context.user_data['current_channel_id']
    words_to_ban = [word.strip() for word in update.message.text.split(',') if word.strip()]
    channels_collection.update_one({"_id": channel_id}, {"$set": {"banned_words": words_to_ban}}, upsert=True)
    await update.message.delete()
    await words_remover_menu(update, context)
    return WORDS_REMOVER_MENU

async def delete_words_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer("Blacklist cleared!")
    channels_collection.update_one({"_id": context.user_data['current_channel_id']}, {"$unset": {"banned_words": ""}})
    await words_remover_menu(update, context)
    return WORDS_REMOVER_MENU
    
async def toggle_link_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    channel_id = context.user_data.get('current_channel_id')
    current_state = (get_channel_settings(channel_id) or {}).get('link_remover_on', False)
    channels_collection.update_one({"_id": channel_id}, {"$set": {"link_remover_on": not current_state}}, upsert=True)
    await main_menu(update, context)
    return MAIN_MENU

async def confirm_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    keyboard = [[InlineKeyboardButton("Yes, Remove it", callback_data="delete_channel")], [InlineKeyboardButton("No, Go Back", callback_data="main_menu_back")]]
    await query.message.edit_text("Are you sure?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_REMOVE

async def perform_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    channels_collection.delete_one({"_id": context.user_data['current_channel_id']})
    await query.message.edit_text("Channel removed successfully.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer(); await update.callback_query.message.delete()
    await update.effective_user.send_message("Operation canceled.")
    return ConversationHandler.END

async def auto_caption_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.channel_post: return
    message = update.channel_post; channel_id = message.chat.id; message_id = message.message_id
    settings = get_channel_settings(channel_id)
    if not settings: return
    try:
        if message.document: await context.bot.send_document(chat_id=LOG_CHANNEL_ID, document=message.document.file_id, caption=message.caption)
        elif message.video: await context.bot.send_video(chat_id=LOG_CHANNEL_ID, video=message.video.file_id, caption=message.caption)
        elif message.photo: await context.bot.send_photo(chat_id=LOG_CHANNEL_ID, photo=message.photo[-1].file_id, caption=message.caption)
        elif message.audio: await context.bot.send_audio(chat_id=LOG_CHANNEL_ID, audio=message.audio.file_id, caption=message.caption)
    except Exception as e: logger.error(f"Failed to re-upload message to log channel: {e}")
    file_caption = message.caption or ""; file_obj = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
    if not file_obj: return
    original_file_name = getattr(file_obj, 'file_name', 'Photo')
    cleaned_file_name = original_file_name
    if settings.get("link_remover_on"):
        cleaned_file_name = re.sub(r'https?://\S+|@\w+|\[.*?\]|\(.*?\)', '', cleaned_file_name)
        cleaned_file_name = re.sub(r'[_.-]{2,}', '_', cleaned_file_name).strip('_. -')
    banned_words = settings.get("banned_words", [])
    if banned_words:
        pattern = r'\b(' + '|'.join(re.escape(word) for word in banned_words) + r')\b'
        cleaned_file_name = re.sub(pattern, '', cleaned_file_name, flags=re.IGNORECASE).strip()
        cleaned_file_name = re.sub(r'\s{2,}', ' ', cleaned_file_name).strip()
        cleaned_file_name = re.sub(r'[_.-]{2,}', '_', cleaned_file_name).strip('_. -')
    file_title, file_ext = os.path.splitext(cleaned_file_name)
    new_caption_template = settings.get("caption_text") or ""
    if new_caption_template:
        file_size_mb = f"{file_obj.file_size / (1024*1024):.2f} MB" if file_obj.file_size else "N/A"
        safe_full_name = html.escape(str(cleaned_file_name)); safe_title = html.escape(file_title); safe_file_caption = html.escape(file_caption)
        new_caption = new_caption_template.replace("{file_name}", safe_full_name).replace("{file_title}", safe_title).replace("{file_size}", file_size_mb).replace("{file_caption}", safe_file_caption)
    else: new_caption = cleaned_file_name
    try:
        if new_caption != (message.caption or ""): await context.bot.edit_message_caption(chat_id=channel_id, message_id=message_id, caption=new_caption, parse_mode='HTML')
    except Exception as e:
        if not ('message is not modified' in str(e).lower()): logger.error(f"Failed to edit caption in {channel_id}: {e}")

async def handle_new_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.my_chat_member: return
    new_member = update.my_chat_member.new_chat_member
    if new_member.status == 'administrator':
        user_id = update.my_chat_member.from_user.id; chat_id = update.my_chat_member.chat.id
        logger.info(f"Bot promoted to admin in {chat_id} by user {user_id}")
        channels_collection.update_one({"_id": chat_id}, {"$set": {"admin_user_id": user_id}}, upsert=True)
        await context.bot.send_message(chat_id=user_id, text=f"✅ I've been successfully added as an admin to <b>{update.my_chat_member.chat.title}</b>!", parse_mode='HTML')


def run_bot_polling():
    """Sets up and runs the bot's polling loop."""
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_error_handler(error_handler)
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("settings", settings_start), CallbackQueryHandler(settings_start, pattern='^settings_menu$')],
        states={
            SELECT_CHANNEL: [CallbackQueryHandler(main_menu, pattern=r'^channel_')],
            MAIN_MENU: [
                CallbackQueryHandler(caption_menu, pattern='^manage_caption$'),
                CallbackQueryHandler(words_remover_menu, pattern='^manage_words_remover$'),
                CallbackQueryHandler(toggle_link_remover, pattern='^toggle_link_remover$'),
                CallbackQueryHandler(confirm_remove_channel, pattern='^confirm_remove$'),
                CallbackQueryHandler(settings_start, pattern='^settings_menu$'),
            ],
            CAPTION_MENU: [
                CallbackQueryHandler(set_caption_prompt, pattern='^set_caption_prompt$'),
                CallbackQueryHandler(delete_caption, pattern='^delete_caption$'),
                CallbackQueryHandler(main_menu, pattern='^main_menu_back$'),
            ],
            WORDS_REMOVER_MENU: [
                CallbackQueryHandler(set_words_remover_prompt, pattern='^set_words_remover_prompt$'),
                CallbackQueryHandler(delete_words_remover, pattern='^delete_words_remover$'),
                CallbackQueryHandler(main_menu, pattern='^main_menu_back$'),
            ],
            AWAITING_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_caption)],
            AWAITING_WORDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_words_remover)],
            CONFIRM_REMOVE: [CallbackQueryHandler(perform_remove_channel, pattern='^delete_channel$'), CallbackQueryHandler(main_menu, pattern='^main_menu_back$')],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^cancel$')],
        per_message=False, allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
    application.add_handler(CallbackQueryHandler(start, pattern='^start_menu$'))
    file_filter = (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO) & filters.ChatType.CHANNEL
    application.add_handler(MessageHandler(file_filter, auto_caption_handler))
    application.add_handler(ChatMemberHandler(handle_new_admin, ChatMemberHandler.MY_CHAT_MEMBER))
    
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Start the bot in a background thread
    bot_thread = Thread(target=run_bot_polling)
    bot_thread.daemon = True
    bot_thread.start()

    # The Flask app runs in the main thread, which Gunicorn will manage.
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))