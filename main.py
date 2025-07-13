# main.py (Final Definitive Version)
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

# --- Conversation states ---
SELECTING_CHANNEL, MANAGE_CHANNEL, MANAGE_CAPTION, SETTING_CAPTION, CONFIRM_REMOVE = range(5)

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
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list); update_str = update.to_dict() if isinstance(update, Update) else str(update)
    truncated_update = str(update_str)[:1000]; truncated_traceback = tb_string[-2000:]
    message = f"An exception was raised: {html.escape(str(context.error))}\n\n<b>Update:</b>\n<pre>{html.escape(truncated_update)}</pre>\n\n<b>Traceback:</b>\n<pre>{html.escape(truncated_traceback)}</pre>"
    if DEVELOPER_CHAT_ID:
        try: await context.bot.send_message(chat_id=DEVELOPER_CHAT_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception as e: logger.error(f"Failed to send error log to developer: {e}")

# --- THE PERMANENT FIX ---
async def edit_or_send_message(ctx, text, reply_markup):
    """A robust helper to edit a message, correctly distinguishing between photo and text messages."""
    message_obj = None
    if isinstance(ctx, Update):
        message_obj = ctx.callback_query.message if ctx.callback_query else ctx.message
    else:
        message_obj = ctx
    
    try:
        if message_obj.photo:
            # This is a photo message, so we must use edit_caption
            await message_obj.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        else:
            # This is a text message, so we use edit_text
            await message_obj.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.warning("Message not modified, ignoring.")
        else:
            logger.error(f"Error editing message: {e}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user; bot_username = context.bot.username
    add_to_channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages"
    keyboard = [[InlineKeyboardButton("➕ Add Me to Your Channel ➕", url=add_to_channel_url)], [InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")], [InlineKeyboardButton("❓ Help", callback_data="help")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption = f"Hey {user.mention_html()}!\n\nI am an Auto Caption Bot. I can automatically edit captions for files, videos, and photos you post in your channels.\n\n1. Add me to your channel as an admin.\n2. Use the <b>/settings</b> command to configure me.\n\nEnjoy hassle-free channel management!"
    if update.callback_query:
        await update.callback_query.answer()
        await edit_or_send_message(update.callback_query.message, caption, reply_markup)
    else: await update.message.reply_photo(photo="https://i.imgur.com/rS2aYyH.jpeg", caption=caption, parse_mode='HTML', reply_markup=reply_markup)
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = "<b>How to use me:</b>\n\n1️⃣ <b>Add to Channel:</b> Add this bot as an admin...\n\n2️⃣ <b>Configure:</b> Send /settings...\n\n3️⃣ <b>Set Caption:</b> Use placeholders...\n\n4️⃣ <b>Link Remover:</b> Toggle on/off.\n\n5️⃣ <b>Log Channel:</b> Files are re-uploaded for your records."
    keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="start_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.answer()
        await edit_or_send_message(update.callback_query.message, help_text, reply_markup)
    else: await update.message.reply_text(text=help_text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)

# ... (The rest of the code is unchanged and correct) ...

async def placeholder_feature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer("This feature is under development.", show_alert=True)

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
        except Forbidden:
            logger.warning(f"Bot is not in channel {channel_id} anymore. Removing from DB.")
            channels_collection.delete_one({"_id": channel_id, "admin_user_id": user_id})
        except Exception as e: logger.error(f"Could not get chat info for {channel_id}: {e}")
    if not keyboard:
        text = "I'm not an admin in any of your channels yet. Add me to a channel first, then try /settings again."
        await edit_or_send_message(update, text, None)
        return ConversationHandler.END
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    await edit_or_send_message(update, "Choose a channel to manage its settings:", InlineKeyboardMarkup(keyboard))
    return SELECTING_CHANNEL

async def select_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    channel_id = int(query.data.split('_')[1]); context.user_data['current_channel_id'] = channel_id
    settings = get_channel_settings(channel_id) or {}; link_remover_status = "ON ✔️" if settings.get('link_remover_on') else "OFF ❌"
    keyboard = [[InlineKeyboardButton("📝 Set Caption 📝", callback_data="manage_caption")], [InlineKeyboardButton("🚫 Set Words Remover 🚫", callback_data="placeholder")], [InlineKeyboardButton(f"✂️ Link Remover: {link_remover_status}", callback_data="toggle_link_remover")], [InlineKeyboardButton("🗑️ Remove Channel", callback_data="remove_channel")], [InlineKeyboardButton("⬅️ Back", callback_data="settings_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await edit_or_send_message(query.message, f"Managing settings for: <b>{(await context.bot.get_chat(channel_id)).title}</b>", reply_markup)
    return MANAGE_CHANNEL

async def manage_caption_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    settings = get_channel_settings(context.user_data['current_channel_id']) or {}
    caption_text = settings.get("caption_text", "Not Set")
    text = f"<b>Caption Settings</b>\n\nCurrent Caption:\n<pre>{html.escape(caption_text)}</pre>"
    keyboard = [[InlineKeyboardButton("✏️ Set Caption", callback_data="set_caption_prompt")], [InlineKeyboardButton("🗑️ Del Caption", callback_data="delete_caption"), InlineKeyboardButton("✍️ Caption Font", callback_data="caption_font_help")], [InlineKeyboardButton("⬅️ Back", callback_data=f"channel_{context.user_data['current_channel_id']}")]]
    await edit_or_send_message(query.message, text, InlineKeyboardMarkup(keyboard))
    return MANAGE_CAPTION

async def caption_font_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    font_help_text = ('🔰 <b>About Caption Font</b> 🔰\n\n' 'You can use HTML tags to format your caption text.\n\n' '➤ <b>Bold Text</b>\n<pre>' + html.escape('<b>{file_name}</b>') + '</pre>\n\n' '➤ <i>Italic Text</i>\n<pre>' + html.escape('<i>{file_name}</i>') + '</pre>\n\n' '➤ <u>Underline Text</u>\n<pre>' + html.escape('<u>{file_name}</u>') + '</pre>\n\n' '➤ <s>Strike Text</s>\n<pre>' + html.escape('<s>{file_name}</s>') + '</pre>\n\n' '➤ Spoiler Text\n<pre>' + html.escape('<tg-spoiler>{file_name}</tg-spoiler>') + '</pre>\n\n' '➤ <code>Mono Text</code>\n<pre>' + html.escape('<code>{file_name}</code>') + '</pre>\n\n' '➤ Hyperlink Text\n<pre>' + html.escape('<a href="https://t.me/RexonBlack">{file_name}</a>') + '</pre>')
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="manage_caption")]]
    await edit_or_send_message(query.message, font_help_text, InlineKeyboardMarkup(keyboard))
    return MANAGE_CAPTION

async def set_caption_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    text = "Send me the new caption text. Use the placeholders and HTML formatting as needed."
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="manage_caption")]]
    await edit_or_send_message(query.message, text, InlineKeyboardMarkup(keyboard))
    return SETTING_CAPTION

async def save_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_id = context.user_data['current_channel_id']; new_caption_text = update.message.text
    channels_collection.update_one({"_id": channel_id}, {"$set": {"caption_text": new_caption_text}}, upsert=True)
    await update.message.reply_text("✅ Caption updated successfully!")
    class DummyQuery:
        def __init__(self, message): self.message = message
        async def answer(self): pass
    await manage_caption_menu(Update(update.update_id, callback_query=DummyQuery(message=update.message)), context)
    return MANAGE_CAPTION

async def delete_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer("Caption deleted!")
    channels_collection.update_one({"_id": context.user_data['current_channel_id']}, {"$unset": {"caption_text": ""}})
    await manage_caption_menu(update, context)
    return MANAGE_CAPTION

async def toggle_link_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    channel_id = context.user_data['current_channel_id']
    current_state = (get_channel_settings(channel_id) or {}).get('link_remover_on', False)
    channels_collection.update_one({"_id": channel_id}, {"$set": {"link_remover_on": not current_state}}, upsert=True)
    await select_channel(update, context)
    return MANAGE_CHANNEL

async def remove_channel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    keyboard = [[InlineKeyboardButton("Yes, Remove it", callback_data="confirm_delete")], [InlineKeyboardButton("No, Go Back", callback_data=f"channel_{context.user_data['current_channel_id']}")] ]
    await edit_or_send_message(query.message, "Are you sure?", InlineKeyboardMarkup(keyboard))
    return CONFIRM_REMOVE

async def perform_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    channels_collection.delete_one({"_id": context.user_data['current_channel_id']})
    await edit_or_send_message(query.message, "Channel removed successfully.", None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await edit_or_send_message(update.callback_query.message, "Operation canceled.", None)
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
    new_caption_template = settings.get("caption_text") or file_caption
    new_caption = new_caption_template
    if new_caption:
        file_size_mb = f"{file_obj.file_size / (1024*1024):.2f} MB" if file_obj.file_size else "N/A"
        safe_file_name = html.escape(str(cleaned_file_name))
        safe_file_caption = html.escape(file_caption)
        new_caption = new_caption.replace("{file_name}", safe_file_name).replace("{file_size}", file_size_mb).replace("{file_caption}", safe_file_caption)
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
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("settings", settings_start), CallbackQueryHandler(settings_start, pattern='^settings_menu$')],
        states={
            SELECTING_CHANNEL: [CallbackQueryHandler(select_channel, pattern='^channel_')],
            MANAGE_CHANNEL: [CallbackQueryHandler(manage_caption_menu, pattern='^manage_caption$'), CallbackQueryHandler(toggle_link_remover, pattern='^toggle_link_remover$'), CallbackQueryHandler(remove_channel_confirm, pattern='^remove_channel$'), CallbackQueryHandler(placeholder_feature, pattern='^placeholder$')],
            MANAGE_CAPTION: [CallbackQueryHandler(set_caption_prompt, pattern='^set_caption_prompt$'), CallbackQueryHandler(delete_caption, pattern='^delete_caption$'), CallbackQueryHandler(caption_font_help, pattern='^caption_font_help$'), CallbackQueryHandler(select_channel, pattern=r'^channel_')],
            SETTING_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_caption), CallbackQueryHandler(manage_caption_menu, pattern='^manage_caption$')],
            CONFIRM_REMOVE: [CallbackQueryHandler(perform_remove_channel, pattern='^confirm_delete$'), CallbackQueryHandler(select_channel, pattern=r'^channel_')],
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
    main()