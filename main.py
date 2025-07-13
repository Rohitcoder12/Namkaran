# main.py
import logging
import os
import re
from pymongo import MongoClient

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.error import Forbidden

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- New Environment Variables ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID")) # Must be an integer

# Conversation states
SELECTING_CHANNEL, MANAGE_CHANNEL, SETTING_CAPTION, CONFIRM_REMOVE = range(4)

# --- MongoDB Functions ---

def get_db_collection():
    """Establishes a connection to MongoDB and returns the channels collection."""
    client = MongoClient(MONGO_URI)
    db = client.get_database() # The DB name is part of the URI
    return db.channels

channels_collection = get_db_collection()
# Create an index for faster lookups by admin_user_id
channels_collection.create_index("admin_user_id")

# --- Helper Functions (Now using MongoDB) ---

def get_channel_settings(channel_id):
    """Fetches settings for a specific channel from MongoDB."""
    return channels_collection.find_one({"_id": channel_id})

def get_user_channels(user_id):
    """Fetches all channels managed by a specific user."""
    channels_cursor = channels_collection.find({"admin_user_id": user_id})
    return [c["_id"] for c in channels_cursor]

# --- Command Handlers (Minor text updates) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    bot_username = context.bot.username
    add_to_channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Me to Your Channel ➕", url=add_to_channel_url)],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_photo(
        photo="https://i.imgur.com/rS2aYyH.jpeg",
        caption=f"Hey {user.mention_html()}!\n\nI am an Auto Caption Bot. I can automatically edit captions for files, videos, and photos you post in your channels.\n\n"
                f"1. Add me to your channel as an admin.\n"
                f"2. Use the <b>/settings</b> command to configure me.\n\n"
                f"Enjoy hassle-free channel management!",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "<b>How to use me:</b>\n\n"
        "1️⃣ <b>Add to Channel:</b> Add this bot to your channel as an admin with 'Post Messages' and 'Edit Messages' permissions.\n\n"
        "2️⃣ <b>Configure:</b> Send /settings in this private chat with me. I will show you a list of channels where I'm an admin. Select a channel to manage its settings.\n\n"
        "3️⃣ <b>Set Caption:</b> You can set a custom caption template. Use placeholders to insert file details automatically:\n"
        "   - <code>{file_name}</code> - The name of the file or 'Photo' for images.\n"
        "   - <code>{file_size}</code> - The size of the file (e.g., 12.34 MB).\n"
        "   - <code>{file_caption}</code> - The original caption of the file (if any).\n\n"
        "4️⃣ <b>Link Remover:</b> Toggle this option to automatically remove any links from the captions.\n\n"
        "5️⃣ <b>Log Channel:</b> All processed files are automatically forwarded to a private log channel for your records."
    )
    keyboard = [[InlineKeyboardButton("⬅️ Back to Start", callback_data="start_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text=help_text, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
    else:
        await update.message.reply_text(text=help_text, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)

# --- Settings & Conversation Logic (Updated to use MongoDB) ---

async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    user_channels = get_user_channels(user_id)
    
    if not user_channels:
        await query.edit_message_text("I'm not an admin in any of your channels yet. Add me to a channel first, then try /settings again.")
        return ConversationHandler.END

    keyboard = []
    for channel_id in user_channels:
        try:
            chat = await context.bot.get_chat(channel_id)
            keyboard.append([InlineKeyboardButton(f"{chat.title}", callback_data=f"channel_{channel_id}")])
        except Exception as e:
            logger.error(f"Could not get chat info for {channel_id}: {e}")
            
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Choose a channel to manage its settings:", reply_markup=reply_markup)
    return SELECTING_CHANNEL

async def select_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    channel_id = int(query.data.split('_')[1])
    context.user_data['current_channel_id'] = channel_id
    
    settings = get_channel_settings(channel_id) or {} # Use empty dict if no settings found
    chat = await context.bot.get_chat(channel_id)

    caption_status = "Not Set" if not settings.get('caption_text') else "Set ✅"
    link_remover_status = "ON ✔️" if settings.get('link_remover_on') else "OFF ❌"
    
    keyboard = [
        [InlineKeyboardButton(f"📝 Caption: {caption_status}", callback_data="set_caption")],
        [InlineKeyboardButton(f"✂️ Link Remover: {link_remover_status}", callback_data="toggle_link_remover")],
        [InlineKeyboardButton("🗑️ Remove Channel", callback_data="remove_channel")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_channels")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Managing settings for: <b>{chat.title}</b>", reply_markup=reply_markup, parse_mode='HTML')
    return MANAGE_CHANNEL

async def save_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_id = context.user_data['current_channel_id']
    new_caption_text = update.message.text
    
    channels_collection.update_one(
        {"_id": channel_id},
        {"$set": {"caption_text": new_caption_text}},
        upsert=True
    )
    
    await update.message.reply_text("✅ Caption updated successfully!")
    await back_to_manage(update, context)
    return MANAGE_CHANNEL

async def toggle_link_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data['current_channel_id']

    # Get current state or default to False
    current_setting = get_channel_settings(channel_id)
    current_state = current_setting.get('link_remover_on', False) if current_setting else False

    channels_collection.update_one(
        {"_id": channel_id},
        {"$set": {"link_remover_on": not current_state}},
        upsert=True
    )
    
    await select_channel(update, context)
    return MANAGE_CHANNEL

async def perform_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    channel_id = context.user_data['current_channel_id']

    channels_collection.delete_one({"_id": channel_id})
    
    await query.edit_message_text("Channel removed successfully.")
    return ConversationHandler.END

# --- Core Auto-Captioning & Forwarding Logic ---

async def auto_caption_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.channel_post:
        return

    message = update.channel_post
    channel_id = message.chat.id
    message_id = message.message_id
    
    settings = get_channel_settings(channel_id)
    if not settings:
        return # Channel is not managed by the bot

    # --- NEW: Forward the message to the log channel ---
    try:
        await context.bot.forward_message(
            chat_id=LOG_CHANNEL_ID,
            from_chat_id=channel_id,
            message_id=message_id
        )
        logger.info(f"Forwarded message {message_id} from {channel_id} to log channel {LOG_CHANNEL_ID}")
    except Forbidden:
        logger.error(f"Bot is not an admin in the log channel ({LOG_CHANNEL_ID}) or cannot send messages.")
    except Exception as e:
        logger.error(f"Failed to forward message from {channel_id} to log channel: {e}")

    # --- Existing captioning logic ---
    file_caption = message.caption or ""
    
    # Handle different file types including Photos
    file_obj = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
    if not file_obj:
        return

    # Photos don't have a file_name, so we handle that
    file_name = getattr(file_obj, 'file_name', 'Photo') # Defaults to 'Photo' if no file_name attribute
    file_size_bytes = file_obj.file_size
    file_size_mb = f"{file_size_bytes / (1024*1024):.2f} MB" if file_size_bytes else "N/A"

    new_caption = settings.get("caption_text") or file_caption
    
    if new_caption:
        new_caption = new_caption.replace("{file_name}", str(file_name))
        new_caption = new_caption.replace("{file_size}", file_size_mb)
        new_caption = new_caption.replace("{file_caption}", file_caption)

    if settings.get("link_remover_on"):
        new_caption = re.sub(r'https?://\S+', '', new_caption).strip()

    try:
        if new_caption != file_caption:
            await context.bot.edit_message_caption(
                chat_id=channel_id,
                message_id=message_id,
                caption=new_caption,
                parse_mode='HTML'
            )
            logger.info(f"Edited caption for message {message_id} in channel {channel_id}")
    except Exception as e:
        logger.error(f"Failed to edit caption in {channel_id}: {e}")

async def handle_new_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles when the bot is made an admin in a new channel."""
    if not update.my_chat_member:
        return

    new_member = update.my_chat_member.new_chat_member
    if new_member.status == 'administrator':
        user_id = update.my_chat_member.from_user.id
        chat_id = update.my_chat_member.chat.id
        
        logger.info(f"Bot promoted to admin in {chat_id} by user {user_id}")
        
        # Add channel to DB, linking it to the user who added it
        channels_collection.update_one(
            {"_id": chat_id},
            {"$set": {"admin_user_id": user_id}},
            upsert=True
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ I've been successfully added as an admin to <b>{update.my_chat_member.chat.title}</b>!\n\n"
                 f"You can now configure its settings using the /settings command." ,
            parse_mode='HTML'
        )

# (The ConversationHandler and main function setup remain largely the same, but I'm including them
# fully for completeness and updating the MessageHandler filter for photos.)

async def set_caption_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    help_text = "Send me the new caption text. Use these placeholders:\n`{file_name}`\n`{file_size}`\n`{file_caption}`"
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_manage")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text=help_text, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
    return SETTING_CAPTION

async def remove_channel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("Yes, Remove it", callback_data="confirm_delete")],
        [InlineKeyboardButton("No, Go Back", callback_data="back_to_manage")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Are you sure?", reply_markup=reply_markup)
    return CONFIRM_REMOVE
    
async def back_to_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # A bit of a hack to recreate the menu without a query object
    class DummyQuery:
        def __init__(self, data, from_user): self.data = data; self.from_user = from_user
        async def answer(self): pass

    user = update.effective_user
    channel_id = context.user_data['current_channel_id']
    dummy_query = DummyQuery(f"channel_{channel_id}", user)
    
    if update.callback_query: await update.callback_query.message.delete()

    await select_channel(dummy_query, context)
    return MANAGE_CHANNEL

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Operation canceled.")
    return ConversationHandler.END

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    
    settings_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(settings_start, pattern='^settings_menu$')],
        states={
            SELECTING_CHANNEL: [CallbackQueryHandler(select_channel, pattern='^channel_')],
            MANAGE_CHANNEL: [
                CallbackQueryHandler(set_caption_prompt, pattern='^set_caption$'),
                CallbackQueryHandler(toggle_link_remover, pattern='^toggle_link_remover$'),
                CallbackQueryHandler(remove_channel_confirm, pattern='^remove_channel$'),
                CallbackQueryHandler(settings_start, pattern='^back_to_channels$'),
            ],
            SETTING_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_caption), CallbackQueryHandler(back_to_manage, pattern='^back_to_manage$')],
            CONFIRM_REMOVE: [CallbackQueryHandler(perform_remove_channel, pattern='^confirm_delete$'), CallbackQueryHandler(back_to_manage, pattern='^back_to_manage$')],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^cancel$')],
        per_user=True, per_chat=True
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", start))
    application.add_handler(CallbackQueryHandler(help_command, pattern='^help$'))
    application.add_handler(CallbackQueryHandler(start, pattern='^start_menu$'))
    application.add_handler(settings_conv_handler)
    
    # Updated filter to include photos
    file_filter = (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO) & filters.ChatType.CHANNEL
    application.add_handler(MessageHandler(file_filter, auto_caption_handler))
    
    application.add_handler(MessageHandler(filters.ChatMemberUpdated.MY_CHAT_MEMBER, handle_new_admin))

    application.run_polling()

if __name__ == "__main__":
    main()
