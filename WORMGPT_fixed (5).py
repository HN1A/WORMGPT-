# -*- coding: utf-8 -*-
import logging
import datetime
# import requests # Remove synchronous requests
import aiohttp # Add asynchronous http client
import re
import json
import os
import asyncio
import time # For debounced saving
from typing import Dict, Any, List, Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
    JobQueue,
    PicklePersistence # Using persistence for data
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
# from telegram.helpers import escape_markdown # Use custom escape function

# ---------------------- إعدادات البوت ----------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- تحذير: لا تضع التوكن هنا مباشرة في الإنتاج ---
# --- يفضل استخدام متغيرات البيئة أو ملف إعدادات ---
# --- تم إخفاء التوكن --- 
# TODO: Replace with your actual token or load from environment variables/config file
BOT_TOKEN = "7403353511:AAFZm-R-BFwHVi4OmmxTggbIxejEC7iv9nc" # Replace with your actual token or load from env
ADMIN_ID = "7445800040"  # معرف الأدمن
API_URL = "https://dev-apis-xyz.pantheonsite.io/wp-content/apis/freeAi.php"
API_TIMEOUT = 30 # Adjusted timeout
# DATA_FILE = "user_data.json" # Replaced by persistence
PERSISTENCE_FILE = "bot_persistence.pkl"

# إعدادات الرسائل التجريبية
MAX_FREE_MESSAGES = 3
FREE_TRIAL_MESSAGE = "🆓 لديك *{remaining}* رسائل مجانية متبقية\\. بعد انتهائها، يلزمك الاشتراك\\."
TRIAL_EXHAUSTED = "⏳ لقد استنفدت جميع رسائلك المجانية\\. للحصول على المزيد، يرجى الاشتراك الآن لمواصلة الاستخدام\\."

# إعدادات الحفظ المؤجل (Debounced Saving)
SAVE_DEBOUNCE_SECONDS = 5 # Save data at most every 5 seconds
last_save_time = 0
save_scheduled = False
save_lock = asyncio.Lock()

# ---------------------- هياكل البيانات (تدار الآن بواسطة Persistence) ----------------------
# The UserData class is no longer needed explicitly here if using PicklePersistence.
# Data will be stored in context.bot_data and context.user_data.
# We'll define helper functions to access this data safely.

async def update_persistence(context: ContextTypes.DEFAULT_TYPE):
    """Schedules a debounced save of persistence data."""
    global last_save_time, save_scheduled
    current_time = time.time()
    async with save_lock:
        if not save_scheduled and (current_time - last_save_time > SAVE_DEBOUNCE_SECONDS):
            # Save immediately if debounce time has passed
            if context.application.persistence:
                 await context.application.persistence.flush()
                 last_save_time = current_time
                 logger.info("Persistence data flushed immediately.")
            else:
                 logger.warning("Persistence object not found, cannot flush data.")

        elif not save_scheduled:
            # Schedule a save after the debounce period
            save_scheduled = True
            context.job_queue.run_once(do_save_persistence, SAVE_DEBOUNCE_SECONDS, name='debounce_save')
            logger.debug("Persistence save scheduled.")

async def do_save_persistence(context: ContextTypes.DEFAULT_TYPE):
    """Performs the actual save operation."""
    global last_save_time, save_scheduled
    async with save_lock:
        if context.application.persistence:
            await context.application.persistence.flush()
            last_save_time = time.time()
            save_scheduled = False
            logger.info("Persistence data flushed after debounce.")
        else:
            logger.warning("Persistence object not found in scheduled save, cannot flush data.")


# Helper functions to manage user data within context
def get_user_stats(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Dict[str, Any]:
    # Ensure user_data exists for the user using context.user_data
    # setdefault ensures the user_id key exists before accessing 'stats'
    user_specific_data = context.user_data.setdefault(user_id, {})
    # Access and modify stats using the mutable user_specific_data dict
    return user_specific_data.setdefault("stats", {"messages_sent": 0, "last_active": None, "join_date": None})

def get_vip_users(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, Dict[str, Any]]:
    return context.bot_data.setdefault("vip_users", {})

def get_pending_payments(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, Dict[str, Any]]:
    return context.bot_data.setdefault("pending_payments", {})

def get_banned_users(context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    return context.bot_data.setdefault("banned_users", [])

def get_free_trial_users(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, int]:
    return context.bot_data.setdefault("free_trial_users", {})

# Initialize bot_data structure if empty
def initialize_bot_data(application: Application):
     logger.info("Initializing bot_data structure...")
     application.bot_data.setdefault("vip_users", {})
     application.bot_data.setdefault("pending_payments", {})
     application.bot_data.setdefault("banned_users", [])
     application.bot_data.setdefault("free_trial_users", {})
     logger.info("Bot_data structure initialized.")


# رسائل البوت المحسنة (مع تنسيق Markdown V2)
# تم تعديل بعض الرسائل وتصحيح الـ escape
bot_messages = {
    "welcome": "✨ أهلاً بك في *WORMGPT* \\- بوت الذكاء الاصطناعي المتطور *بدون قيود*\\! ✨\\n\\nأنا هنا لمساعدتك في الإجابة على استفساراتك، كتابة النصوص، توليد الأفكار، والمزيد\\.\\n\\n*ابدأ الآن* بالاختيار من القوائم أدناه أو أرسل رسالتك مباشرة\\.",
    "not_vip": "🔒 عذراً، اشتراكك غير مفعل أو انتهت صلاحيته\\. يمكنك استخدام *الرسائل المجانية* المتبقية \\(إن وجدت\\) أو *الاشتراك* في إحدى خططنا المميزة للاستخدام *غير المحدود*\\.",
    "vip_active": "🎉 تهانينا\\! تم تفعيل اشتراكك بنجاح في *WORMGPT*\\. يمكنك الآن الاستمتاع بتجربة الذكاء الاصطناعي *بدون قيود*\\.",
    "subscription_expired": "⏳ نأسف لإبلاغك بانتهاء صلاحية اشتراكك\\. لتجديد الوصول *غير المحدود*، يرجى اختيار إحدى خطط التجديد\\.",
    "admin_greeting": "👑 أهلاً بك أيها المدير في لوحة تحكم *WORMGPT*\\!",
    "broadcast_template": "📢 *رسالة من الإدارة* 📢\\n\\n{message}", # Ensure {message} is properly escaped before sending if needed
    "features": "🌟 *ميزات WORMGPT* 🌟\\n\\n🤖 *ذكاء اصطناعي متطور بدون قيود*\\.\\n💻 دعم *الأكواد البرمجية* بتنسيق Markdown \\(```\\) و \\(`\\`\\)\\.\\n✨ واجهة مستخدم *سهلة وجذابة*\\.\\n📊 *إحصائيات* مفصلة للمشتركين والأدمن\\.\n⚡️ *سرعة استجابة* فائقة \\(محسّنة\\)\\.\\n💳 نظام *اشتراكات* مرن ومتكامل\\.\\n📞 *دعم فني* سريع عبر المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ)\\.\\n🆓 *تجربة مجانية* \\({max_free} رسائل\\) للمستخدمين الجدد\\.", # Placeholder for max_free
    "user_banned": "⛔ تم حظرك من استخدام البوت\\. إذا كنت تعتقد أن هذا خطأ، يرجى التواصل مع المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ)\\.",
    "admin_help": "🛠 *أوامر الأدمن* 🛠\\n\\n`/admin` \\- عرض لوحة التحكم الرئيسية\\.\\n`/stats` \\- عرض إحصائيات البوت التفصيلية\\.\\n`/ban` \\- بدء محادثة حظر مستخدم\\.\\n`/unban` \\- بدء محادثة إلغاء حظر مستخدم\\.\\n`/broadcast` \\- بدء محادثة إرسال إعلان للمشتركين\\.\\n`/adminhelp` \\- عرض هذه الرسالة المساعدة\\.",
    "api_error": "⚠️ عذراً، حدث خطأ أثناء التواصل مع خادم الذكاء الاصطناعي\\. يرجى المحاولة مرة أخرى بعد قليل\\.",
    "api_timeout": "⏳ استغرقت معالجة طلبك وقتاً أطول من المعتاد \\(تجاوز المهلة\\)\\. قد يكون هناك ضغط على الخادم\\. يرجى المحاولة مجدداً\\.",
    "payment_pending": "⏳ طلب اشتراكك لا يزال معلقاً أو انتهت صلاحيته \\(أكثر من يومين\\)\\. يرجى التواصل مع المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ) لتأكيد الدفع أو إعادة الطلب\\.",
    "payment_success": "✅ تم استلام طلب اشتراكك بنجاح\\! لإتمام العملية وتفعيل الاشتراك، يرجى التواصل مع المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ)\\.",
    "user_unbanned": "🎉 تم إلغاء حظرك بنجاح\\! يمكنك الآن استخدام *WORMGPT* مرة أخرى\\.",
    "subscription_info": "📊 *معلومات اشتراكك* 📊\\n\\n📦 الخطة: *{plan}*\\n⏳ تنتهي في: `{expiry}`\\n✉️ الرسائل المرسلة: *{messages}* رسالة",
    "plan_choices": "🛒 *اختر خطة الاشتراك التي تناسبك* 🛒\\n\\n{plan_details}",
    "confirm_plan": "🛒 *تأكيد طلب الاشتراك* 🛒\\n\\n📦 الخطة المختارة: *{plan}*\\n💰 السعر: *{price}*\\n⏳ المدة: *{days} يوم*\\n\\nهل أنت متأكد من رغبتك في المتابعة؟",
    "admin_stats": "📊 *إحصائيات WORMGPT* 📊\\n\\n👥 إجمالي المستخدمين \\(تفاعلوا\\): *{users}*\\n⭐ المشتركين النشطين: *{vips}*\\n✉️ إجمالي الرسائل \\(منذ آخر تشغيل\\): *{messages}*\\n⛔ المحظورين: *{banned}*\\n🆓 مستخدمين التجربة النشطين: *{trial_users}*\\n⏳ طلبات دفع معلقة: *{pending_payments}*",
    "vip_list": "📋 *قائمة المشتركين النشطين* \\(VIP\\) 📋\\n\\n{list}",
    "no_vips": "ℹ️ لا يوجد مشتركين نشطين حالياً\\.",
    "user_activated": "✅ تم تفعيل اشتراك المستخدم `{user}` بنجاح\\!",
    "user_deactivated": "✅ تم إلغاء اشتراك المستخدم `{user}` بنجاح\\.",
    "user_banned_success": "✅ تم حظر المستخدم `{user}` بنجاح\\!",
    "user_unbanned_success": "✅ تم إلغاء حظر المستخدم `{user}` بنجاح\\!",
    "broadcast_sent": "🎉 تم الانتهاء من إرسال الإعلان\\.\\n\\n✅ نجح الإرسال لـ *{success}* مشترك\\.\\n❌ فشل الإرسال لـ *{failed}* مشترك \\(قد يكون بسبب حظر البوت أو مشاكل أخرى\\)\\.",
    "invalid_user": "⚠️ المعرف المدخل غير صالح\\. يرجى إدخال معرف مستخدم صحيح \\(مثل `@username`\\) أو ID رقمي\\.",
    "admin_only": "⛔ عذراً، هذا الأمر مخصص للمدير فقط\\.",
    "subscription_required": "🔒 هذه الميزة تتطلب اشتراكاً فعالاً\\. يرجى الاشتراك للاستفادة منها\\.",
    "error_occurred": "⚠️ حدث خطأ غير متوقع\\. تم تسجيل الخطأ وسيتم مراجعته\\. نأسف للإزعاج، يرجى المحاولة مرة أخرى لاحقاً\\.",
    "trial_info": "🆓 لديك *{remaining}* رسائل مجانية متبقية من أصل *{total}* رسالة\\."
}

# خطط الاشتراك (تبقى كما هي)
SUBSCRIPTION_PLANS = {
    "daily": {"days": 1, "price": 4, "price_str": "4$", "emoji": "⏳"},
    "weekly": {"days": 7, "price": 8, "price_str": "8$", "emoji": "📅"},
    "monthly": {"days": 30, "price": 12, "price_str": "12$", "emoji": "🗓️"},
    "premium": {"days": 365, "price": 100, "price_str": "100$", "emoji": "💎"}
}

# حالات المحادثة (تبقى كما هي)
class ConversationStates:
    ADMIN_MENU = 0
    ADMIN_ACTIVATE_PLAN = 1
    ADMIN_ACTIVATE_USER = 2
    ADMIN_DEACTIVATE_USER = 3
    ADMIN_BROADCAST = 4
    ADMIN_BAN_USER = 6
    ADMIN_UNBAN_USER = 7
    ADMIN_CONFIRM_BROADCAST = 8

# ---------------------- وظائف المساعدة المحسنة ----------------------
def is_admin(user_id: int) -> bool:
    return str(user_id) == ADMIN_ID

def is_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if is_admin(user_id): return True
    vip_users = get_vip_users(context)
    if user_id in vip_users:
        # Ensure expiry is datetime object
        expiry = vip_users[user_id].get("expiry")
        if isinstance(expiry, str): # Convert from string if loaded from old format potentially
             try:
                 expiry = datetime.datetime.fromisoformat(expiry)
                 vip_users[user_id]["expiry"] = expiry # Update in context
             except ValueError:
                 logger.error(f"Invalid expiry date format for user {user_id}: {vip_users[user_id].get('expiry')}")
                 return False
        if isinstance(expiry, datetime.datetime):
            # Make sure datetime is timezone-aware or compare consistently
            # Assuming datetime.datetime.now() is naive, compare naively
            return datetime.datetime.now() < expiry
        else:
             logger.warning(f"Expiry for user {user_id} is not a datetime object: {type(expiry)}")
             return False # Treat invalid expiry as expired
    return False

def is_banned(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    return user_id in get_banned_users(context)

def has_free_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    return get_free_trial_users(context).get(user_id, 0) > 0

async def send_typing_action(update: Update):
    """Sends typing action asynchronously."""
    if update.effective_chat:
        try:
            await update.effective_chat.send_action(action="typing")
        except TelegramError as e:
            # Log common, non-critical errors as warnings
            if "chat not found" in str(e).lower() or "bot was blocked" in str(e).lower():
                logger.warning(f"Could not send typing action to chat {update.effective_chat.id}: {e}")
            else:
                logger.error(f"Error sending typing action to chat {update.effective_chat.id}: {e}")
        except Exception as e:
             logger.error(f"Unexpected error sending typing action: {e}", exc_info=True)

# Improved MarkdownV2 Escaper
def escape_markdown_v2(text: str, entity_types: Optional[List[str]] = None) -> str:
    """
    Escapes text for Telegram MarkdownV2 parse mode.
    Handles escaping outside specific entity types like 'code' and 'pre'.

    Args:
        text: The text to escape.
        entity_types: A list of entity types (e.g., ['pre', 'code']) that should not be escaped internally.

    Returns:
        The escaped string.
    """
    if not text:
        return ""

    escape_chars = r'_*[]()~`>#+-=|{}.!' # Characters to escape
    # Simple approach: Escape all special characters.
    # This assumes the input `text` is plain text or has already been processed
    # to separate code blocks which should not be escaped internally.
    escaped_text = re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

    return escaped_text

# Revised clean_response focusing on fixing the Unicode error and simplifying
def clean_response(text: str) -> str:
    """
    Cleans the API response text.
    Removes potentially problematic Unicode characters and formats code blocks.
    Returns text ready for MarkdownV2 (code blocks marked, rest needs escaping).
    """
    if not isinstance(text, str):
        logger.warning(f"clean_response received non-string input: {type(text)}")
        return ""

    # 1. Handle problematic Unicode characters
    try:
        # Remove control characters except tab, newline, carriage return
        cleaned_text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
        # Remove Unicode non-characters and specials often causing issues
        cleaned_text = re.sub(r'[\uFFF0-\uFFFF]', '', cleaned_text)
        # Handle surrogates robustly by encoding/decoding
        # This should fix the incomplete escape error by removing lone surrogates
        cleaned_text = cleaned_text.encode('utf-16', 'surrogatepass').decode('utf-16', 'ignore')

    except Exception as e:
        logger.error(f"Error during Unicode cleaning: {e}", exc_info=True)
        # Fallback: return original text or a basic cleaned version
        cleaned_text = text.encode('utf-8', 'ignore').decode('utf-8')

    # 2. Format code blocks (assuming API uses ```python ... ``` or `...`)
    # This function now only marks code blocks; escaping happens later.
    def replace_code_block(match):
        language = match.group(1) or "" # Handle optional language
        code = match.group(2).strip()
        # Ensure triple backticks are not inside the code itself (add zero-width space)
        code = code.replace('```', '`\u200b``')
        return f"```{language}\n{code}\n```"

    # Handle ``` blocks (case-insensitive language tag)
    cleaned_text = re.sub(r'```(\w*)\n?([\s\S]+?)\n?```', replace_code_block, cleaned_text, flags=re.IGNORECASE)

    # Handle `inline code` - ensure no newlines inside, make non-greedy
    cleaned_text = re.sub(r'`([^`\n]+?)`', r'`\1`', cleaned_text)

    return cleaned_text.strip()


# Asynchronous API Call using aiohttp
# Create a single session for efficiency
aiohttp_session = None

async def get_aiohttp_session() -> aiohttp.ClientSession:
    """Initializes and returns a shared aiohttp ClientSession."""
    global aiohttp_session
    if aiohttp_session is None or aiohttp_session.closed:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        # Consider adding connection pool limits if needed
        connector = aiohttp.TCPConnector(limit_per_host=20) # Example limit
        aiohttp_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        logger.info("Created new aiohttp session.")
    return aiohttp_session

async def close_aiohttp_session():
    """Closes the shared aiohttp session."""
    global aiohttp_session
    if aiohttp_session and not aiohttp_session.closed:
        await aiohttp_session.close()
        aiohttp_session = None
        logger.info("Closed aiohttp session.")

async def call_wormgpt_api_async(prompt: str) -> Tuple[Optional[str], bool]:
    """
    Asynchronously calls the WormGPT API using aiohttp.

    Args:
        prompt: The user's prompt.

    Returns:
        A tuple: (response_text, is_markdown_formatted).
        response_text: The cleaned API response or an error message (plain text).
        is_markdown_formatted: True if the response_text contains ``` or ` blocks.
                               False if it's plain text (like an error message).
    """
    session = await get_aiohttp_session()
    params = {'prompt': prompt, 'model': 'WormGPT'}
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; WORMGPTBot/1.1; +https://t.me/your_bot_username)'} # Updated UA

    try:
        async with session.get(API_URL, params=params, headers=headers) as response:
            response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)

            # Read response body safely, trying UTF-8 then fallback
            try:
                response_text = await response.text(encoding='utf-8')
            except UnicodeDecodeError:
                logger.warning("API response was not UTF-8, trying fallback decoding.")
                response_bytes = await response.read()
                response_text = response_bytes.decode('utf-8', errors='ignore')

            # Clean the response (handles Unicode issues, identifies code blocks)
            cleaned_response = clean_response(response_text)

            if not cleaned_response:
                logger.warning(f"API response for prompt '{prompt[:50]}...' was empty after cleaning.")
                return bot_messages["api_error"] + " (الرد كان فارغاً)", False # Plain text error

            # Check if the cleaned response contains markdown code blocks/inline
            is_markdown = '```' in cleaned_response or '`' in cleaned_response
            return cleaned_response, is_markdown # Return cleaned text and format flag

    except asyncio.TimeoutError:
        logger.error(f"API Timeout Error (aiohttp) for prompt: {prompt[:50]}...")
        return bot_messages["api_timeout"], False # Plain text error
    except aiohttp.ClientResponseError as e:
        logger.error(f"API HTTP Error: {e.status} - {e.message} for prompt: {prompt[:50]}...")
        return bot_messages["api_error"], False # Plain text error
    except aiohttp.ClientError as e: # Catch other client errors like connection issues
        logger.error(f"API Client Connection Error: {e} for prompt: {prompt[:50]}...")
        return bot_messages["api_error"], False # Plain text error
    except Exception as e:
        # Catch potential errors in clean_response as well
        logger.error(f"Unexpected API/Processing Error: {e}", exc_info=True)
        return bot_messages["api_error"], False # Plain text error


# Revised send_formatted_message to handle MarkdownV2 correctly
async def send_formatted_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: Optional[str] = ParseMode.MARKDOWN_V2, # Default to MarkdownV2
    reply_markup=None,
    is_response_formatted: bool = False # Flag if 'text' already has ``` or `
):
    """
    Sends a message, handling MarkdownV2 escaping and splitting.

    Args:
        update: The Telegram Update object.
        context: The Telegram CallbackContext object.
        text: The message text. Can be plain or contain ```/` blocks if is_response_formatted=True.
        parse_mode: Parse mode (MARKDOWN_V2, HTML, or None).
        reply_markup: Inline keyboard markup.
        is_response_formatted: If True, assumes 'text' came from clean_response and needs selective escaping.
                               If False, assumes 'text' is plain and needs full escaping if parse_mode=MARKDOWN_V2.
    """
    MAX_LENGTH = 4096
    if not text or not update.effective_message:
        logger.warning("Attempted to send an empty message or update has no effective_message.")
        return

    final_text = text
    actual_parse_mode = parse_mode

    # --- MarkdownV2 Handling ---
    if parse_mode == ParseMode.MARKDOWN_V2:
        if is_response_formatted:
            # Text has ``` or ` blocks. Escape *around* them.
            parts = re.split(r'(```(?:\w*\n)?(?:[\s\S]+?\n)?```|`[^`\n]+?`)', text)
            escaped_parts = []
            for i, part in enumerate(parts):
                if not part: continue # Skip empty parts from split
                if i % 2 == 1: # Code block or inline code - leave as is
                    escaped_parts.append(part)
                else: # Plain text part - escape it
                    escaped_parts.append(escape_markdown_v2(part))
            final_text = "".join(escaped_parts)
        else:
            # Text is plain, escape the whole thing
            final_text = escape_markdown_v2(text)

        # Basic validation: Check for unescaped special characters outside code blocks
        # This is a simple check and might not catch all edge cases
        temp_text = re.sub(r'```(?:\w*\n)?(?:[\s\S]+?\n)?```|`[^`\n]+?`', '', final_text) # Remove code blocks
        if re.search(r'(?<!\\)[_*~>#+\-=|{}.!]', temp_text): # Look for unescaped chars
             logger.warning(f"Potential unescaped MarkdownV2 characters detected in: {temp_text[:100]}...")
             # Consider falling back to plain text or trying to re-escape

    # --- Sending Logic ---
    try:
        if len(final_text) <= MAX_LENGTH:
            await update.effective_message.reply_text(
                final_text,
                parse_mode=actual_parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        else:
            # Splitting logic - Improved slightly to handle edge cases
            logger.info(f"Message length ({len(final_text)}) exceeds limit, splitting...")
            parts = []
            start_index = 0
            while start_index < len(final_text):
                # Find a suitable split point (newline preferred) near MAX_LENGTH
                split_index = -1
                search_end = min(start_index + MAX_LENGTH, len(final_text))
                # Try to find last newline within the chunk
                newline_index = final_text.rfind('\n', start_index, search_end)
                if newline_index != -1 and newline_index > start_index:
                    split_index = newline_index + 1 # Split after newline
                else:
                    # No newline found, or it's at the very beginning, force split at MAX_LENGTH
                    split_index = search_end
                
                # Ensure we don't split inside a code block marker ` or ```
                # This is still basic and might fail for complex cases
                chunk = final_text[start_index:split_index]
                # Avoid splitting ``` or `
                if chunk.endswith('`') and not chunk.endswith('``') and split_index < len(final_text):
                     # Avoid splitting single backtick
                     split_index -= 1
                elif chunk.endswith('``') and split_index < len(final_text):
                     # Avoid splitting double backtick
                     split_index -= 2
                elif chunk.endswith('```') and split_index < len(final_text):
                     # Avoid splitting triple backtick start/end
                     split_index -=3
                
                # Ensure split_index is valid
                split_index = max(start_index + 1, split_index) # Must advance at least 1 char
                split_index = min(split_index, len(final_text)) # Cannot exceed total length

                parts.append(final_text[start_index:split_index])
                start_index = split_index

            # Send the parts
            for i, part in enumerate(parts):
                if part.strip(): # Don't send empty parts
                    current_reply_markup = reply_markup if i == len(parts) - 1 else None
                    await update.effective_message.reply_text(
                        part,
                        parse_mode=actual_parse_mode,
                        reply_markup=current_reply_markup,
                        disable_web_page_preview=True
                    )
                    if i < len(parts) - 1:
                        await asyncio.sleep(0.8) # Slightly longer delay for split messages

    except TelegramError as e:
        logger.error(f"Error sending formatted message ({actual_parse_mode}): {e}")
        logger.error(f"Failed message content (first 500 chars): {final_text[:500]}")
        try:
            error_msg_plain = bot_messages["error_occurred"]
            if "Can't parse entities" in str(e):
                 error_msg_plain += " (خطأ في تنسيق الرسالة)"
            elif "message is too long" in str(e).lower():
                 error_msg_plain += " (الرسالة أطول من اللازم)"
            # Add more specific error details if possible
            if hasattr(e, 'message'):
                 error_msg_plain += f" - {e.message[:100]}"

            await update.effective_message.reply_text(
                 error_msg_plain,
                 parse_mode=None,
                 reply_markup=reply_markup if len(final_text) <= MAX_LENGTH else None
            )
        except Exception as inner_e:
            logger.error(f"Failed to send plain text error fallback: {inner_e}")
    except Exception as e:
        logger.error(f"Unexpected error in send_formatted_message: {e}", exc_info=True)

# Async get_user_info (using context.bot)
async def get_user_info(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Dict[str, Any]:
    """Gets user info asynchronously and escapes it for MarkdownV2."""
    try:
        user = await context.bot.get_chat(user_id)
        username = f"@{user.username}" if user.username else "لا يوجد معرف"
        name = user.full_name or f"User_{user_id}"

        escaped_name = escape_markdown_v2(name)
        escaped_username = escape_markdown_v2(username) if username != "لا يوجد معرف" else username

        return {"username": escaped_username, "name": escaped_name, "id": user_id, "raw_name": name, "raw_username": user.username}
    except TelegramError as e:
        logger.warning(f"Could not get info for user {user_id}: {e}")
        escaped_id_str = escape_markdown_v2(str(user_id))
        return {"username": "غير معروف", "name": f"User\\_{escaped_id_str}", "id": user_id, "raw_name": f"User_{user_id}", "raw_username": None}
    except Exception as e:
        logger.error(f"Unexpected error getting info for user {user_id}: {e}", exc_info=True)
        return {"username": "خطأ", "name": "خطأ", "id": user_id, "raw_name": "Error", "raw_username": None}

# Error handler (already async, ensure plain text reply)
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            error_msg_plain = bot_messages["error_occurred"]
            await update.effective_message.reply_text(error_msg_plain, parse_mode=None)
        except Exception as e:
            logger.error(f"Failed to send error message to user after an exception: {e}")

# --- End of Part 1 ---


# --- Start of Part 2 ---

# ---------------------- معالجات الأوامر الأساسية (متابعة) ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command, welcoming users and showing options."""
    user = update.effective_user
    user_id = user.id
    now = datetime.datetime.now()

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    await send_typing_action(update)

    user_stats = get_user_stats(context, user_id)
    is_new_user = False
    if user_stats.get("join_date") is None:
        is_new_user = True
        user_stats["join_date"] = now
        # Grant free trial if not VIP and no trial record exists
        free_trial_users = get_free_trial_users(context)
        if user_id not in free_trial_users and not is_vip(context, user_id):
            free_trial_users[user_id] = MAX_FREE_MESSAGES
            logger.info(f"Granted {MAX_FREE_MESSAGES} free messages to new user {user_id}")

    user_stats["last_active"] = now

    # Schedule persistence update
    await update_persistence(context)

    try:
        if is_vip(context, user_id):
            vip_users = get_vip_users(context)
            vip_info = vip_users.get(user_id, {})
            expiry = vip_info.get("expiry")
            plan = vip_info.get("plan", "غير محدد")
            messages = user_stats.get("messages_sent", 0)

            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M")) if isinstance(expiry, datetime.datetime) else "غير محدد"
            plan_str = escape_markdown_v2(plan.capitalize())
            user_first_name = escape_markdown_v2(user.first_name or f"User {user_id}")

            welcome_vip_msg = f"🎉 أهلاً بعودتك يا `{user_first_name}`\\! 🎉\\n\\nاشتراكك في *WORMGPT* \\({plan_str}\\) لا يزال فعالاً حتى `{expiry_str}`\\. استمتع بالذكاء الاصطناعي *بدون قيود*\\.\\n\\n" + bot_messages['features'].format(max_free=MAX_FREE_MESSAGES)
            await send_formatted_message(update, context, welcome_vip_msg, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

        else:
            # Show subscription options and free trial info for regular users
            plan_details_list = []
            for plan_name, plan_info in SUBSCRIPTION_PLANS.items():
                plan_details_list.append(
f"{plan_info['emoji']} *{escape_markdown_v2(plan_name.capitalize())}*: {escape_markdown_v2(plan_info['price_str'])}) \\({plan_info['days']} يوم\\)"                )
            plan_details = "\\n".join(plan_details_list)

            buttons = []
            free_trial_users = get_free_trial_users(context)
            remaining_free = free_trial_users.get(user_id, 0)
            if remaining_free > 0:
                buttons.append([InlineKeyboardButton(f"🆓 تجربة مجانية ({remaining_free} متبقية)", callback_data="free_trial_info")])
            # Show request button only if they have no trial record AND are not VIP
            elif user_id not in free_trial_users and not is_vip(context, user_id):
                 buttons.append([InlineKeyboardButton("🎁 طلب تجربة مجانية", callback_data="request_free_trial")])

            plan_buttons = [InlineKeyboardButton(
                                f"{plan_info['emoji']} {escape_markdown_v2(plan_name.capitalize())} ({escape_markdown_v2(plan_info['price_str'])})",
                                callback_data=f"plan_{plan_name}"
                            ) for plan_name, plan_info in SUBSCRIPTION_PLANS.items()]
            # Arrange plan buttons in rows of 2
            buttons.extend([plan_buttons[i:i+2] for i in range(0, len(plan_buttons), 2)])

            buttons.append([InlineKeyboardButton("📞 راسل المطور", url="https://t.me/OZOOZOZ")])
            buttons.append([InlineKeyboardButton("🌟 عرض الميزات", callback_data="show_features")])

            markup = InlineKeyboardMarkup(buttons)
            welcome_msg = bot_messages["welcome"] + "\\n\\n" + bot_messages["plan_choices"].format(plan_details=plan_details)
            await send_formatted_message(update, context, welcome_msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    except TelegramError as e:
        logger.error(f"TelegramError in start command for user {user_id}: {e}")
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
    except Exception as e:
        logger.error(f"Unexpected error in start command for user {user_id}: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)

async def show_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /features command or callback query."""
    query = update.callback_query
    message = update.effective_message
    try:
        features_text = bot_messages["features"].format(max_free=MAX_FREE_MESSAGES)
        if query:
            await query.answer()
            # Use send_formatted_message to handle potential length issues
            await query.edit_message_text("⏳ جارٍ تحميل الميزات\\.\\.\\.", parse_mode=None)
            await send_formatted_message(update, context, features_text, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        elif message:
             await send_formatted_message(update, context, features_text, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    except TelegramError as e:
        if "message is not modified" in str(e).lower():
            logger.info("Message not modified (show_features)")
            if query: await query.answer("الميزات معروضة بالفعل.") # Provide feedback
        else:
            logger.error(f"Error handling show_features: {e}")
            if query: await query.answer("حدث خطأ.")
    except Exception as e:
        logger.error(f"Unexpected error in show_features: {e}", exc_info=True)
        if query: await query.answer("حدث خطأ.")

async def subscription_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user's current subscription or trial status."""
    user_id = update.effective_user.id

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    await send_typing_action(update)

    try:
        info_message = ""
        if is_vip(context, user_id):
            vip_users = get_vip_users(context)
            vip_info = vip_users.get(user_id, {})
            expiry = vip_info.get("expiry")
            plan = vip_info.get("plan", "غير محدد")
            user_stats = get_user_stats(context, user_id)
            messages = user_stats.get("messages_sent", 0)

            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M")) if isinstance(expiry, datetime.datetime) else "غير محدد"
            plan_str = escape_markdown_v2(plan.capitalize())
            remaining_str = ""
            if isinstance(expiry, datetime.datetime):
                remaining = expiry - datetime.datetime.now()
                if remaining.total_seconds() > 0:
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    remaining_str = f"\\n🕒 المتبقي: *{days}* يوم و *{hours}* ساعة"
                else:
                    remaining_str = "\\n⏳ الاشتراك منتهي الصلاحية\\."

            info_message = bot_messages["subscription_info"].format(
                plan=plan_str,
                expiry=f"`{expiry_str}`",
                messages=messages
            ) + remaining_str

        else:
            free_trial_users = get_free_trial_users(context)
            remaining = free_trial_users.get(user_id)
            if remaining is not None and remaining > 0:
                info_message = bot_messages["trial_info"].format(
                    remaining=remaining,
                    total=MAX_FREE_MESSAGES
                )
            elif user_id in free_trial_users and remaining <= 0:
                 info_message = TRIAL_EXHAUSTED
            else:
                info_message = escape_markdown_v2("⚠️ ليس لديك اشتراك فعال أو رسائل مجانية حالياً.")

        await send_formatted_message(update, context, info_message, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    except Exception as e:
        logger.error(f"Error in subscription_info_command: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays admin help message."""
    if not is_admin(update.effective_user.id):
        await send_formatted_message(update, context, bot_messages["admin_only"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return
    await send_formatted_message(update, context, bot_messages["admin_help"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

# ---------------------- معالجة الرسائل المحسنة ----------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles regular text messages from users."""
    user = update.effective_user
    user_id = user.id
    message_text = update.message.text

    if is_banned(context, user_id):
        logger.info(f"Ignoring message from banned user {user_id}")
        return

    await send_typing_action(update)

    now = datetime.datetime.now()
    user_stats = get_user_stats(context, user_id)
    # Initialize join date if missing (e.g., user interacted before persistence)
    if user_stats.get("join_date") is None:
        user_stats["join_date"] = now
        logger.warning(f"User {user_id} sent message without /start, setting join_date.")
        # Grant trial if needed (redundant with /start but safe)
        free_trial_users = get_free_trial_users(context)
        if user_id not in free_trial_users and not is_vip(context, user_id):
            free_trial_users[user_id] = MAX_FREE_MESSAGES
            logger.info(f"Granted free trial to user {user_id} via handle_message.")

    user_stats["messages_sent"] = user_stats.get("messages_sent", 0) + 1
    user_stats["last_active"] = now

    can_use_bot = False
    notify_trial_usage = False
    free_trial_users = get_free_trial_users(context)

    if is_vip(context, user_id):
        can_use_bot = True
    elif user_id in free_trial_users:
        remaining = free_trial_users[user_id]
        if remaining > 0:
            can_use_bot = True
            free_trial_users[user_id] -= 1
            logger.info(f"User {user_id} used a free message. Remaining: {free_trial_users[user_id]}")
            # Notify when trial is exhausted
            if free_trial_users[user_id] == 0:
                notify_trial_usage = True # Flag to send notification after API response
        else:
            # User has a trial record but it's 0 - shouldn't happen if cleaned up, but handle defensively
            logger.warning(f"User {user_id} has 0 free messages in record. Should have been handled earlier.")
            await send_formatted_message(update, context, TRIAL_EXHAUSTED, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
            await update_persistence(context) # Save stats update
            return

    if not can_use_bot:
        await send_formatted_message(update, context, bot_messages["not_vip"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        await update_persistence(context) # Save stats update
        return

    # Call the asynchronous API function
    api_response_text, is_markdown = await call_wormgpt_api_async(message_text)

    # Send the API response (or error message from the API call)
    await send_formatted_message(
        update, context,
        api_response_text,
        parse_mode=ParseMode.MARKDOWN_V2 if is_markdown else None,
        is_response_formatted=is_markdown
    )

    # Send trial exhausted notification *after* the main response
    if notify_trial_usage:
        await asyncio.sleep(0.5) # Small delay
        await send_formatted_message(update, context, TRIAL_EXHAUSTED, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    # Schedule persistence update after processing the message
    await update_persistence(context)

# ---------------------- لوحة تحكم الأدمن (محادثة - محسنة) ----------------------
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main admin menu."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_formatted_message(update, context, bot_messages["admin_only"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return ConversationHandler.END

    # Buttons are generated quickly, no blocking operations here
    buttons = [
        [InlineKeyboardButton("🔑 تفعيل اشتراك", callback_data="admin_activate"),
         InlineKeyboardButton("✖️ إلغاء اشتراك", callback_data="admin_deactivate")],
        [InlineKeyboardButton("📋 عرض المشتركين", callback_data="admin_list_vips"),
         InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats")],
        [InlineKeyboardButton("⛔ حظر مستخدم", callback_data="admin_ban"),
         InlineKeyboardButton("✅ إلغاء حظر", callback_data="admin_unban")],
        [InlineKeyboardButton("📢 إرسال إعلان", callback_data="admin_broadcast")]
    ]
    markup = InlineKeyboardMarkup(buttons)

    # Check if called via command or callback query
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.edit_message_text(bot_messages["admin_greeting"], reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
        except TelegramError as e:
            if "message is not modified" in str(e).lower():
                logger.info("Admin menu not modified.")
            else: raise e # Re-raise other errors
    else:
        await send_formatted_message(update, context, bot_messages["admin_greeting"], reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    return ConversationStates.ADMIN_MENU

async def admin_activate_plan_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows plan selection for activation."""
    query = update.callback_query
    await query.answer()

    buttons = [
        [InlineKeyboardButton(f"{plan_info['emoji']} {escape_markdown_v2(plan_name.capitalize())} ({plan_info['days']} يوم)", callback_data=f"admin_plan_{plan_name}")]
        for plan_name, plan_info in SUBSCRIPTION_PLANS.items()
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")])
    markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(escape_markdown_v2("📝 اختر نوع الاشتراك لتفعيله:"), reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_ACTIVATE_PLAN

async def admin_get_user_for_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for user ID/username to activate."""
    query = update.callback_query
    await query.answer()

    plan = query.data.split("_")[2]
    context.user_data["selected_plan"] = plan # Store plan in user_data (specific to this admin's interaction)

    plan_str = escape_markdown_v2(plan.capitalize())
    message_text = f"📩 أرسل الآن *اسم المستخدم* \\(مثل `@username`\\) أو *الـ ID الرقمي* للشخص الذي تريد تفعيل خطة `{plan_str}` له:"
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_ACTIVATE_USER

async def admin_activate_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activates the selected plan for the specified user."""
    user_input = update.message.text.strip()
    admin_id = update.effective_user.id
    plan = context.user_data.get("selected_plan")

    if not plan:
        await send_formatted_message(update, context, escape_markdown_v2("⚠️ خطأ: لم يتم تحديد خطة. يرجى البدء من جديد /admin"), parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    target_user_id = None
    target_user_info = None

    try:
        # Use async get_user_info which handles lookup and escaping
        if user_input.startswith("@"):
            try:
                # Attempt to resolve username via get_chat
                user = await context.bot.get_chat(user_input)
                target_user_id = user.id
                target_user_info = await get_user_info(context, target_user_id)
            except TelegramError as e:
                logger.warning(f"Could not find user by username {user_input}: {e}")
                await send_formatted_message(update, context, bot_messages['invalid_user'] + escape_markdown_v2(f" \\(لم يتم العثور على {user_input}\\) "), parse_mode=ParseMode.MARKDOWN_V2)
                return ConversationStates.ADMIN_ACTIVATE_USER # Ask again
        else:
            try:
                target_user_id = int(user_input)
                target_user_info = await get_user_info(context, target_user_id)
                # Check if get_user_info actually found the user
                if target_user_info['username'] == "غير معروف" and target_user_info['name'] == f"User\\_{escape_markdown_v2(str(target_user_id))}":
                     logger.warning(f"Could not get valid info for user ID {target_user_id}, might not exist or bot blocked.")
                     # Proceed cautiously, but warn admin
                     await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ لم يتم العثور على معلومات تفصيلية للمستخدم ID `{target_user_id}`. قد لا يكون موجوداً أو قام بحظر البوت. سيتم التفعيل على أي حال."), parse_mode=ParseMode.MARKDOWN_V2)

            except ValueError:
                await send_formatted_message(update, context, bot_messages["invalid_user"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
                return ConversationStates.ADMIN_ACTIVATE_USER # Ask again
            except TelegramError as e: # Catch potential get_chat errors by ID too
                 logger.warning(f"Error fetching user info for ID {target_user_id}: {e}")
                 await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم ID `{target_user_id}`. يرجى المحاولة مرة أخرى."), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationStates.ADMIN_ACTIVATE_USER

        if target_user_id and target_user_info:
            days = SUBSCRIPTION_PLANS[plan]["days"]
            expiry = datetime.datetime.now() + datetime.timedelta(days=days)

            vip_users = get_vip_users(context)
            vip_users[target_user_id] = {"expiry": expiry, "plan": plan}

            # Remove from trial and pending payments if exists
            free_trial_users = get_free_trial_users(context)
            if target_user_id in free_trial_users: del free_trial_users[target_user_id]
            pending_payments = get_pending_payments(context)
            if target_user_id in pending_payments: del pending_payments[target_user_id]

            await update_persistence(context) # Save changes

            # Use already escaped info from get_user_info
            user_display = f"{target_user_info['name']} \\({target_user_info['username']}, ID: `{target_user_id}`\\)"
            plan_str = escape_markdown_v2(plan.capitalize())
            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M"))

            # Send confirmation to admin
            admin_confirmation = (
                bot_messages["user_activated"].format(user=user_display) +
                f"\\n📦 الخطة: *{plan_str}*\\n⏳ المدة: *{days}* يوم\\n📅 تنتهي في: `{expiry_str}`"
            )
            await send_formatted_message(update, context, admin_confirmation, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

            # Send notification to the activated user
            try:
                user_stats = get_user_stats(context, target_user_id)
                user_messages = user_stats.get("messages_sent", 0)
                user_notification = (
                    bot_messages["vip_active"] + "\\n\\n" +
                    bot_messages["subscription_info"].format(
                        plan=plan_str,
                        expiry=f"`{expiry_str}`",
                        messages=user_messages
                    )
                )
                # Use send_formatted_message for consistency and error handling
                # Need to create a dummy Update object or use context.bot.send_message carefully
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=user_notification,
                    parse_mode=ParseMode.MARKDOWN_V2
                    # Note: send_formatted_message needs an Update object, so using bot.send_message directly
                    # Ensure user_notification is correctly escaped (it should be)
                )
            except TelegramError as e:
                logger.error(f"Error sending activation message to user {target_user_id}: {e}")
                error_detail = escape_markdown_v2(str(e))
                await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ تم تفعيل الاشتراك، ولكن فشل إرسال رسالة التأكيد للمستخدم {target_user_id}. الخطأ: ") + f"`{error_detail}`", parse_mode=ParseMode.MARKDOWN_V2)

            context.user_data.pop("selected_plan", None)
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Admin activation error: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
        context.user_data.pop("selected_plan", None)
        return ConversationHandler.END

async def admin_get_user_for_deactivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for user ID/username to deactivate."""
    query = update.callback_query
    await query.answer()
    message_text = escape_markdown_v2("📩 أرسل اسم المستخدم (مثل @username) أو الـ ID الرقمي للشخص الذي تريد إلغاء اشتراكه:")
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_DEACTIVATE_USER

async def admin_deactivate_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivates the subscription for the specified user."""
    user_input = update.message.text.strip()
    admin_id = update.effective_user.id
    target_user_id = None
    target_user_info = None

    try:
        # Use async get_user_info
        if user_input.startswith("@"):
            try:
                user = await context.bot.get_chat(user_input)
                target_user_id = user.id
                target_user_info = await get_user_info(context, target_user_id)
            except TelegramError:
                await send_formatted_message(update, context, bot_messages['invalid_user'] + escape_markdown_v2(f" \\(لم يتم العثور على {user_input}\\) "), parse_mode=ParseMode.MARKDOWN_V2)
                return ConversationStates.ADMIN_DEACTIVATE_USER
        else:
            try:
                target_user_id = int(user_input)
                target_user_info = await get_user_info(context, target_user_id)
                # Check if user info is valid
                if target_user_info['username'] == "غير معروف":
                     logger.warning(f"Could not get valid info for user ID {target_user_id} during deactivation.")
            except ValueError:
                await send_formatted_message(update, context, bot_messages["invalid_user"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
                return ConversationStates.ADMIN_DEACTIVATE_USER
            except TelegramError as e:
                 logger.warning(f"Error fetching user info for ID {target_user_id} during deactivation: {e}")
                 await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم ID `{target_user_id}`. يرجى المحاولة مرة أخرى."), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationStates.ADMIN_DEACTIVATE_USER

        if target_user_id and target_user_info:
            vip_users = get_vip_users(context)
            if target_user_id in vip_users:
                del vip_users[target_user_id]
                await update_persistence(context) # Save changes

                user_display = f"{target_user_info['name']} \\({target_user_info['username']}, ID: `{target_user_id}`\\)"
                await send_formatted_message(update, context, bot_messages["user_deactivated"].format(user=user_display), parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

                # Send notification to the deactivated user
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=bot_messages["subscription_expired"],
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except TelegramError as e:
                    logger.error(f"Error sending deactivation message to user {target_user_id}: {e}")
                    error_detail = escape_markdown_v2(str(e))
                    await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ تم إلغاء الاشتراك، ولكن فشل إرسال رسالة للمستخدم {target_user_id}. الخطأ: ") + f"`{error_detail}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await send_formatted_message(update, context, escape_markdown_v2("⚠️ هذا المستخدم ليس لديه اشتراك فعال لإلغائه."), parse_mode=ParseMode.MARKDOWN_V2)

            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Admin deactivation error: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
        return ConversationHandler.END

async def admin_list_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists active VIP users."""
    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_text("⏳ جارٍ تحميل قائمة المشتركين\\.\\.\\.", parse_mode=None)

        vip_users = get_vip_users(context)
        now = datetime.datetime.now()
        active_vips_data = []

        # Fetch user info concurrently for speed
        user_ids_to_fetch = [uid for uid, data in vip_users.items() if isinstance(data.get("expiry"), datetime.datetime) and data["expiry"] > now]
        user_info_tasks = [get_user_info(context, uid) for uid in user_ids_to_fetch]
        user_infos = await asyncio.gather(*user_info_tasks)

        user_info_map = {info["id"]: info for info in user_infos}

        for user_id in user_ids_to_fetch:
            data = vip_users[user_id]
            user_info = user_info_map.get(user_id)
            if user_info:
                active_vips_data.append((user_id, data, user_info))

        if not active_vips_data:
            await query.edit_message_text(bot_messages["no_vips"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
            return

        # Sort by expiry date
        sorted_vips = sorted(active_vips_data, key=lambda item: item[1]["expiry"])

        vip_list_items = []
        for user_id, data, user_info in sorted_vips:
            expiry = data["expiry"]
            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M"))
            plan_str = escape_markdown_v2(data.get("plan", "N/A").capitalize())
            remaining_days = (expiry - now).days
            name = user_info["name"] # Already escaped
            username = user_info["username"] # Already escaped

            vip_list_items.append(
                f"👤 *{name}* \\({username}, ID: `{user_id}`\\)\\n"
                f"   📦 {plan_str} \\| ⏳ *{remaining_days} يوم* متبقي \\| 📅 تنتهي: `{expiry_str}`"
            )

        full_message = bot_messages["vip_list"].format(list="\\n\\n".join(vip_list_items))

        # Use send_formatted_message to handle potential length and formatting
        # Need to use query.message as the 'update' for send_formatted_message
        dummy_update = Update(update.update_id, message=query.message)
        await send_formatted_message(dummy_update, context, full_message, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        # Delete the temporary 
    except Exception as e:
        logger.error(f"Admin list VIPs error: {e}", exc_info=True)
        # Use query.edit_message_text for callback queries
        try:
            await query.edit_message_text(bot_messages["error_occurred"], parse_mode=None)
        except Exception as inner_e:
            logger.error(f"Failed to send error message in admin_list_vips: {inner_e}")

# -*- coding: utf-8 -*-
import logging
import datetime
# import requests # Remove synchronous requests
import aiohttp # Add asynchronous http client
import re
import json
import os
import asyncio
import time # For debounced saving
from typing import Dict, Any, List, Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
    JobQueue,
    PicklePersistence # Using persistence for data
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
# from telegram.helpers import escape_markdown # Use custom escape function

# ---------------------- إعدادات البوت ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- تحذير: لا تضع التوكن هنا مباشرة في الإنتاج ---
# --- يفضل استخدام متغيرات البيئة أو ملف إعدادات ---
# --- تم إخفاء التوكن --- 
# TODO: Replace with your actual token or load from environment variables/config file
BOT_TOKEN = "7780902956:AAHY4DyX8nIcAjrlyCwd3z74kcoFFE4eHbc" # Replace with your actual token or load from env
ADMIN_ID = "7091341079"  # معرف الأدمن
API_URL = "https://dev-apis-xyz.pantheonsite.io/wp-content/apis/freeAi.php"
API_TIMEOUT = 30 # Adjusted timeout
# DATA_FILE = "user_data.json" # Replaced by persistence
PERSISTENCE_FILE = "bot_persistence.pkl"

# إعدادات الرسائل التجريبية
MAX_FREE_MESSAGES = 3
FREE_TRIAL_MESSAGE = "🆓 لديك *{remaining}* رسائل مجانية متبقية\\. بعد انتهائها، يلزمك الاشتراك\\."
TRIAL_EXHAUSTED = "⏳ لقد استنفدت جميع رسائلك المجانية\\. للحصول على المزيد، يرجى الاشتراك الآن لمواصلة الاستخدام\\."

# إعدادات الحفظ المؤجل (Debounced Saving)
SAVE_DEBOUNCE_SECONDS = 5 # Save data at most every 5 seconds
last_save_time = 0
save_scheduled = False
save_lock = asyncio.Lock()

# ---------------------- هياكل البيانات (تدار الآن بواسطة Persistence) ----------------------
# The UserData class is no longer needed explicitly here if using PicklePersistence.
# Data will be stored in context.bot_data and context.user_data.
# We'll define helper functions to access this data safely.

async def update_persistence(context: ContextTypes.DEFAULT_TYPE):
    """Schedules a debounced save of persistence data."""
    global last_save_time, save_scheduled
    current_time = time.time()
    async with save_lock:
        if not save_scheduled and (current_time - last_save_time > SAVE_DEBOUNCE_SECONDS):
            # Save immediately if debounce time has passed
            if context.application.persistence:
                 await context.application.persistence.flush()
                 last_save_time = current_time
                 logger.info("Persistence data flushed immediately.")
            else:
                 logger.warning("Persistence object not found, cannot flush data.")

        elif not save_scheduled:
            # Schedule a save after the debounce period
            save_scheduled = True
            # Use a unique name to prevent conflicts if called rapidly
            job_name = f"debounce_save_{current_time}"
            context.job_queue.run_once(do_save_persistence, SAVE_DEBOUNCE_SECONDS, name=job_name, data=current_time)
            logger.debug(f"Persistence save scheduled with job name {job_name}.")

async def do_save_persistence(context: ContextTypes.DEFAULT_TYPE):
    """Performs the actual save operation."""
    global last_save_time, save_scheduled
    # Check if this job is the latest scheduled one
    job_time = context.job.data
    async with save_lock:
        # Only save if this is the most recently scheduled job or if no other job is scheduled
        is_latest_job = not context.job_queue.get_jobs_by_name(f"debounce_save_{job_time + 0.1}") # Check for slightly later jobs

        if save_scheduled: # and is_latest_job: # Ensure we only save if still scheduled
            if context.application.persistence:
                await context.application.persistence.flush()
                last_save_time = time.time()
                save_scheduled = False # Reset schedule flag *after* saving
                logger.info(f"Persistence data flushed by job {context.job.name}.")
            else:
                logger.warning("Persistence object not found in scheduled save, cannot flush data.")
                save_scheduled = False # Reset flag even on error
        else:
             logger.debug(f"Skipping save for job {context.job.name}, already saved or cancelled.")


# Helper functions to manage user data within context
def get_user_stats(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Dict[str, Any]:
    # Ensure user_data exists for the user
    if "user_data" not in context.user_data:
         context.user_data[user_id] = {}
    if user_id not in context.user_data:
        context.user_data[user_id] = {}

    # Use application.user_data for user-specific data
    return context.user_data[user_id].setdefault("stats", {"messages_sent": 0, "last_active": None, "join_date": None})

def get_vip_users(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, Dict[str, Any]]:
    return context.bot_data.setdefault("vip_users", {})

def get_pending_payments(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, Dict[str, Any]]:
    return context.bot_data.setdefault("pending_payments", {})

def get_banned_users(context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    return context.bot_data.setdefault("banned_users", [])

def get_free_trial_users(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, int]:
    return context.bot_data.setdefault("free_trial_users", {})

# Initialize bot_data structure if empty
def initialize_bot_data(application: Application):
     logger.info("Initializing bot_data structure...")
     application.bot_data.setdefault("vip_users", {})
     application.bot_data.setdefault("pending_payments", {})
     application.bot_data.setdefault("banned_users", [])
     application.bot_data.setdefault("free_trial_users", {})
     logger.info("Bot_data structure initialized.")


# رسائل البوت المحسنة (مع تنسيق Markdown V2)
# تم تعديل بعض الرسائل وتصحيح الـ escape
bot_messages = {
    "welcome": "✨ أهلاً بك في *WORMGPT* \\- بوت الذكاء الاصطناعي المتطور *بدون قيود*\\! ✨\\n\\nأنا هنا لمساعدتك في الإجابة على استفساراتك، كتابة النصوص، توليد الأفكار، والمزيد\\.\\n\\n*ابدأ الآن* بالاختيار من القوائم أدناه أو أرسل رسالتك مباشرة\\.",
    "not_vip": "🔒 عذراً، اشتراكك غير مفعل أو انتهت صلاحيته\\. يمكنك استخدام *الرسائل المجانية* المتبقية \\(إن وجدت\\) أو *الاشتراك* في إحدى خططنا المميزة للاستخدام *غير المحدود*\\.",
    "vip_active": "🎉 تهانينا\\! تم تفعيل اشتراكك بنجاح في *WORMGPT*\\. يمكنك الآن الاستمتاع بتجربة الذكاء الاصطناعي *بدون قيود*\\.",
    "subscription_expired": "⏳ نأسف لإبلاغك بانتهاء صلاحية اشتراكك\\. لتجديد الوصول *غير المحدود*، يرجى اختيار إحدى خطط التجديد\\.",
    "admin_greeting": "👑 أهلاً بك أيها المدير في لوحة تحكم *WORMGPT*\\!",
    "broadcast_template": "📢 *رسالة من الإدارة* 📢\\n\\n{message}", # Ensure {message} is properly escaped before sending if needed
    "features": "🌟 *ميزات WORMGPT* 🌟\\n\\n🤖 *ذكاء اصطناعي متطور بدون قيود*\\.\\n💻 دعم *الأكواد البرمجية* بتنسيق Markdown \\(```\\) و \\(`\\`\\)\\.\\n✨ واجهة مستخدم *سهلة وجذابة*\\.\\n📊 *إحصائيات* مفصلة للمشتركين والأدمن\\.\\n⚡️ *سرعة استجابة* فائقة \\(محسّنة\\)\\.\\n💾 *حفظ بيانات* المستخدمين بشكل دائم\\.\\n💳 نظام *اشتراكات* مرن ومتكامل\\.\\n📞 *دعم فني* سريع عبر المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ)\\.\\n🆓 *تجربة مجانية* \\({max_free} رسائل\\) للمستخدمين الجدد\\.", # Placeholder for max_free
    "user_banned": "⛔ تم حظرك من استخدام البوت\\. إذا كنت تعتقد أن هذا خطأ، يرجى التواصل مع المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ)\\.",
    "admin_help": "🛠 *أوامر الأدمن* 🛠\\n\\n`/admin` \\- عرض لوحة التحكم الرئيسية\\.\\n`/stats` \\- عرض إحصائيات البوت التفصيلية\\.\\n`/ban` \\- بدء محادثة حظر مستخدم\\.\\n`/unban` \\- بدء محادثة إلغاء حظر مستخدم\\.\\n`/broadcast` \\- بدء محادثة إرسال إعلان للمشتركين\\.\\n`/adminhelp` \\- عرض هذه الرسالة المساعدة\\.",
    "api_error": "⚠️ عذراً، حدث خطأ أثناء التواصل مع خادم الذكاء الاصطناعي\\. يرجى المحاولة مرة أخرى بعد قليل\\.",
    "api_timeout": "⏳ استغرقت معالجة طلبك وقتاً أطول من المعتاد \\(تجاوز المهلة\\)\\. قد يكون هناك ضغط على الخادم\\. يرجى المحاولة مجدداً\\.",
    "payment_pending": "⏳ طلب اشتراكك لا يزال معلقاً أو انتهت صلاحيته \\(أكثر من يومين\\)\\. يرجى التواصل مع المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ) لتأكيد الدفع أو إعادة الطلب\\.",
    "payment_success": "✅ تم استلام طلب اشتراكك بنجاح\\! لإتمام العملية وتفعيل الاشتراك، يرجى التواصل مع المطور \\[@OZOOZOZ](https://t.me/OZOOZOZ)\\.",
    "user_unbanned": "🎉 تم إلغاء حظرك بنجاح\\! يمكنك الآن استخدام *WORMGPT* مرة أخرى\\.",
    "subscription_info": "📊 *معلومات اشتراكك* 📊\\n\\n📦 الخطة: *{plan}*\\n⏳ تنتهي في: `{expiry}`\\n✉️ الرسائل المرسلة: *{messages}* رسالة",
    "plan_choices": "🛒 *اختر خطة الاشتراك التي تناسبك* 🛒\\n\\n{plan_details}",
    "confirm_plan": "🛒 *تأكيد طلب الاشتراك* 🛒\\n\\n📦 الخطة المختارة: *{plan}*\\n💰 السعر: *{price}*\\n⏳ المدة: *{days} يوم*\\n\\nهل أنت متأكد من رغبتك في المتابعة؟",
    "admin_stats": "📊 *إحصائيات WORMGPT* 📊\\n\\n👥 إجمالي المستخدمين \\(تفاعلوا\\): *{users}*\\n⭐ المشتركين النشطين: *{vips}*\\n✉️ إجمالي الرسائل \\(المسجلة\\): *{messages}*\\n⛔ المحظورين: *{banned}*\\n🆓 مستخدمين التجربة النشطين: *{trial_users}*\\n⏳ طلبات دفع معلقة: *{pending_payments}*",
    "vip_list": "📋 *قائمة المشتركين النشطين* \\(VIP\\) 📋\\n\\n{list}",
    "no_vips": "ℹ️ لا يوجد مشتركين نشطين حالياً\\.",
    "user_activated": "✅ تم تفعيل اشتراك المستخدم `{user}` بنجاح\\!",
    "user_deactivated": "✅ تم إلغاء اشتراك المستخدم `{user}` بنجاح\\.",
    "user_banned_success": "✅ تم حظر المستخدم `{user}` بنجاح\\!",
    "user_unbanned_success": "✅ تم إلغاء حظر المستخدم `{user}` بنجاح\\!",
    "broadcast_sent": "🎉 تم الانتهاء من إرسال الإعلان\\.\\n\\n✅ نجح الإرسال لـ *{success}* مشترك\\.\\n❌ فشل الإرسال لـ *{failed}* مشترك \\(قد يكون بسبب حظر البوت أو مشاكل أخرى\\)\\.",
    "invalid_user": "⚠️ المعرف المدخل غير صالح\\. يرجى إدخال معرف مستخدم صحيح \\(مثل `@username`\\) أو ID رقمي\\.",
    "admin_only": "⛔ عذراً، هذا الأمر مخصص للمدير فقط\\.",
    "subscription_required": "🔒 هذه الميزة تتطلب اشتراكاً فعالاً\\. يرجى الاشتراك للاستفادة منها\\.",
    "error_occurred": "⚠️ حدث خطأ غير متوقع\\. تم تسجيل الخطأ وسيتم مراجعته\\. نأسف للإزعاج، يرجى المحاولة مرة أخرى لاحقاً\\.",
    "trial_info": "🆓 لديك *{remaining}* رسائل مجانية متبقية من أصل *{total}* رسالة\\."
}

# خطط الاشتراك (تبقى كما هي)
SUBSCRIPTION_PLANS = {
    "daily": {"days": 1, "price": 4, "price_str": "4$", "emoji": "⏳"},
    "weekly": {"days": 7, "price": 8, "price_str": "8$", "emoji": "📅"},
    "monthly": {"days": 30, "price": 12, "price_str": "12$", "emoji": "🗓️"},
    "premium": {"days": 365, "price": 100, "price_str": "100$", "emoji": "💎"}
}

# حالات المحادثة (تبقى كما هي)
class ConversationStates:
    ADMIN_MENU = 0
    ADMIN_ACTIVATE_PLAN = 1
    ADMIN_ACTIVATE_USER = 2
    ADMIN_DEACTIVATE_USER = 3
    ADMIN_BROADCAST = 4
    ADMIN_BAN_USER = 6
    ADMIN_UNBAN_USER = 7
    ADMIN_CONFIRM_BROADCAST = 8

# ---------------------- وظائف المساعدة المحسنة ----------------------
def is_admin(user_id: int) -> bool:
    return str(user_id) == ADMIN_ID

def is_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if is_admin(user_id): return True
    vip_users = get_vip_users(context)
    if user_id in vip_users:
        # Ensure expiry is datetime object
        expiry = vip_users[user_id].get("expiry")
        if isinstance(expiry, str): # Convert from string if loaded from old format potentially
             try:
                 expiry = datetime.datetime.fromisoformat(expiry)
                 vip_users[user_id]["expiry"] = expiry # Update in context
             except ValueError:
                 logger.error(f"Invalid expiry date format for user {user_id}: {vip_users[user_id].get('expiry')}")
                 return False
        if isinstance(expiry, datetime.datetime):
            # Make sure datetime is timezone-aware or compare consistently
            # Assuming datetime.datetime.now() is naive, compare naively
            return datetime.datetime.now() < expiry
        else:
             logger.warning(f"Expiry for user {user_id} is not a datetime object: {type(expiry)}")
             return False # Treat invalid expiry as expired
    return False

def is_banned(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    return user_id in get_banned_users(context)

def has_free_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    return get_free_trial_users(context).get(user_id, 0) > 0

async def send_typing_action(update: Update):
    """Sends typing action asynchronously."""
    if update.effective_chat:
        try:
            await update.effective_chat.send_action(action="typing")
        except TelegramError as e:
            # Log common, non-critical errors as warnings
            if "chat not found" in str(e).lower() or "bot was blocked" in str(e).lower():
                logger.warning(f"Could not send typing action to chat {update.effective_chat.id}: {e}")
            else:
                logger.error(f"Error sending typing action to chat {update.effective_chat.id}: {e}")
        except Exception as e:
             logger.error(f"Unexpected error sending typing action: {e}", exc_info=True)

# Improved MarkdownV2 Escaper
def escape_markdown_v2(text: str, entity_types: Optional[List[str]] = None) -> str:
    """
    Escapes text for Telegram MarkdownV2 parse mode.
    Handles escaping outside specific entity types like 'code' and 'pre'.

    Args:
        text: The text to escape.
        entity_types: A list of entity types (e.g., ["pre", "code"]) that should not be escaped internally.
                      (Note: This implementation simplifies and escapes all listed chars regardless of entities,
                       relying on later separation of code blocks in send_formatted_message)

    Returns:
        The escaped string.
    """
    if not text:
        return ""

    escape_chars = r"_*[]()~`>#+-=|{}.!" # Characters to escape
    # Use a regex substitution to escape each character in the list
    # The pattern looks for any character in the escape_chars set
    # The replacement adds a backslash before the found character
    escaped_text = re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)

    return escaped_text

# Revised clean_response focusing on fixing the Unicode error and simplifying
def clean_response(text: str) -> str:
    """
    Cleans the API response text.
    Removes potentially problematic Unicode characters and formats code blocks.
    Returns text ready for MarkdownV2 (code blocks marked, rest needs escaping).
    """
    if not isinstance(text, str):
        logger.warning(f"clean_response received non-string input: {type(text)}")
        return ""

    # 1. Handle problematic Unicode characters
    try:
        # Remove control characters except tab, newline, carriage return
        cleaned_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
        # Remove Unicode non-characters and specials often causing issues
        cleaned_text = re.sub(r"[\uFFF0-\uFFFF]", "", cleaned_text)
        # Handle surrogates robustly by encoding/decoding
        # This should fix the incomplete escape error by removing lone surrogates
        cleaned_text = cleaned_text.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")

    except Exception as e:
        logger.error(f"Error during Unicode cleaning: {e}", exc_info=True)
        # Fallback: return original text or a basic cleaned version
        cleaned_text = text.encode("utf-8", "ignore").decode("utf-8")

    # 2. Format code blocks (assuming API uses ```python ... ``` or `...`)
    # This function now only marks code blocks; escaping happens later.
    def replace_code_block(match):
        language = match.group(1) or "" # Handle optional language
        code = match.group(2).strip()
        # Ensure triple backticks are not inside the code itself (add zero-width space)
        code = code.replace("```", "`\u200b``")
        return f"```{language}\n{code}\n```"

    # Handle ``` blocks (case-insensitive language tag)
    cleaned_text = re.sub(r"```(\w*)\n?([\s\S]+?)\n?```", replace_code_block, cleaned_text, flags=re.IGNORECASE)

    # Handle `inline code` - ensure no newlines inside, make non-greedy
    cleaned_text = re.sub(r"`([^`\n]+?)`", r"`\1`", cleaned_text)

    return cleaned_text.strip()


# Asynchronous API Call using aiohttp
# Create a single session for efficiency
aiohttp_session = None

async def get_aiohttp_session() -> aiohttp.ClientSession:
    """Initializes and returns a shared aiohttp ClientSession."""
    global aiohttp_session
    if aiohttp_session is None or aiohttp_session.closed:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        # Consider adding connection pool limits if needed
        connector = aiohttp.TCPConnector(limit_per_host=20) # Example limit
        aiohttp_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        logger.info("Created new aiohttp session.")
    return aiohttp_session

async def close_aiohttp_session():
    """Closes the shared aiohttp session."""
    global aiohttp_session
    if aiohttp_session and not aiohttp_session.closed:
        await aiohttp_session.close()
        aiohttp_session = None
        logger.info("Closed aiohttp session.")

async def call_wormgpt_api_async(prompt: str) -> Tuple[Optional[str], bool]:
    """
    Asynchronously calls the WormGPT API using aiohttp.

    Args:
        prompt: The user"s prompt.

    Returns:
        A tuple: (response_text, is_markdown_formatted).
        response_text: The cleaned API response or an error message (plain text).
        is_markdown_formatted: True if the response_text contains ``` or ` blocks.
                               False if it"s plain text (like an error message).
    """
    session = await get_aiohttp_session()
    params = {"prompt": prompt, "model": "WormGPT"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; WORMGPTBot/1.1; +https://t.me/your_bot_username)"} # Updated UA

    try:
        async with session.get(API_URL, params=params, headers=headers) as response:
            response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)

            # Read response body safely, trying UTF-8 then fallback
            try:
                response_text = await response.text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning("API response was not UTF-8, trying fallback decoding.")
                response_bytes = await response.read()
                response_text = response_bytes.decode("utf-8", errors="ignore")

            # Clean the response (handles Unicode issues, identifies code blocks)
            cleaned_response = clean_response(response_text)

            if not cleaned_response:
                logger.warning(f"API response for prompt ", prompt[:50], "... was empty after cleaning.")
                return bot_messages["api_error"] + " (الرد كان فارغاً)", False # Plain text error

            # Check if the cleaned response contains markdown code blocks/inline
            is_markdown = "```" in cleaned_response or "`" in cleaned_response
            return cleaned_response, is_markdown # Return cleaned text and format flag

    except asyncio.TimeoutError:
        logger.error(f"API Timeout Error (aiohttp) for prompt: {prompt[:50]}...")
        return bot_messages["api_timeout"], False # Plain text error
    except aiohttp.ClientResponseError as e:
        logger.error(f"API HTTP Error: {e.status} - {e.message} for prompt: {prompt[:50]}...")
        return bot_messages["api_error"], False # Plain text error
    except aiohttp.ClientError as e: # Catch other client errors like connection issues
        logger.error(f"API Client Connection Error: {e} for prompt: {prompt[:50]}...")
        return bot_messages["api_error"], False # Plain text error
    except Exception as e:
        # Catch potential errors in clean_response as well
        logger.error(f"Unexpected API/Processing Error: {e}", exc_info=True)
        return bot_messages["api_error"], False # Plain text error


# Revised send_formatted_message to handle MarkdownV2 correctly
async def send_formatted_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: Optional[str] = ParseMode.MARKDOWN_V2, # Default to MarkdownV2
    reply_markup=None,
    is_response_formatted: bool = False # Flag if "text" already has ``` or `
):
    """
    Sends a message, handling MarkdownV2 escaping and splitting.

    Args:
        update: The Telegram Update object.
        context: The Telegram CallbackContext object.
        text: The message text. Can be plain or contain ```/` blocks if is_response_formatted=True.
        parse_mode: Parse mode (MARKDOWN_V2, HTML, or None).
        reply_markup: Inline keyboard markup.
        is_response_formatted: If True, assumes "text" came from clean_response and needs selective escaping.
                               If False, assumes "text" is plain and needs full escaping if parse_mode=MARKDOWN_V2.
    """
    MAX_LENGTH = 4096
    if not text or not update.effective_message:
        logger.warning("Attempted to send an empty message or update has no effective_message.")
        return

    final_text = text
    actual_parse_mode = parse_mode

    # --- MarkdownV2 Handling ---
    if parse_mode == ParseMode.MARKDOWN_V2:
        if is_response_formatted:
            # Text has ``` or ` blocks. Escape *around* them.
            parts = re.split(r"(```(?:\w*\n)?(?:[\s\S]+?\n)?```|`[^`\n]+?`)", text)
            escaped_parts = []
            for i, part in enumerate(parts):
                if not part: continue # Skip empty parts from split
                if i % 2 == 1: # Code block or inline code - leave as is
                    escaped_parts.append(part)
                else: # Plain text part - escape it
                    escaped_parts.append(escape_markdown_v2(part))
            final_text = "".join(escaped_parts)
        else:
            # Text is plain, escape the whole thing
            final_text = escape_markdown_v2(text)

        # Basic validation: Check for unescaped special characters outside code blocks
        # This is a simple check and might not catch all edge cases
        temp_text = re.sub(r"```(?:\w*\n)?(?:[\s\S]+?\n)?```|`[^`\n]+?`", "", final_text) # Remove code blocks
        # Look for unescaped chars: _ * [ ] ( ) ~ ` > # + - = | { } . !
        # Ensure the character is not already preceded by a backslash
        if re.search(r"(?<!\\)(?:\\\\)*([_*[\]()~`>#+\-=|{}.!])", temp_text):
             # Found potentially unescaped character
             match = re.search(r"(?<!\\)(?:\\\\)*([_*[\]()~`>#+\-=|{}.!])", temp_text)
             char = match.group(1)
             pos = match.start(1)
             context_snippet = temp_text[max(0, pos-20):min(len(temp_text), pos+20)]
             logger.warning(f"Potential unescaped MarkdownV2 character ", char, f" detected near: ...{context_snippet}... Attempting fallback to plain text.")
             # Fallback to plain text to avoid Telegram error
             final_text = text # Use original unescaped text
             actual_parse_mode = None

    # --- Sending Logic ---
    try:
        if len(final_text) <= MAX_LENGTH:
            await update.effective_message.reply_text(
                final_text,
                parse_mode=actual_parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        else:
            # Splitting logic - Improved slightly to handle edge cases
            logger.info(f"Message length ({len(final_text)}) exceeds limit, splitting...")
            parts = []
            start_index = 0
            while start_index < len(final_text):
                # Find a suitable split point (newline preferred) near MAX_LENGTH
                split_index = -1
                search_end = min(start_index + MAX_LENGTH, len(final_text))
                # Try to find last newline within the chunk
                newline_index = final_text.rfind("\n", start_index, search_end)
                if newline_index != -1 and newline_index > start_index:
                    split_index = newline_index + 1 # Split after newline
                else:
                    # No newline found, or it"s at the very beginning, force split at MAX_LENGTH
                    split_index = search_end

                # Ensure we don"t split inside a code block marker ` or ```
                # This is still basic and might fail for complex nested cases
                chunk_to_check = final_text[start_index:split_index]
                # Check if the split point is inside ```...```
                open_triple = chunk_to_check.count("```") % 2 != 0
                # Check if the split point is inside `...`
                open_single = chunk_to_check.count("`") % 2 != 0

                if open_triple or open_single:
                    # Try to find the closing marker or force split earlier/later
                    # Simple fallback: just split at MAX_LENGTH if inside code
                    split_index = search_end
                    logger.debug("Splitting potentially inside a code block, forcing split at max length.")

                # Ensure split_index is valid
                split_index = max(start_index + 1, split_index) # Must advance at least 1 char
                split_index = min(split_index, len(final_text)) # Cannot exceed total length

                parts.append(final_text[start_index:split_index])
                start_index = split_index

            # Send the parts
            for i, part in enumerate(parts):
                if part.strip(): # Don"t send empty parts
                    current_reply_markup = reply_markup if i == len(parts) - 1 else None
                    await update.effective_message.reply_text(
                        part,
                        parse_mode=actual_parse_mode,
                        reply_markup=current_reply_markup,
                        disable_web_page_preview=True
                    )
                    if i < len(parts) - 1:
                        await asyncio.sleep(0.8) # Slightly longer delay for split messages

    except TelegramError as e:
        logger.error(f"Error sending formatted message ({actual_parse_mode}): {e}")
        logger.error(f"Failed message content (first 500 chars): {final_text[:500]}")
        try:
            error_msg_plain = bot_messages["error_occurred"]
            if "Can't parse entities" in str(e):
                 error_msg_plain += " (خطأ في تنسيق الرسالة)"
            elif "message is too long" in str(e).lower():
                 error_msg_plain += " (الرسالة أطول من اللازم)"
            # Add more specific error details if possible
            if hasattr(e, "message"):
                 # Escape the error message itself before including it
                 escaped_telegram_error = escape_markdown_v2(e.message[:100])
                 error_msg_plain += f" - `{escaped_telegram_error}`"

            await update.effective_message.reply_text(
                 error_msg_plain,
                 parse_mode=None, # Send error as plain text
                 reply_markup=reply_markup if len(final_text) <= MAX_LENGTH else None
            )
        except Exception as inner_e:
            logger.error(f"Failed to send plain text error fallback: {inner_e}")
    except Exception as e:
        logger.error(f"Unexpected error in send_formatted_message: {e}", exc_info=True)

# Async get_user_info (using context.bot)
async def get_user_info(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Dict[str, Any]:
    """Gets user info asynchronously and escapes it for MarkdownV2."""
    try:
        user = await context.bot.get_chat(user_id)
        username = f"@{user.username}" if user.username else "لا يوجد معرف"
        name = user.full_name or f"User_{user_id}"

        escaped_name = escape_markdown_v2(name)
        # Username might contain underscores, escape it too
        escaped_username = escape_markdown_v2(username) if username != "لا يوجد معرف" else username

        return {"username": escaped_username, "name": escaped_name, "id": user_id, "raw_name": name, "raw_username": user.username}
    except TelegramError as e:
        logger.warning(f"Could not get info for user {user_id}: {e}")
        escaped_id_str = escape_markdown_v2(str(user_id))
        return {"username": "غير معروف", "name": f"User\\_{escaped_id_str}", "id": user_id, "raw_name": f"User_{user_id}", "raw_username": None}
    except Exception as e:
        logger.error(f"Unexpected error getting info for user {user_id}: {e}", exc_info=True)
        return {"username": "خطأ", "name": "خطأ", "id": user_id, "raw_name": "Error", "raw_username": None}

# Error handler (already async, ensure plain text reply)
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            error_msg_plain = bot_messages["error_occurred"]
            await update.effective_message.reply_text(error_msg_plain, parse_mode=None)
        except Exception as e:
            logger.error(f"Failed to send error message to user after an exception: {e}")

# --- Start of Part 2 ---

# ---------------------- معالجات الأوامر الأساسية (متابعة) ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command, welcoming users and showing options."""
    user = update.effective_user
    user_id = user.id
    now = datetime.datetime.now()

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    await send_typing_action(update)

    user_stats = get_user_stats(context, user_id)
    is_new_user = False
    if user_stats.get("join_date") is None:
        is_new_user = True
        user_stats["join_date"] = now
        # Grant free trial if not VIP and no trial record exists
        free_trial_users = get_free_trial_users(context)
        if user_id not in free_trial_users and not is_vip(context, user_id):
            free_trial_users[user_id] = MAX_FREE_MESSAGES
            logger.info(f"Granted {MAX_FREE_MESSAGES} free messages to new user {user_id}")

    user_stats["last_active"] = now

    # Schedule persistence update
    await update_persistence(context)

    try:
        if is_vip(context, user_id):
            vip_users = get_vip_users(context)
            vip_info = vip_users.get(user_id, {})
            expiry = vip_info.get("expiry")
            plan = vip_info.get("plan", "غير محدد")
            messages = user_stats.get("messages_sent", 0)

            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M")) if isinstance(expiry, datetime.datetime) else "غير محدد"
            plan_str = escape_markdown_v2(plan.capitalize())
            user_first_name = escape_markdown_v2(user.first_name or f"User {user_id}")

            welcome_vip_msg = f"🎉 أهلاً بعودتك يا `{user_first_name}`\\! 🎉\\n\\nاشتراكك في *WORMGPT* \\({plan_str}\\) لا يزال فعالاً حتى `{expiry_str}`\\. استمتع بالذكاء الاصطناعي *بدون قيود*\\.\\n\\n" + bot_messages['features'].format(max_free=MAX_FREE_MESSAGES)
            await send_formatted_message(update, context, welcome_vip_msg, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

        else:
            # Show subscription options and free trial info for regular users
            plan_details_list = []
            for plan_name, plan_info in SUBSCRIPTION_PLANS.items():
                plan_details_list.append(
f"{plan_info['emoji']} *{escape_markdown_v2(plan_name.capitalize())}*: {escape_markdown_v2(plan_info['price_str'])}) \\({plan_info['days']} يوم\\)"                )
            plan_details = "\\n".join(plan_details_list)

            buttons = []
            free_trial_users = get_free_trial_users(context)
            remaining_free = free_trial_users.get(user_id, 0)
            if remaining_free > 0:
                buttons.append([InlineKeyboardButton(f"🆓 تجربة مجانية ({remaining_free} متبقية)", callback_data="free_trial_info")])
            # Show request button only if they have no trial record AND are not VIP
            elif user_id not in free_trial_users and not is_vip(context, user_id):
                 buttons.append([InlineKeyboardButton("🎁 طلب تجربة مجانية", callback_data="request_free_trial")])

            plan_buttons = [InlineKeyboardButton(
                                f"{plan_info['emoji']} {escape_markdown_v2(plan_name.capitalize())} ({escape_markdown_v2(plan_info['price_str'])})",
                                callback_data=f"plan_{plan_name}"
                            ) for plan_name, plan_info in SUBSCRIPTION_PLANS.items()]
            # Arrange plan buttons in rows of 2
            buttons.extend([plan_buttons[i:i+2] for i in range(0, len(plan_buttons), 2)])

            buttons.append([InlineKeyboardButton("📞 راسل المطور", url="https://t.me/OZOOZOZ")])
            buttons.append([InlineKeyboardButton("🌟 عرض الميزات", callback_data="show_features")])

            markup = InlineKeyboardMarkup(buttons)
            welcome_msg = bot_messages["welcome"] + "\\n\\n" + bot_messages["plan_choices"].format(plan_details=plan_details)
            await send_formatted_message(update, context, welcome_msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    except TelegramError as e:
        logger.error(f"TelegramError in start command for user {user_id}: {e}")
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
    except Exception as e:
        logger.error(f"Unexpected error in start command for user {user_id}: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)

async def show_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /features command or callback query."""
    query = update.callback_query
    message = update.effective_message
    try:
        features_text = bot_messages["features"].format(max_free=MAX_FREE_MESSAGES)
        if query:
            await query.answer()
            # Edit the message directly with the features text
            await query.edit_message_text(features_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_to_start")]])) # Add back button
        elif message:
             await send_formatted_message(update, context, features_text, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    except TelegramError as e:
        if "message is not modified" in str(e).lower():
            logger.info("Message not modified (show_features)")
            if query: await query.answer("الميزات معروضة بالفعل.") # Provide feedback
        else:
            logger.error(f"Error handling show_features: {e}")
            if query: await query.answer("حدث خطأ.")
    except Exception as e:
        logger.error(f"Unexpected error in show_features: {e}", exc_info=True)
        if query: await query.answer("حدث خطأ.")

async def subscription_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user"s current subscription or trial status."""
    user_id = update.effective_user.id

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    await send_typing_action(update)

    try:
        info_message = ""
        if is_vip(context, user_id):
            vip_users = get_vip_users(context)
            vip_info = vip_users.get(user_id, {})
            expiry = vip_info.get("expiry")
            plan = vip_info.get("plan", "غير محدد")
            user_stats = get_user_stats(context, user_id)
            messages = user_stats.get("messages_sent", 0)

            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M")) if isinstance(expiry, datetime.datetime) else "غير محدد"
            plan_str = escape_markdown_v2(plan.capitalize())
            remaining_str = ""
            if isinstance(expiry, datetime.datetime):
                remaining = expiry - datetime.datetime.now()
                if remaining.total_seconds() > 0:
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    remaining_str = f"\\n🕒 المتبقي: *{days}* يوم و *{hours}* ساعة"
                else:
                    remaining_str = "\\n⏳ الاشتراك منتهي الصلاحية\\."

            info_message = bot_messages["subscription_info"].format(
                plan=plan_str,
                expiry=f"`{expiry_str}`",
                messages=messages
            ) + remaining_str

        else:
            free_trial_users = get_free_trial_users(context)
            remaining = free_trial_users.get(user_id)
            if remaining is not None and remaining > 0:
                info_message = bot_messages["trial_info"].format(
                    remaining=remaining,
                    total=MAX_FREE_MESSAGES
                )
            elif user_id in free_trial_users and remaining <= 0:
                 info_message = TRIAL_EXHAUSTED
            else:
                # User has no VIP, no trial record - offer trial request
                info_message = escape_markdown_v2("⚠️ ليس لديك اشتراك فعال أو رسائل مجانية حالياً.")
                # Optionally add a button to request trial or view plans
                # reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 طلب تجربة مجانية", callback_data="request_free_trial")]])

        await send_formatted_message(update, context, info_message, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    except Exception as e:
        logger.error(f"Error in subscription_info_command: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays admin help message."""
    if not is_admin(update.effective_user.id):
        await send_formatted_message(update, context, bot_messages["admin_only"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return
    await send_formatted_message(update, context, bot_messages["admin_help"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

# ---------------------- معالجة الرسائل المحسنة ----------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles regular text messages from users."""
    user = update.effective_user
    user_id = user.id
    message_text = update.message.text

    if is_banned(context, user_id):
        logger.info(f"Ignoring message from banned user {user_id}")
        return

    await send_typing_action(update)

    now = datetime.datetime.now()
    user_stats = get_user_stats(context, user_id)
    # Initialize join date if missing (e.g., user interacted before persistence)
    if user_stats.get("join_date") is None:
        user_stats["join_date"] = now
        logger.warning(f"User {user_id} sent message without /start, setting join_date.")
        # Grant trial if needed (redundant with /start but safe)
        free_trial_users = get_free_trial_users(context)
        if user_id not in free_trial_users and not is_vip(context, user_id):
            free_trial_users[user_id] = MAX_FREE_MESSAGES
            logger.info(f"Granted free trial to user {user_id} via handle_message.")

    user_stats["messages_sent"] = user_stats.get("messages_sent", 0) + 1
    user_stats["last_active"] = now

    can_use_bot = False
    notify_trial_usage = False
    free_trial_users = get_free_trial_users(context)

    if is_vip(context, user_id):
        can_use_bot = True
    elif user_id in free_trial_users:
        remaining = free_trial_users[user_id]
        if remaining > 0:
            can_use_bot = True
            free_trial_users[user_id] -= 1
            logger.info(f"User {user_id} used a free message. Remaining: {free_trial_users[user_id]}")
            # Notify when trial is exhausted
            if free_trial_users[user_id] == 0:
                notify_trial_usage = True # Flag to send notification after API response
        else:
            # User has a trial record but it"s 0 - shouldn"t happen if cleaned up, but handle defensively
            logger.warning(f"User {user_id} has 0 free messages in record. Should have been handled earlier.")
            await send_formatted_message(update, context, TRIAL_EXHAUSTED, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
            await update_persistence(context) # Save stats update
            return

    if not can_use_bot:
        await send_formatted_message(update, context, bot_messages["not_vip"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        await update_persistence(context) # Save stats update
        return

    # Call the asynchronous API function
    api_response_text, is_markdown = await call_wormgpt_api_async(message_text)

    # Send the API response (or error message from the API call)
    await send_formatted_message(
        update, context,
        api_response_text,
        parse_mode=ParseMode.MARKDOWN_V2 if is_markdown else None,
        is_response_formatted=is_markdown
    )

    # Send trial exhausted notification *after* the main response
    if notify_trial_usage:
        await asyncio.sleep(0.5) # Small delay
        await send_formatted_message(update, context, TRIAL_EXHAUSTED, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    # Schedule persistence update after processing the message
    await update_persistence(context)

# ---------------------- لوحة تحكم الأدمن (محادثة - محسنة) ----------------------
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main admin menu."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_formatted_message(update, context, bot_messages["admin_only"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return ConversationHandler.END

    # Buttons are generated quickly, no blocking operations here
    buttons = [
        [InlineKeyboardButton("🔑 تفعيل اشتراك", callback_data="admin_activate"),
         InlineKeyboardButton("✖️ إلغاء اشتراك", callback_data="admin_deactivate")],
        [InlineKeyboardButton("📋 عرض المشتركين", callback_data="admin_list_vips"),
         InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats")],
        [InlineKeyboardButton("⛔ حظر مستخدم", callback_data="admin_ban"),
         InlineKeyboardButton("✅ إلغاء حظر", callback_data="admin_unban")],
        [InlineKeyboardButton("📢 إرسال إعلان", callback_data="admin_broadcast")]
    ]
    markup = InlineKeyboardMarkup(buttons)

    # Check if called via command or callback query
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.edit_message_text(bot_messages["admin_greeting"], reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
        except TelegramError as e:
            if "message is not modified" in str(e).lower():
                logger.info("Admin menu not modified.")
            else: raise e # Re-raise other errors
    else:
        await send_formatted_message(update, context, bot_messages["admin_greeting"], reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

    return ConversationStates.ADMIN_MENU

async def admin_activate_plan_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows plan selection for activation."""
    query = update.callback_query
    await query.answer()

    buttons = [
        [InlineKeyboardButton(f"{plan_info['emoji']} {escape_markdown_v2(plan_name.capitalize())} ({plan_info['days']} يوم)", callback_data=f"admin_plan_{plan_name}")]
        for plan_name, plan_info in SUBSCRIPTION_PLANS.items()
    ]
    buttons.append([InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")])
    markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(escape_markdown_v2("📝 اختر نوع الاشتراك لتفعيله:"), reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_ACTIVATE_PLAN

async def admin_get_user_for_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for user ID/username to activate."""
    query = update.callback_query
    await query.answer()

    plan = query.data.split("_")[2]
    context.user_data["selected_plan"] = plan # Store plan in user_data (specific to this admin"s interaction)

    plan_str = escape_markdown_v2(plan.capitalize())
    message_text = f"📩 أرسل الآن *اسم المستخدم* \\(مثل `@username`\\) أو *الـ ID الرقمي* للشخص الذي تريد تفعيل خطة `{plan_str}` له:"
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_ACTIVATE_USER

async def admin_activate_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activates the selected plan for the specified user."""
    user_input = update.message.text.strip()
    admin_id = update.effective_user.id
    plan = context.user_data.get("selected_plan")

    if not plan:
        await send_formatted_message(update, context, escape_markdown_v2("⚠️ خطأ: لم يتم تحديد خطة. يرجى البدء من جديد /admin"), parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    target_user_id = None
    target_user_info = None

    try:
        # Use async get_user_info which handles lookup and escaping
        if user_input.startswith("@"):
            try:
                # Attempt to resolve username via get_chat
                user = await context.bot.get_chat(user_input)
                target_user_id = user.id
                target_user_info = await get_user_info(context, target_user_id)
            except TelegramError as e:
                logger.warning(f"Could not find user by username {user_input}: {e}")
                await send_formatted_message(update, context, bot_messages['invalid_user'] + escape_markdown_v2(f" \\(لم يتم العثور على {user_input}\\) "), parse_mode=ParseMode.MARKDOWN_V2)
                return ConversationStates.ADMIN_ACTIVATE_USER # Ask again
        else:
            try:
                target_user_id = int(user_input)
                target_user_info = await get_user_info(context, target_user_id)
                # Check if get_user_info actually found the user
                if target_user_info['username'] == "غير معروف" and target_user_info['name'] == f"User\\_{escape_markdown_v2(str(target_user_id))}":
                     logger.warning(f"Could not get valid info for user ID {target_user_id}, might not exist or bot blocked.")
                     # Proceed cautiously, but warn admin
                     await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ لم يتم العثور على معلومات تفصيلية للمستخدم ID `{target_user_id}`. قد لا يكون موجوداً أو قام بحظر البوت. سيتم التفعيل على أي حال."), parse_mode=ParseMode.MARKDOWN_V2)

            except ValueError:
                await send_formatted_message(update, context, bot_messages["invalid_user"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
                return ConversationStates.ADMIN_ACTIVATE_USER # Ask again
            except TelegramError as e: # Catch potential get_chat errors by ID too
                 logger.warning(f"Error fetching user info for ID {target_user_id}: {e}")
                 await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم ID `{target_user_id}`. يرجى المحاولة مرة أخرى."), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationStates.ADMIN_ACTIVATE_USER

        if target_user_id and target_user_info:
            days = SUBSCRIPTION_PLANS[plan]["days"]
            expiry = datetime.datetime.now() + datetime.timedelta(days=days)

            vip_users = get_vip_users(context)
            vip_users[target_user_id] = {"expiry": expiry, "plan": plan}

            # Remove from trial and pending payments if exists
            free_trial_users = get_free_trial_users(context)
            if target_user_id in free_trial_users: del free_trial_users[target_user_id]
            pending_payments = get_pending_payments(context)
            if target_user_id in pending_payments: del pending_payments[target_user_id]

            await update_persistence(context) # Save changes

            # Use already escaped info from get_user_info
            user_display = f"{target_user_info['name']} \\({target_user_info['username']}, ID: `{target_user_id}`\\)"
            plan_str = escape_markdown_v2(plan.capitalize())
            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M"))

            # Send confirmation to admin
            admin_confirmation = (
                bot_messages["user_activated"].format(user=user_display) +
                f"\\n📦 الخطة: *{plan_str}*\\n⏳ المدة: *{days}* يوم\\n📅 تنتهي في: `{expiry_str}`"
            )
            await send_formatted_message(update, context, admin_confirmation, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

            # Send notification to the activated user
            try:
                user_stats = get_user_stats(context, target_user_id)
                user_messages = user_stats.get("messages_sent", 0)
                user_notification = (
                    bot_messages["vip_active"] + "\\n\\n" +
                    bot_messages["subscription_info"].format(
                        plan=plan_str,
                        expiry=f"`{expiry_str}`",
                        messages=user_messages
                    )
                )
                # Use send_formatted_message for consistency and error handling
                # Need to use context.bot.send_message carefully as send_formatted_message needs an Update
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=user_notification,
                    parse_mode=ParseMode.MARKDOWN_V2
                    # Note: Ensure user_notification is correctly escaped (it should be)
                )
            except TelegramError as e:
                logger.error(f"Error sending activation message to user {target_user_id}: {e}")
                error_detail = escape_markdown_v2(str(e))
                await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ تم تفعيل الاشتراك، ولكن فشل إرسال رسالة التأكيد للمستخدم {target_user_id}. الخطأ: ") + f"`{error_detail}`", parse_mode=ParseMode.MARKDOWN_V2)

            context.user_data.pop("selected_plan", None)
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Admin activation error: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
        context.user_data.pop("selected_plan", None)
        return ConversationHandler.END

async def admin_get_user_for_deactivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for user ID/username to deactivate."""
    query = update.callback_query
    await query.answer()
    message_text = escape_markdown_v2("📩 أرسل اسم المستخدم (مثل @username) أو الـ ID الرقمي للشخص الذي تريد إلغاء اشتراكه:")
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_DEACTIVATE_USER

async def admin_deactivate_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivates the subscription for the specified user."""
    user_input = update.message.text.strip()
    admin_id = update.effective_user.id
    target_user_id = None
    target_user_info = None

    try:
        # Use async get_user_info
        if user_input.startswith("@"):
            try:
                user = await context.bot.get_chat(user_input)
                target_user_id = user.id
                target_user_info = await get_user_info(context, target_user_id)
            except TelegramError:
                await send_formatted_message(update, context, bot_messages['invalid_user'] + escape_markdown_v2(f" \\(لم يتم العثور على {user_input}\\) "), parse_mode=ParseMode.MARKDOWN_V2)
                return ConversationStates.ADMIN_DEACTIVATE_USER
        else:
            try:
                target_user_id = int(user_input)
                target_user_info = await get_user_info(context, target_user_id)
                # Check if user info is valid
                if target_user_info['username'] == "غير معروف":
                     logger.warning(f"Could not get valid info for user ID {target_user_id} during deactivation.")
            except ValueError:
                await send_formatted_message(update, context, bot_messages["invalid_user"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
                return ConversationStates.ADMIN_DEACTIVATE_USER
            except TelegramError as e:
                 logger.warning(f"Error fetching user info for ID {target_user_id} during deactivation: {e}")
                 await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم ID `{target_user_id}`. يرجى المحاولة مرة أخرى."), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationStates.ADMIN_DEACTIVATE_USER

        if target_user_id and target_user_info:
            vip_users = get_vip_users(context)
            if target_user_id in vip_users:
                del vip_users[target_user_id]
                await update_persistence(context) # Save changes

                user_display = f"{target_user_info['name']} \\({target_user_info['username']}, ID: `{target_user_id}`\\)"
                await send_formatted_message(update, context, bot_messages["user_deactivated"].format(user=user_display), parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

                # Send notification to the deactivated user
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=bot_messages["subscription_expired"],
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except TelegramError as e:
                    logger.error(f"Error sending deactivation message to user {target_user_id}: {e}")
                    error_detail = escape_markdown_v2(str(e))
                    await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ تم إلغاء الاشتراك، ولكن فشل إرسال رسالة للمستخدم {target_user_id}. الخطأ: ") + f"`{error_detail}`", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await send_formatted_message(update, context, escape_markdown_v2("⚠️ هذا المستخدم ليس لديه اشتراك فعال لإلغائه."), parse_mode=ParseMode.MARKDOWN_V2)

            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Admin deactivation error: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
        return ConversationHandler.END

async def admin_list_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists active VIP users, optimized with concurrent fetching."""
    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_text("⏳ جارٍ تحميل قائمة المشتركين\\.\\.\\.", parse_mode=None)

        vip_users = get_vip_users(context)
        now = datetime.datetime.now()
        active_vips_data = []

        # Filter active VIPs first
        active_vip_ids = []
        for uid, data in vip_users.items():
            expiry = data.get("expiry")
            if isinstance(expiry, str): # Handle potential string dates from old persistence
                try: expiry = datetime.datetime.fromisoformat(expiry)
                except: expiry = None
            if isinstance(expiry, datetime.datetime) and expiry > now:
                active_vip_ids.append(uid)

        if not active_vip_ids:
            await query.edit_message_text(bot_messages["no_vips"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
            return

        # Fetch user info concurrently
        user_info_tasks = [get_user_info(context, uid) for uid in active_vip_ids]
        user_infos = await asyncio.gather(*user_info_tasks, return_exceptions=True)

        user_info_map = {info["id"]: info for info in user_infos if isinstance(info, dict)}
        failed_fetches = [uid for uid, info in zip(active_vip_ids, user_infos) if not isinstance(info, dict)]
        if failed_fetches:
             logger.warning(f"Failed to fetch info for {len(failed_fetches)} VIP users: {failed_fetches}")

        # Prepare data for sorting
        for user_id in active_vip_ids:
            data = vip_users[user_id]
            user_info = user_info_map.get(user_id)
            expiry = data["expiry"]
            if isinstance(expiry, str): expiry = datetime.datetime.fromisoformat(expiry)

            if user_info: # Only include users we could get info for
                active_vips_data.append((user_id, expiry, data.get("plan", "N/A"), user_info))
            else:
                 # Include with placeholder info if fetch failed
                 escaped_id_str = escape_markdown_v2(str(user_id))
                 placeholder_info = {"name": f"User\\_{escaped_id_str}", "username": "غير معروف"}
                 active_vips_data.append((user_id, expiry, data.get("plan", "N/A"), placeholder_info))

        # Sort by expiry date
        sorted_vips = sorted(active_vips_data, key=lambda item: item[1])

        vip_list_items = []
        for user_id, expiry, plan, user_info in sorted_vips:
            expiry_str = escape_markdown_v2(expiry.strftime("%Y-%m-%d %H:%M"))
            plan_str = escape_markdown_v2(plan.capitalize())
            remaining_days = (expiry - now).days
            name = user_info["name"] # Already escaped
            username = user_info["username"] # Already escaped

            vip_list_items.append(
                f"👤 *{name}* \\({username}, ID: `{user_id}`\\)\\n"
                f"   📦 {plan_str} \\| ⏳ *{remaining_days} يوم* متبقي \\| 📅 تنتهي: `{expiry_str}`"
            )

        full_message = bot_messages["vip_list"].format(list="\\n\\n".join(vip_list_items))

        # Use send_formatted_message via query.edit_message_text for potentially long lists
        # Telegram might have limits on edit_message_text length too, but let's try
        try:
            await query.edit_message_text(full_message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")]])) # Add back button
        except TelegramError as e:
             if "message is too long" in str(e).lower():
                 logger.warning("VIP list too long for edit_message_text, sending as new message.")
                 # Fallback: Send as a new message if edit fails due to length
                 await send_formatted_message(update, context, full_message, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")]])) # Add back button
             else:
                 raise e # Re-raise other errors

    except Exception as e:
        logger.error(f"Error in admin_list_vips: {e}", exc_info=True)
        try:
            await query.edit_message_text(bot_messages["error_occurred"], parse_mode=None, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")]])) # Add back button
        except TelegramError:
             await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)

async def admin_stats_display(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays bot statistics, optimized."""
    query = update.callback_query
    message = update.effective_message

    if query:
        await query.answer()
        await query.edit_message_text("⏳ جارٍ تحميل الإحصائيات\\.\\.\\.", parse_mode=None)

    try:
        # Access data using helper functions
        all_user_data = context.user_data # Get all user data dict
        vip_users = get_vip_users(context)
        banned_users = get_banned_users(context)
        free_trial_users = get_free_trial_users(context)
        pending_payments = get_pending_payments(context)

        total_users = len(all_user_data)
        active_vips = len([uid for uid in vip_users if is_vip(context, uid)])
        # Sum messages from user_stats within application.user_data
        total_messages = sum(ud.get("stats", {}).get("messages_sent", 0) for uid, ud in all_user_data.items())
        banned_count = len(banned_users)
        trial_users_count = len([uid for uid, count in free_trial_users.items() if count > 0])
        pending_payments_count = len(pending_payments)

        # Get top users based on messages_sent in stats
        user_stats_list = [(uid, ud.get("stats", {}).get("messages_sent", 0)) for uid, ud in all_user_data.items()]
        top_users_stats = sorted(user_stats_list, key=lambda x: x[1], reverse=True)[:5]

        # Fetch info for top users concurrently
        top_user_ids = [uid for uid, count in top_users_stats]
        top_user_info_tasks = [get_user_info(context, uid) for uid in top_user_ids]
        top_user_infos = await asyncio.gather(*top_user_info_tasks, return_exceptions=True)
        top_user_info_map = {info["id"]: info for info in top_user_infos if isinstance(info, dict)}

        top_users_list = []
        for i, (uid, msg_count) in enumerate(top_users_stats):
            user_info = top_user_info_map.get(uid)
            if user_info:
                status = "⭐" if is_vip(context, uid) else ("🆓" if uid in free_trial_users and free_trial_users[uid] > 0 else "")
                name = user_info["name"] # Already escaped
                username = user_info["username"] # Already escaped
                top_users_list.append(f"{i+1}\\. {name} \\({username}\\) {status} \\- *{msg_count}* رسالة")
            else:
                 top_users_list.append(f"{i+1}\\. `User {uid}` \\- *{msg_count}* رسالة \\(خطأ في جلب المعلومات\\)")

        top_users_text = "\\n".join(top_users_list) if top_users_list else escape_markdown_v2("لا يوجد مستخدمين نشطين")

        stats_message = bot_messages["admin_stats"].format(
            users=total_users,
            vips=active_vips,
            messages=total_messages,
            banned=banned_count,
            trial_users=trial_users_count,
            pending_payments=pending_payments_count
        ) + f"\\n\\n🏆 *أكثر المستخدمين نشاطاً*:\\n{top_users_text}"

        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")]]) # Add back button

        if query:
            await query.edit_message_text(stats_message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=markup)
        elif message:
             await send_formatted_message(update, context, stats_message, parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True, reply_markup=markup)

    except Exception as e:
        logger.error(f"Error in admin_stats_display: {e}", exc_info=True)
        error_msg = bot_messages["error_occurred"]
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="admin_back_to_menu")]])
        if query:
            try: await query.edit_message_text(error_msg, parse_mode=None, reply_markup=markup)
            except: await send_formatted_message(update, context, error_msg, parse_mode=None)
        else:
             await send_formatted_message(update, context, error_msg, parse_mode=None)

# ---------------------- إدارة الحظر والإعلانات (محادثة - محسنة) ----------------------
async def admin_get_user_for_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for user ID/username to ban."""
    query = update.callback_query
    await query.answer()
    message_text = escape_markdown_v2("🚫 أرسل اسم المستخدم (مثل @username) أو الـ ID الرقمي للشخص الذي تريد حظره:")
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_BAN_USER

async def admin_ban_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bans the specified user."""
    user_input = update.message.text.strip()
    admin_id = update.effective_user.id
    target_user_id = None
    target_user_info = None

    try:
        # Use async get_user_info
        if user_input.startswith("@"):
            try:
                user = await context.bot.get_chat(user_input)
                target_user_id = user.id
                target_user_info = await get_user_info(context, target_user_id)
            except TelegramError:
                await send_formatted_message(update, context, bot_messages['invalid_user'] + escape_markdown_v2(f" \\(لم يتم العثور على {user_input}\\) "), parse_mode=ParseMode.MARKDOWN_V2)
                return ConversationStates.ADMIN_BAN_USER
        else:
            try:
                target_user_id = int(user_input)
                target_user_info = await get_user_info(context, target_user_id)
                if target_user_info['username'] == "غير معروف":
                     logger.warning(f"Could not get valid info for user ID {target_user_id} during ban.")
            except ValueError:
                await send_formatted_message(update, context, bot_messages["invalid_user"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
                return ConversationStates.ADMIN_BAN_USER
            except TelegramError as e:
                 logger.warning(f"Error fetching user info for ID {target_user_id} during ban: {e}")
                 await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم ID `{target_user_id}`. يرجى المحاولة مرة أخرى."), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationStates.ADMIN_BAN_USER

        if target_user_id and target_user_info:
            if target_user_id == int(ADMIN_ID):
                 await send_formatted_message(update, context, escape_markdown_v2("لا يمكنك حظر نفسك أيها المدير!"), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationHandler.END

            banned_users = get_banned_users(context)
            if target_user_id not in banned_users:
                banned_users.append(target_user_id)
                # Also remove VIP status if they had one
                vip_users = get_vip_users(context)
                if target_user_id in vip_users: del vip_users[target_user_id]
                free_trial_users = get_free_trial_users(context)
                if target_user_id in free_trial_users: del free_trial_users[target_user_id]

                await update_persistence(context) # Save changes

                user_display = f"{target_user_info['name']} \\({target_user_info['username']}, ID: `{target_user_id}`\\)"
                await send_formatted_message(update, context, bot_messages["user_banned_success"].format(user=user_display), parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

                # Send notification to the banned user
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=bot_messages["user_banned"],
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except TelegramError as e:
                    logger.error(f"Error sending ban message to user {target_user_id}: {e}")
                    # Don"t bother admin with this error, just log it
            else:
                await send_formatted_message(update, context, escape_markdown_v2("⚠️ هذا المستخدم محظور بالفعل."), parse_mode=ParseMode.MARKDOWN_V2)

            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Admin ban error: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
        return ConversationHandler.END

async def admin_get_user_for_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for user ID/username to unban."""
    query = update.callback_query
    await query.answer()
    message_text = escape_markdown_v2("✅ أرسل اسم المستخدم (مثل @username) أو الـ ID الرقمي للشخص الذي تريد إلغاء حظره:")
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_UNBAN_USER

async def admin_unban_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unbans the specified user."""
    user_input = update.message.text.strip()
    admin_id = update.effective_user.id
    target_user_id = None
    target_user_info = None

    try:
        # Use async get_user_info
        if user_input.startswith("@"):
            try:
                user = await context.bot.get_chat(user_input)
                target_user_id = user.id
                target_user_info = await get_user_info(context, target_user_id)
            except TelegramError:
                await send_formatted_message(update, context, bot_messages['invalid_user'] + escape_markdown_v2(f" \\(لم يتم العثور على {user_input}\\) "), parse_mode=ParseMode.MARKDOWN_V2)
                return ConversationStates.ADMIN_UNBAN_USER
        else:
            try:
                target_user_id = int(user_input)
                target_user_info = await get_user_info(context, target_user_id)
                if target_user_info['username'] == "غير معروف":
                     logger.warning(f"Could not get valid info for user ID {target_user_id} during unban.")
            except ValueError:
                await send_formatted_message(update, context, bot_messages["invalid_user"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
                return ConversationStates.ADMIN_UNBAN_USER
            except TelegramError as e:
                 logger.warning(f"Error fetching user info for ID {target_user_id} during unban: {e}")
                 await send_formatted_message(update, context, escape_markdown_v2(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم ID `{target_user_id}`. يرجى المحاولة مرة أخرى."), parse_mode=ParseMode.MARKDOWN_V2)
                 return ConversationStates.ADMIN_UNBAN_USER

        if target_user_id and target_user_info:
            banned_users = get_banned_users(context)
            if target_user_id in banned_users:
                banned_users.remove(target_user_id)
                await update_persistence(context) # Save changes

                user_display = f"{target_user_info['name']} \\({target_user_info['username']}, ID: `{target_user_id}`\\)"
                await send_formatted_message(update, context, bot_messages["user_unbanned_success"].format(user=user_display), parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

                # Send notification to the unbanned user
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=bot_messages["user_unbanned"],
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except TelegramError as e:
                    logger.error(f"Error sending unban message to user {target_user_id}: {e}")
                    # Don"t bother admin, just log
            else:
                await send_formatted_message(update, context, escape_markdown_v2("⚠️ هذا المستخدم ليس محظوراً."), parse_mode=ParseMode.MARKDOWN_V2)

            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Admin unban error: {e}", exc_info=True)
        await send_formatted_message(update, context, bot_messages["error_occurred"], parse_mode=None)
        return ConversationHandler.END

async def admin_get_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks admin for the broadcast message content."""
    query = update.callback_query
    await query.answer()
    message_text = escape_markdown_v2("📢 أرسل الرسالة التي تريد بثها لجميع المشتركين النشطين. يمكنك استخدام تنسيق Markdown V2 (مثل *bold*, _italic_, `code`, [link](url)). سيتم إرسال الرسالة كما هي.")
    await query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationStates.ADMIN_BROADCAST

async def admin_confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirms the broadcast message with the admin."""
    # Get the raw text and entities to preserve admin"s formatting
    message_text = update.message.text
    entities = update.message.entities
    context.user_data["broadcast_message_text"] = message_text
    context.user_data["broadcast_message_entities"] = entities

    vip_users = get_vip_users(context)
    active_vips_count = len([uid for uid in vip_users if is_vip(context, uid)])

    if active_vips_count == 0:
        await send_formatted_message(update, context, bot_messages["no_vips"] + escape_markdown_v2(" لا يمكن إرسال البث."), parse_mode=ParseMode.MARKDOWN_V2)
        context.user_data.pop("broadcast_message_text", None)
        context.user_data.pop("broadcast_message_entities", None)
        return ConversationHandler.END

    # Show preview (send the message back to admin as they formatted it)
    preview_header = f"*معاينة رسالة البث* \\(سيتم إرسالها كما هي إلى *{active_vips_count}* مشترك نشط\\):\\n\\n"
    await update.message.reply_text(preview_header, parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text(message_text, entities=entities) # Send the actual message back

    confirmation_text = "\\n\\nهل أنت متأكد من الإرسال؟"
    buttons = [
        [InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="admin_send_broadcast_now")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="admin_cancel_broadcast")]
    ]
    markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(confirmation_text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)

    return ConversationStates.ADMIN_CONFIRM_BROADCAST

async def admin_send_broadcast_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the broadcast message to all active VIP users."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ جارٍ إرسال الإعلان للمشتركين\\.\\.\\.", parse_mode=None)

    message_text = context.user_data.get("broadcast_message_text")
    entities = context.user_data.get("broadcast_message_entities")

    if not message_text:
        await query.edit_message_text(escape_markdown_v2("⚠️ خطأ: لم يتم العثور على رسالة البث. يرجى البدء من جديد /admin"), parse_mode=ParseMode.MARKDOWN_V2)
        return ConversationHandler.END

    vip_users = get_vip_users(context)
    active_vip_ids = [uid for uid in vip_users if is_vip(context, uid)]

    success_count = 0
    failed_count = 0
    send_tasks = []

    # Prepare send tasks
    for user_id in active_vip_ids:
        # Add the broadcast header
        full_message_text = bot_messages["broadcast_template"].format(message="") + message_text
        # Adjust entities for the added header length
        header_len = len(bot_messages["broadcast_template"].format(message=""))
        adjusted_entities = None
        if entities:
             adjusted_entities = [entity.shift(header_len) for entity in entities]

        send_tasks.append(context.bot.send_message(
            chat_id=user_id,
            text=full_message_text,
            entities=adjusted_entities,
            disable_web_page_preview=True # Disable previews for broadcasts
        ))

    # Send concurrently with rate limiting
    delay_between_sends = 0.1 # 10 messages per second approx
    results = []
    for task in send_tasks:
        try:
            result = await task
            results.append(result)
            success_count += 1
        except TelegramError as e:
            logger.warning(f"Failed to send broadcast to user {task.cr_frame.f_locals['chat_id']}: {e}")
            results.append(e)
            failed_count += 1
        except Exception as e:
             logger.error(f"Unexpected error during broadcast send: {e}", exc_info=True)
             results.append(e)
             failed_count += 1
        await asyncio.sleep(delay_between_sends) # Rate limit

    result_message = bot_messages["broadcast_sent"].format(success=success_count, failed=failed_count)
    await query.edit_message_text(result_message, parse_mode=ParseMode.MARKDOWN_V2)

    context.user_data.pop("broadcast_message_text", None)
    context.user_data.pop("broadcast_message_entities", None)
    return ConversationHandler.END

async def admin_cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the broadcast operation."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(escape_markdown_v2("✅ تم إلغاء إرسال الإعلان."), parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data.pop("broadcast_message_text", None)
    context.user_data.pop("broadcast_message_entities", None)
    return ConversationHandler.END

async def admin_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler to go back to the main admin menu."""
    # This essentially calls the admin_menu function again
    return await admin_menu(update, context)

# ---------------------- معالجات Callback Query الأخرى ----------------------
async def handle_plan_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user selecting a subscription plan."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    plan_name = query.data.split("_")[1]
    if plan_name in SUBSCRIPTION_PLANS:
        plan_info = SUBSCRIPTION_PLANS[plan_name]
        confirm_text = bot_messages["confirm_plan"].format(
            plan=escape_markdown_v2(plan_name.capitalize()),
            price=escape_markdown_v2(plan_info['price_str']),
            days=plan_info['days']
        )
        buttons = [
            [InlineKeyboardButton("✅ نعم، تأكيد الطلب", callback_data=f"confirm_{plan_name}")],
            [InlineKeyboardButton("❌ لا، إلغاء", callback_data="cancel_plan")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(confirm_text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await query.edit_message_text(escape_markdown_v2("⚠️ خطة غير صالحة."), parse_mode=ParseMode.MARKDOWN_V2)

async def handle_plan_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user confirming a subscription plan request."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    plan_name = query.data.split("_")[1]
    if plan_name in SUBSCRIPTION_PLANS:
        pending_payments = get_pending_payments(context)
        # Store pending payment with current timestamp
        pending_payments[user_id] = {"plan": plan_name, "payment_date": datetime.datetime.now()}
        await update_persistence(context) # Save pending payment

        await query.edit_message_text(bot_messages["payment_success"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)

        # Notify admin about the new pending payment
        try:
            user_info = await get_user_info(context, user_id)
            admin_notification = (
                f"🔔 *طلب اشتراك جديد معلق* 🔔\\n\\n"
                f"👤 المستخدم: {user_info['name']} \\({user_info['username']}, ID: `{user_id}`\\)\\n"
                f"📦 الخطة المطلوبة: *{escape_markdown_v2(plan_name.capitalize())}*\\n"
                f"💰 السعر: *{escape_markdown_v2(SUBSCRIPTION_PLANS[plan_name]['price_str'])}*\\n\\n"
                f"يرجى التواصل مع المستخدم وتفعيل الاشتراك يدوياً عبر أمر `/admin` بعد تأكيد الدفع\\."
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_notification, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Failed to send pending payment notification to admin: {e}")

    else:
        await query.edit_message_text(escape_markdown_v2("⚠️ خطة غير صالحة."), parse_mode=ParseMode.MARKDOWN_V2)

async def handle_plan_cancellation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user cancelling a plan selection."""
    query = update.callback_query
    await query.answer()
    # Go back to the initial start message/menu
    # Re-sending the start message might be complex, maybe just a confirmation?
    await query.edit_message_text(escape_markdown_v2("✅ تم إلغاء طلب الاشتراك."), parse_mode=ParseMode.MARKDOWN_V2)
    # Optionally, show the main menu again after a delay or with a button
    # await asyncio.sleep(2)
    # return await start(update, context) # This might not work correctly with query edits

async def handle_free_trial_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows remaining free trial messages."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    free_trial_users = get_free_trial_users(context)
    remaining = free_trial_users.get(user_id, 0)
    info_message = bot_messages["trial_info"].format(remaining=remaining, total=MAX_FREE_MESSAGES)
    await query.edit_message_text(info_message, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_to_start")]])) # Add back button

async def handle_request_free_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user explicitly requesting a free trial."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if is_banned(context, user_id):
        await send_formatted_message(update, context, bot_messages["user_banned"], parse_mode=ParseMode.MARKDOWN_V2, is_response_formatted=True)
        return

    free_trial_users = get_free_trial_users(context)
    if user_id not in free_trial_users and not is_vip(context, user_id):
        free_trial_users[user_id] = MAX_FREE_MESSAGES
        await update_persistence(context)
        logger.info(f"Granted {MAX_FREE_MESSAGES} free messages to user {user_id} via request button.")
        await query.edit_message_text(
            f"✅ تم منحك *{MAX_FREE_MESSAGES}* رسائل مجانية\\! استمتع بالتجربة\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_to_start")]])
        )
    elif is_vip(context, user_id):
         await query.answer("أنت مشترك بالفعل، لا تحتاج لتجربة مجانية!", show_alert=True)
    else:
        # User already had a trial (even if 0 messages left)
        remaining = free_trial_users.get(user_id, 0)
        if remaining > 0:
             await query.answer(f"لديك بالفعل {remaining} رسائل مجانية متبقية.", show_alert=True)
        else:
             await query.answer("لقد استنفدت رسائلك المجانية بالفعل.", show_alert=True)

async def back_to_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback to show the initial start menu again after editing a message."""
    query = update.callback_query
    if query:
        await query.answer()
        # We need to effectively call the start command logic again,
        # replacing the current message content.
        # Create a dummy Update object that looks like a command
        dummy_message = query.message.reply_text # Use reply_text to get a Message object to modify
        dummy_message.text = "/start"
        dummy_message.entities = []
        dummy_update = Update(update.update_id, message=dummy_message)
        dummy_update.effective_user = query.from_user # Ensure user context is correct
        dummy_update.effective_chat = query.message.chat # Ensure chat context is correct

        # Delete the old message with buttons
        try:
            await query.delete_message()
        except TelegramError as e:
            logger.warning(f"Could not delete message before showing start menu again: {e}")

        # Call the start handler
        await start(dummy_update, context)
    else:
        # If somehow called without a query, just log it
        logger.warning("back_to_start_callback called without a query.")

# ---------------------- إعداد التطبيق والتشغيل ----------------------
async def post_init(application: Application):
    """Post-initialization tasks, like initializing bot_data."""
    initialize_bot_data(application)
    # Initialize the shared aiohttp session
    await get_aiohttp_session()

async def on_shutdown(application: Application):
    """Tasks to run on shutdown, like closing sessions and saving data."""
    logger.info("Bot is shutting down. Closing aiohttp session and flushing persistence...")
    await close_aiohttp_session()
    # Ensure final data is saved
    if application.persistence:
        await application.persistence.flush()
        logger.info("Persistence data flushed on shutdown.")

def main():
    """Starts the bot."""
    # Create persistence object
    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)

    # Create the Application and pass it the bot"s token and persistence.
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .post_init(post_init) # Run after persistence is loaded
        .post_shutdown(on_shutdown) # Run before exiting
        .concurrent_updates(True) # Enable concurrent handling
        .build()
    )

    # --- Admin Conversation Handler ---
    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_menu), CallbackQueryHandler(admin_menu, pattern="^admin_back_to_menu$")],
        states={
            ConversationStates.ADMIN_MENU: [
                CallbackQueryHandler(admin_activate_plan_select, pattern="^admin_activate$"),
                CallbackQueryHandler(admin_get_user_for_deactivation, pattern="^admin_deactivate$"),
                CallbackQueryHandler(admin_list_vips, pattern="^admin_list_vips$"),
                CallbackQueryHandler(admin_stats_display, pattern="^admin_stats$"),
                CallbackQueryHandler(admin_get_user_for_ban, pattern="^admin_ban$"),
                CallbackQueryHandler(admin_get_user_for_unban, pattern="^admin_unban$"),
                CallbackQueryHandler(admin_get_broadcast_message, pattern="^admin_broadcast$"),
                # Add handler for unexpected callbacks in this state
                CallbackQueryHandler(admin_menu, pattern="^.*$") # Go back to menu if unknown button pressed
            ],
            ConversationStates.ADMIN_ACTIVATE_PLAN: [
                CallbackQueryHandler(admin_get_user_for_activation, pattern="^admin_plan_"),
                CallbackQueryHandler(admin_back_to_menu, pattern="^admin_back_to_menu$"),
            ],
            ConversationStates.ADMIN_ACTIVATE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_activate_user_confirm)
            ],
            ConversationStates.ADMIN_DEACTIVATE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_deactivate_user_confirm)
            ],
            ConversationStates.ADMIN_BAN_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_user_confirm)
            ],
            ConversationStates.ADMIN_UNBAN_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_user_confirm)
            ],
            ConversationStates.ADMIN_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_confirm_broadcast)
            ],
            ConversationStates.ADMIN_CONFIRM_BROADCAST: [
                CallbackQueryHandler(admin_send_broadcast_now, pattern="^admin_send_broadcast_now$"),
                CallbackQueryHandler(admin_cancel_broadcast, pattern="^admin_cancel_broadcast$"),
            ],
        },
        fallbacks=[CommandHandler("admin", admin_menu), CommandHandler("cancel", admin_cancel_broadcast)], # Allow restarting or cancelling
        persistent=False, # Do not persist conversation state across restarts
        name="admin_conversation",
        allow_reentry=True
    )

    # --- Command Handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("features", show_features))
    application.add_handler(CommandHandler("info", subscription_info_command))
    application.add_handler(CommandHandler("adminhelp", admin_help_command))
    application.add_handler(CommandHandler("stats", admin_stats_display)) # Allow /stats directly for admin

    # --- Admin Conversation Handler ---
    application.add_handler(admin_conv_handler)

    # --- Callback Query Handlers (outside conversation) ---
    application.add_handler(CallbackQueryHandler(show_features, pattern="^show_features$"))
    application.add_handler(CallbackQueryHandler(handle_plan_selection, pattern="^plan_"))
    application.add_handler(CallbackQueryHandler(handle_plan_confirmation, pattern="^confirm_"))
    application.add_handler(CallbackQueryHandler(handle_plan_cancellation, pattern="^cancel_plan$"))
    application.add_handler(CallbackQueryHandler(handle_free_trial_info, pattern="^free_trial_info$"))
    application.add_handler(CallbackQueryHandler(handle_request_free_trial, pattern="^request_free_trial$"))
    application.add_handler(CallbackQueryHandler(back_to_start_callback, pattern="^back_to_start$")) # Handle back button

    # --- Message Handler (must be last) ---
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # --- Error Handler ---
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

