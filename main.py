# main.py (Final version with Caption Font and Sub-menu features)
import logging
import os
import re
import html
import json
import traceback
from pymongo import MongoClient
from pymongo.errors import ConfigurationError

from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
  app.run(host='0.0.0.0', port=8080)
def keep_alive():
    t = Thread(target=run_flask)
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

# --- NEW: Expanded Conversation states ---
SELECTING_CHANNEL, MANAGE_CHANNEL, MANAGE_CAPTION, SETTING_CAPTION, CONFIRM_REMOVE = range(5)

# --- Database Functions ---
def get_db_collection():
    if not MONGO_DB_NAME: raise ValueError("MONGO_DB_NAME environment variable is not set.")
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB_NAME]
        return db.channels
    except Exception as e:
        logger.error(f"Could not connect to MongoDB: {e}")
        raise
channels_collection = get_db_collection()
channels_collection.create_index("admin_user_id")

# --- Helper Functions ---
def get_channel_settings(channel_id): return channels_collection.find_one({"_id": channel_id})
def get_user_channels(user_id):
    channels_cursor = channels_collection.find({"admin_user_id": user_id})
    return [c["_id"] for c in channels_cursor]

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, BadRequest) and "message is not modified" in str(context.error).lower():
        logger.warning("Tried to edit a message with the same content. Ignoring.")
        return
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    truncated_update = str(update_str)[:1000]; truncated_traceback = tb_string[-2000:]
    message = f"An exception was raised: {html.escape(str(context.error))}\n\n<b>Update:</b>\n<pre>{html.escape(truncated_update)}</pre>\n\n<b>Traceback (last 2000 chars):</b>\n<pre>{html.escape(truncated_traceback)}</pre>"
    if DEVELOPER_CHAT_ID:
        try: await context.bot.send_message(chat_id=DEVELOPER_CHAT_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Failed to send error log to developer: {e}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user; bot_username = context.bot.username
    add_to_channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages"
    keyboard = [[InlineKeyboardButton("➕ Add Me to Your Channel ➕", url=add_to_channel_url)], [InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")], [InlineKeyboardButton("❓ Help", callback_data="help")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption = f"Hey {user.mention_html()}!\n\nI am an Auto Caption Bot. I can automatically edit captions for files, videos, and photos you post in your channels.\n\n1. Add me to your channel as an admin.\n2. Use the <b>/settings</b> command to configure me.\n\nEnjoy hassle-free channel management!"
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.edit_message_caption(caption=caption, reply_markup=reply_markup, parse_mode='HTML')
        except BadRequest: await update.callback_query.edit_message_text(text=caption, reply_markup=reply_markup, parse_mode='HTML')
    else: await update.message.reply_photo(photo="https://i.imgur.com/rS2aYyH.jpeg", caption=caption, parse_mode='HTML', reply_markup=reply_markup)
    return ConversationHandler.END

# --- Placeholder for future features ---
async def placeholder_feature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("This feature is under development.", show_alert=True)

# --- THE FIX: Updated Settings & Conversation Logic ---

async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    query = update.callback_query
    if query: await query.answer(); message_to_edit = query.message
    else: message_to_edit = update.message
    user_channels = get_user_channels(user_id)
    if not user_channels:
        await message_to_edit.reply_text("I'm not an admin in any of your channels yet. Add me to a channel first, then try /settings again.")
        return ConversationHandler.END
    keyboard = []
    for channel_id in user_channels:
        try:
            chat = await context.bot.get_chat(channel_id)
            keyboard.append([InlineKeyboardButton(f"{chat.title}", callback_data=f"channel_{channel_id}")])
        except Exception as e: logger.error(f"Could not get chat info for {channel_id}: {e}")
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Choose a channel to manage its settings:"
    try: await message_to_edit.edit_text(text, reply_markup=reply_markup)
    except BadRequest:
        await message_to_edit.delete()
        await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    return SELECTING_CHANNEL

async def select_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    channel_id = int(query.data.split('_')[1])
    context.user_data['current_channel_id'] = channel_id
    settings = get_channel_settings(channel_id) or {}
    chat = await context.bot.get_chat(channel_id)
    link_remover_status = "ON ✔️" if settings.get('link_remover_on') else "OFF ❌"
    
    # NEW: Updated main settings menu
    keyboard = [
        [InlineKeyboardButton("📝 Set Caption 📝", callback_data="manage_caption")],
        [InlineKeyboardButton("🚫 Set Words Remover 🚫", callback_data="placeholder")],
        [InlineKeyboardButton("♻️ Set Suffix & Prefix ♻️", callback_data="placeholder")],
        [InlineKeyboardButton("➕ Set Replace Words ➕", callback_data="placeholder")],
        [InlineKeyboardButton(f"✂️ Link Remover: {link_remover_status}", callback_data="toggle_link_remover")],
        [InlineKeyboardButton("🗑️ Remove Channel", callback_data="remove_channel")],
        [InlineKeyboardButton("⬅️ Back", callback_data="settings_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Managing settings for: <b>{chat.title}</b>", reply_markup=reply_markup, parse_mode='HTML')
    return MANAGE_CHANNEL

# --- NEW: Caption Sub-Menu Logic ---
async def manage_caption_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data['current_channel_id']
    settings = get_channel_settings(channel_id) or {}
    
    caption_text = settings.get("caption_text", "Not Set")
    
    text = (f"<b>Caption Settings</b>\n\n"
            f"Current Caption:\n<pre>{html.escape(caption_text)}</pre>")

    keyboard = [
        [InlineKeyboardButton("✏️ Set Caption", callback_data="set_caption_prompt")],
        [InlineKeyboardButton("🗑️ Del Caption", callback_data="delete_caption"), InlineKeyboardButton("✍️ Caption Font", callback_data="caption_font_help")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"channel_{channel_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
    return MANAGE_CAPTION

async def delete_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Caption deleted!")
    channel_id = context.user_data['current_channel_id']
    channels_collection.update_one({"_id": channel_id}, {"$unset": {"caption_text": ""}})
    
    # Refresh the caption menu
    await manage_caption_menu(update, context)
    return MANAGE_CAPTION

async def caption_font_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    font_help_text = (
        "🔰 <b>About Caption Font</b> 🔰\n\n"
        "You can use HTML tags to format your caption text.\n\n"
        "➤ <b>Bold Text</b>\n  <code><b>{file_name}</b></code>\n\n"
        "➤ <i>Italic Text</i>\n  <code><i>{file_name}</i></code>\n\n"
        "➤ <u>Underline Text</u>\n  <code><u>{file_name}</u></code>\n\n"
        "➤ <s>Strike Text</s>\n  <code><s>{file_name}</s></code>\n\n"
        "➤ <spoiler>Spoiler Text</spoiler>\n  <code><spoiler>{file_name}</spoiler></code>\n\n"
        "➤ <code>Mono Text</code>\n  <code><code>{file_name}</code></code>\n\n"
        "➤ Hyperlink Text\n  <code><a href=\"https://t.me/your_link\">{file_name}</a></code>"
    )
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="manage_caption")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(font_help_text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
    return MANAGE_CAPTION

# --- Existing functions, slightly adapted ---
async def set_caption_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    help_text = "Send me the new caption text. Use the placeholders and HTML formatting as needed."
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="manage_caption")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=help_text, parse_mode='HTML', reply_markup=reply_markup)
    return SETTING_CAPTION

async def save_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_id = context.user_data['current_channel_id']; new_caption_text = update.message.text
    channels_collection.update_one({"_id": channel_id}, {"$set": {"caption_text": new_caption_text}}, upsert=True)
    await update.message.reply_text("✅ Caption updated successfully! Returning to the caption menu.")
    class DummyQuery:
        def __init__(self, data, message): self.data = data; self.message = message
        async def answer(self): pass
    await manage_caption_menu(Update(update.update_id, callback_query=DummyQuery(data="manage_caption", message=update.message)), context)
    return MANAGE_CAPTION

# ... (The rest of the code is largely unchanged) ...

async def toggle_link_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); channel_id = context.user_data['current_channel_id']
    current_setting = get_channel_settings(channel_id); current_state = current_setting.get('link_remover_on', False) if current_setting else False
    channels_collection.update_one({"_id": channel_id}, {"$set": {"link_remover_on": not current_state}}, upsert=True)
    await select_channel(update, context)
    return MANAGE_CHANNEL

async def remove_channel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    keyboard = [[InlineKeyboardButton("Yes, Remove it", callback_data="confirm_delete")], [InlineKeyboardButton("No, Go Back", callback_data=f"channel_{context.user_data['current_channel_id']}")] ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Are you sure?", reply_markup=reply_markup)
    return CONFIRM_REMOVE

async def perform_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); channel_id = context.user_data['current_channel_id']
    channels_collection.delete_one({"_id": channel_id})
    await query.edit_message_text("Channel removed successfully.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Operation canceled.")
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
    file_caption = message.caption or ""
    file_obj = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
    if not file_obj: return
    file_name = getattr(file_obj, 'file_name', 'Photo')
    file_size_mb = f"{file_obj.file_size / (1024*1024):.2f} MB" if file_obj.file_size else "N/A"
    new_caption = settings.get("caption_text") or file_caption
    if new_caption:
        new_caption = new_caption.replace("{file_name}", str(file_name)).replace("{file_size}", file_size_mb).replace("{file_caption}", file_caption)
    if settings.get("link_remover_on"): new_caption = re.sub(r'https?://\S+|@\w+', '', new_caption).strip()
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
        await context.bot.send_message(chat_id=user_id, text=f"✅ I've been successfully added as an admin to <b>{update.my_chat_member.chat.title}</b>!\n\nYou can now configure its settings using the /settings command." , parse_mode='HTML')

def main() -> None:
    keep_alive()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_error_handler(error_handler)
    
    # NEW: Updated ConversationHandler with more states
    settings_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("settings", settings_start), CallbackQueryHandler(settings_start, pattern='^settings_menu$')],
        states={
            SELECTING_CHANNEL: [CallbackQueryHandler(select_channel, pattern='^channel_')],
            MANAGE_CHANNEL: [
                CallbackQueryHandler(manage_caption_menu, pattern='^manage_caption$'),
                CallbackQueryHandler(toggle_link_remover, pattern='^toggle_link_remover$'),
                CallbackQueryHandler(remove_channel_confirm, pattern='^remove_channel$'),
                CallbackQueryHandler(placeholder_feature, pattern='^placeholder$'), # For new features
                CallbackQueryHandler(settings_start, pattern='^settings_menu$'),
            ],
            MANAGE_CAPTION: [
                CallbackQueryHandler(set_caption_prompt, pattern='^set_caption_prompt$'),
                CallbackQueryHandler(delete_caption, pattern='^delete_caption$'),
                CallbackQueryHandler(caption_font_help, pattern='^caption_font_help$'),
                CallbackQueryHandler(select_channel, pattern=r'^channel_'), # Back button
            ],
            SETTING_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_caption),
                CallbackQueryHandler(manage_caption_menu, pattern='^manage_caption') # Back button
            ],
            CONFIRM_REMOVE: [
                CallbackQueryHandler(perform_remove_channel, pattern='^confirm_delete$'),
                CallbackQueryHandler(select_channel, pattern=r'^channel_')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^cancel$')],
        per_message=False,
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(settings_conv_handler)
    application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
    application.add_handler(CallbackQueryHandler(start, pattern='^start_menu$'))
    file_filter = (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO) & filters.ChatType.CHANNEL
    application.add_handler(MessageHandler(file_filter, auto_caption_handler))
    application.add_handler(ChatMemberHandler(handle_new_admin, ChatMemberHandler.MY_CHAT_MEMBER))
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()