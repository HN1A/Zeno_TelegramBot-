import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from telegram.error import TelegramError
import datetime
from typing import Dict, Any
import asyncio
import aiosqlite
import pytz
from queue import Queue
import nest_asyncio
import warnings
import requests
import json
import os
import base64
import re

# تجاهل تحذيرات PTBUserWarning حول per_message=False
warnings.filterwarnings("ignore", category=UserWarning, module="telegram.ext")

# تطبيق nest_asyncio للسماح بحلقات أحداث متداخلة في بيئات مثل Pydroid 3
nest_asyncio.apply()

# 🔧 إعدادات التسجيل
logging.basicConfig(
    format='🕒 %(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 🔑 إعدادات البوت
TOKEN = '8089095646:AAHE2mKVozLUgQKH-emeFuJ9hNH1WDmaWXo'
ADMIN_IDS = [7091341079]  # استبدل بأيدي المستخدمين المشرفين
WELCOME_IMAGE_URL = 'https://i.postimg.cc/4NKRKqx0/file-00000000797861f889786c5f43bfb69a.png'
BOT_USERNAME = 'ZenoGPT'
REQUIRE_ACTIVATION = True  # إعداد متطلب التفعيل الافتراضي

# 🗂 إعدادات قاعدة البيانات
DB_NAME = 'zenobot.db'
TRAINING_DATA_FILE = 'training_data.json'

# 🔑 إعدادات واجهات برمجة التطبيقات للذكاء الاصطناعي
OPENROUTER_API_KEY = "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"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_KEY = "AIzaSyDV1Hwzgo6HaUctAch0B6qzXZ8ujr14jIM"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"

# 🚦 حالات المحادثة
REQUESTING_ACTIVATION, BROADCASTING, TICKET_HANDLING, ACTIVATE_USER = range(4)

# 🎚 مستويات المستخدمين
USER_LEVELS = {
    0: "👶 مبتدئ",
    1: "👤 مستخدم عادي",
    2: "⭐ مستخدم متميز",
    3: "👑 مستخدم VIP",
    10: "🛡 مساعد أدمن",
    99: "👨‍💼 أدمن",
    100: "🕴️ مالك البوت"
}

# 🔥 تهيئة قاعدة البيانات
async def init_db():
    """
    تهيئة قاعدة بيانات SQLite وإنشاء الجداول اللازمة إذا لم تكن موجودة
    """
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_active BOOLEAN DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            join_date TEXT,
            last_seen TEXT,
            level INTEGER DEFAULT 1,
            points INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS activation_requests (
            chat_id INTEGER PRIMARY KEY,
            request_time TEXT,
            FOREIGN KEY(chat_id) REFERENCES users(chat_id)
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            subject TEXT,
            message TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            FOREIGN KEY(chat_id) REFERENCES users(chat_id)
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            date TEXT PRIMARY KEY,
            active_users INTEGER,
            new_users INTEGER,
            messages_processed INTEGER
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS ai_conversations (
            chat_id INTEGER PRIMARY KEY,
            conversation_history TEXT,
            last_interaction TEXT,
            FOREIGN KEY(chat_id) REFERENCES users(chat_id)
        )
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            chat_id INTEGER,
            file_type TEXT,
            file_name TEXT,
            file_size INTEGER,
            saved_path TEXT,
            upload_date TEXT,
            FOREIGN KEY(chat_id) REFERENCES users(chat_id)
        )
        ''')
        
        await db.commit()

    # تهيئة ملف بيانات التدريب إذا لم يكن موجودًا
    if not os.path.exists(TRAINING_DATA_FILE):
        with open(TRAINING_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({"conversations": []}, f, ensure_ascii=False, indent=2)

# 🔄 حفظ المحادثة في بيانات التدريب
def save_to_training_data(user_message: str, bot_response: str, user_id: int):
    """
    حفظ المحادثة في ملف بيانات التدريب
    """
    try:
        with open(TRAINING_DATA_FILE, 'r+', encoding='utf-8') as f:
            data = json.load(f)
            data["conversations"].append({
                "user_id": user_id,
                "timestamp": datetime.datetime.now().isoformat(),
                "user_message": user_message,
                "bot_response": bot_response
            })
            f.seek(0)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.truncate()
    except Exception as e:
        logger.error(f"خطأ في حفظ بيانات التدريب: {e}")

async def process_image_with_ai(file_id: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    """
    معالجة الصورة باستخدام الذكاء الاصطناعي
    """
    try:
        # الحصول على ملف الصورة من تلجرام
        file = await context.bot.get_file(file_id)
        file_url = file.file_path
        
        # تحميل الصورة وتحويلها إلى base64
        response = requests.get(file_url)
        if response.status_code != 200:
            return "⚠️ تعذر تحميل الصورة للتحليل"
        
        image_base64 = base64.b64encode(response.content).decode('utf-8')
        
        # استخدام Gemini لتحليل الصورة (يدعم الصور)
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": "قم بتحليل هذه الصورة ووصف محتواها."},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_base64
                        }
                    }
                ]
            }]
        }
        
        params = {'key': GEMINI_API_KEY}
        response = requests.post(GEMINI_URL, headers=headers, params=params, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            return "⚠️ تعذر تحليل الصورة باستخدام الذكاء الاصطناعي"
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الصورة: {e}")
        return "⚠️ حدث خطأ أثناء معالجة الصورة"

async def process_file_with_ai(file_id: str, file_name: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> str:
    """
    معالجة الملفات باستخدام الذكاء الاصطناعي
    """
    try:
        # الحصول على الملف من تلجرام
        file = await context.bot.get_file(file_id)
        file_url = file.file_path
        
        # تحميل الملف
        response = requests.get(file_url)
        if response.status_code != 200:
            return "⚠️ تعذر تحميل الملف للتحليل"
        
        # قراءة محتوى الملف (للملفات النصية)
        file_content = response.text
        
        # استخدام الذكاء الاصطناعي لتحليل الملف
        prompt = f"قم بتحليل محتوى الملف {file_name}:\n\n{file_content[:5000]}\n\n... [محتوى مختصر]"
        
        return await get_ai_response(prompt, chat_id)
        
    except Exception as e:
        logger.error(f"خطأ في معالجة الملف: {e}")
        return "⚠️ حدث خطأ أثناء معالجة الملف"

def format_code_blocks(text: str) -> str:
    """
    تحسين تنسيق كتل الأكواد البرمجية في النص
    """
    if '```' not in text:
        return text
    
    # تقسيم النص إلى أجزاء
    parts = text.split('```')
    result = []
    
    for i, part in enumerate(parts):
        if i % 2 == 1:  # هذا جزء من كود
            # تحديد لغة البرمجة إذا كانت محددة
            if '\n' in part:
                lang, code = part.split('\n', 1)
                lang = lang.strip()
                if not lang:
                    lang = 'text'
            else:
                lang = 'text'
                code = part
            
            # إضافة تنسيق الكود
            result.append(f"<pre><code class='language-{lang}'>{code}</code></pre>")
        else:
            result.append(part)
    
    return ''.join(result)

# 🤖 وظائف استجابة الذكاء الاصطناعي مع تنسيق الكود
async def get_ai_response(prompt: str, chat_id: int) -> str:
    """
    الحصول على رد من واجهة برمجة التطبيقات للذكاء الاصطناعي (OpenRouter أو Gemini) مع تنسيق صحيح للكود
    """
    try:
        # التحقق مما إذا كانت هذه تحية لعرض رسالة الترحيب
        greeting_keywords = [
            'مرحبا', 'أهلاً', 'السلام عليكم', 'ابدأ', 'تشغيل', 'من أنت', 'من مطورك', 'من صنعك', 'تعريف', 'تقديم',
            'hello', 'hi', 'start', 'begin', 'who are you', 'who made you', 'introduction', 'info', 'welcome', 'launch'
        ]
        
        if any(keyword in prompt.lower() for keyword in greeting_keywords):
            return (
                "مرحبًا بك في زِينو جي بي تي بوت!\n\n"
                "أنا هنا لمساعدتك والإجابة على أسئلتك بكل احترافية وذكاء.\n"
                "تم تطويري على يد المطور المبدع آلمهَيَّب.\n"
                "استعد لتجربة تفاعلية مميزة مدعومة بالذكاء الاصطناعي.\n\n"
                "---\n\n"
                "Welcome to ZenoGPTbot!\n\n"
                "I'm here to assist you and answer your questions with intelligence and precision.\n"
                "I was proudly developed by the talented creator Almuhaib.\n"
                "Get ready for a smart and engaging AI experience."
            )
        
        # تجربة OpenRouter أولاً مع نموذج gpt-3.5-turbo
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "openai/gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}]
        }
        
        response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            response_text = result['choices'][0]['message']['content']
            
            # تحسين تنسيق كتل الكود
            response_text = format_code_blocks(response_text)
            return response_text
        
        # إذا فشل OpenRouter، جرب Gemini
        params = {'key': GEMINI_API_KEY}
        gemini_payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        response = requests.post(GEMINI_URL, params=params, json=gemini_payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            response_text = result['candidates'][0]['content']['parts'][0]['text']
            
            # تحسين تنسيق كتل الكود
            response_text = format_code_blocks(response_text)
            return response_text
        
        return "⚠️ عذرًا، حدث خطأ في معالجة طلبك. يرجى المحاولة مرة أخرى لاحقًا."
    
    except Exception as e:
        logger.error(f"خطأ في الحصول على رد الذكاء الاصطناعي: {e}")
        return "⚠️ عذرًا، حدث خطأ في معالجة طلبك. يرجى المحاولة مرة أخرى لاحقًا."

# 💬 معالجة رسائل المستخدم مع الذكاء الاصطناعي
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    معالجة الرسائل العادية من المستخدمين والرد باستخدام الذكاء الاصطناعي
    """
    if not update.message:
        return

    chat_id = update.effective_chat.id
    
    # إذا كانت الرسالة تحتوي على صورة
    if update.message.photo:
        photo = update.message.photo[-1]  # الحصول على أعلى دقة للصورة
        try:
            # عرض إجراء الكتابة
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            
            # تحليل الصورة باستخدام الذكاء الاصطناعي
            analysis_result = await process_image_with_ai(photo.file_id, context, chat_id)
            
            # حفظ معلومات الصورة في قاعدة البيانات (باستخدام INSERT OR IGNORE لتجنب الأخطاء عند التكرار)
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    'INSERT OR IGNORE INTO files (file_id, chat_id, file_type, file_size, upload_date) VALUES (?, ?, ?, ?, ?)',
                    (photo.file_id, chat_id, 'photo', photo.file_size, datetime.datetime.now().isoformat())
                )
                await db.commit()
            
            # إرسال نتيجة التحليل
            await update.message.reply_text(analysis_result, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"خطأ في معالجة الصورة: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة صورتك. يرجى المحاولة مرة أخرى.")
        return
    
    # إذا كانت الرسالة تحتوي على ملف
    elif update.message.document:
        document = update.message.document
        try:
            # عرض إجراء الكتابة
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            
            # تحليل الملف باستخدام الذكاء الاصطناعي
            analysis_result = await process_file_with_ai(document.file_id, document.file_name, context, chat_id)
            
            # حفظ معلومات الملف في قاعدة البيانات (باستخدام INSERT OR IGNORE لتجنب الأخطاء عند التكرار)
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    'INSERT OR IGNORE INTO files (file_id, chat_id, file_type, file_name, file_size, upload_date) VALUES (?, ?, ?, ?, ?, ?)',
                    (document.file_id, chat_id, 'document', document.file_name, document.file_size, datetime.datetime.now().isoformat())
                )
                await db.commit()
            
            # إرسال نتيجة التحليل
            await update.message.reply_text(analysis_result, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"خطأ في معالجة الملف: {e}")
            await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة ملفك. يرجى المحاولة مرة أخرى.")
        return
    
    # إذا كانت الرسالة نصية
    elif update.message.text:
        user_message = update.message.text
    else:
        await update.message.reply_text("⚠️ عذرًا، لا أستطيع معالجة هذا النوع من الرسائل.")
        return
    
    # التحقق مما إذا كان المستخدم نشطًا أو مشرفًا
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT is_active, is_banned FROM users WHERE chat_id = ?', (chat_id,))
            user_data = await cursor.fetchone()
            
            if not user_data:
                await update.message.reply_text("⏳ حسابك غير مسجل. يرجى استخدام /start لتسجيل حسابك.")
                return
                
            is_banned = user_data[1]
            if is_banned:
                await update.message.reply_text("🚫 حسابك محظور من استخدام البوت.")
                return
                
            if REQUIRE_ACTIVATION and not (user_data[0] or chat_id in ADMIN_IDS):
                await update.message.reply_text("⏳ حسابك غير مفعل بعد. يرجى انتظار تفعيل حسابك من قبل الإدارة.")
                return
    except Exception as e:
        logger.error(f"خطأ في التحقق من حالة المستخدم: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى.")
        return
    
    # عرض إجراء الكتابة
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    except Exception as e:
        logger.error(f"خطأ في إرسال إجراء الكتابة: {e}")
    
    # الحصول على رد الذكاء الاصطناعي
    try:
        bot_response = await get_ai_response(user_message, chat_id)
        
        # حفظ المحادثة في بيانات التدريب
        save_to_training_data(user_message, bot_response, chat_id)
        
        # تحديث سجل المحادثة في قاعدة البيانات
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
            INSERT OR REPLACE INTO ai_conversations (chat_id, conversation_history, last_interaction)
            VALUES (?, ?, ?)
            ''', (chat_id, json.dumps({"last_message": user_message, "last_response": bot_response}), 
                 datetime.datetime.now().isoformat()))
            await db.commit()
        
        # إرسال الرد مع التنسيق الصحيح
        try:
            await update.message.reply_text(bot_response, parse_mode='HTML')
        except TelegramError:
            # إذا فشل إرسال الرسالة بتنسيق HTML، حاول إرسالها كنص عادي
            await update.message.reply_text(bot_response)
        
    except Exception as e:
        logger.error(f"خطأ في معالجة الرسالة من {chat_id}: {e}")
        await update.message.reply_text("⚠️ عذرًا، حدث خطأ في معالجة طلبك. يرجى المحاولة مرة أخرى لاحقًا.")

# 🚀 أمر البدء
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    إرسال رسالة ترحيب مع أزرار التفعيل والدعم عند إصدار أمر /start
    """
    if not update.message:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # إضافة المستخدم إلى قاعدة البيانات إذا لم يكن موجودًا
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                'INSERT OR IGNORE INTO users (chat_id, username, first_name, last_name, join_date, last_seen) VALUES (?, ?, ?, ?, ?, ?)',
                (chat_id, user.username, user.first_name, user.last_name, 
                 datetime.datetime.now().isoformat(), datetime.datetime.now().isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"خطأ في تسجيل المستخدم: {e}")
    
    # التحقق مما إذا كان المستخدم مشرفًا
    if chat_id in ADMIN_IDS:
        # رسالة ترحيب للمشرف (لا حاجة للتفعيل)
        welcome_text = (
            "👑 **مرحباً بك أيها الأدمن!**\n\n"
            "🤖 أنت تستطيع استخدام جميع مميزات البوت مباشرة.\n\n"
            "💎 **مميزات البوت:**\n"
            "- إجابة على أسئلتك بدقة عالية\n"
            "- دعم متعدد اللغات\n"
            "- واجهة سهلة الاستخدام\n"
            "- تحديثات مستمرة\n\n"
            "⚡ استخدم /adminhelp لرؤية الأوامر الإدارية."
        )
        
        keyboard = [
            [InlineKeyboardButton("❓ الدعم الفني", callback_data='support_ticket')],
            [InlineKeyboardButton("🛠 لوحة التحكم", callback_data='admin_panel')]
        ]
    else:
        # رسالة ترحيب للمستخدم العادي
        welcome_text = (
            "🌟 **مرحباً بك في بوت الذكاء الاصطناعي ZenoGPT!**\n\n"
            "🤖 هذا البوت مصمم لمساعدتك في مختلف المهام باستخدام الذكاء الاصطناعي المتقدم.\n\n"
            "🔹 حالياً البوت تحت التدريب والاختبار.\n"
            f"🔹 {'سيتم تفعيل حسابك بعد مراجعة الطلب من قبل الإدارة.' if REQUIRE_ACTIVATION else 'يمكنك استخدام البوت مباشرة.'}\n\n"
            "💎 **مميزات البوت:**\n"
            "- إجابة على أسئلتك بدقة عالية\n"
            "- دعم متعدد اللغات\n"
            "- واجهة سهلة الاستخدام\n"
            "- تحديثات مستمرة\n\n"
            "⚡ استخدم الأزرار أدناه للبدء!"
        )
        
        keyboard = []
        if REQUIRE_ACTIVATION:
            # التحقق مما إذا كان المستخدم نشطًا بالفعل
            try:
                async with aiosqlite.connect(DB_NAME) as db:
                    cursor = await db.execute('SELECT is_active FROM users WHERE chat_id = ?', (chat_id,))
                    user_data = await cursor.fetchone()
                    
                    if not (user_data and user_data[0]):
                        keyboard.append([InlineKeyboardButton("📩 ارسال طلب تفعيل", callback_data='request_activation')])
            except Exception as e:
                logger.error(f"خطأ في التحقق من حالة التفعيل: {e}")
        
        keyboard.append([InlineKeyboardButton("❓ الدعم الفني", callback_data='support_ticket')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إرسال الصورة مع التسمية والزر
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=welcome_text,
            reply_markup=reply_markup,
            parse_mode=None
        )
    except TelegramError as e:
        logger.error(f"فشل في إرسال صورة الترحيب إلى {chat_id}: {e}")
        # إذا فشل إرسال الصورة، أرسل الرسالة بدون صورة
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=reply_markup,
            parse_mode=None
        )
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال صورة الترحيب إلى {chat_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=reply_markup,
            parse_mode=None
        )

# 🆘 أمر المساعدة للمستخدمين
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    عرض رسالة المساعدة للمستخدمين العاديين.
    """
    if not update.message:
        return

    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كان المستخدم نشطًا أو مشرفًا
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT is_active FROM users WHERE chat_id = ?', (chat_id,))
            user_data = await cursor.fetchone()
            
            is_active = user_data and user_data[0]
            is_admin = chat_id in ADMIN_IDS
            
            if not is_active and not is_admin:
                await update.message.reply_text(
                    "⏳ حسابك غير مفعل بعد. يرجى انتظار تفعيل حسابك من قبل الإدارة."
                )
                return
    except Exception as e:
        logger.error(f"خطأ في التحقق من حالة المستخدم: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى.")
        return
    
    help_text = (
        "🆘 **كيفية استخدام بوت ZenoGPT**\n\n"
        "🔹 يمكنك التحدث مع البوت مباشرة وسيقوم بالرد عليك.\n"
        "🔹 استخدم /help لعرض هذه الرسالة.\n"
        "🔹 استخدم /support لفتح تذكرة دعم فني.\n\n"
        "💡 **نصائح للاستخدام الأمثل:**\n"
        "- اكتب أسئلتك بوضوح\n"
        "- يمكنك استخدام البوت باللغة العربية أو الإنجليزية\n"
        "- البوت قادر على فهم السياق في المحادثة\n\n"
        "🚀 استمتع بتجربة الذكاء الاصطناعي المتقدم!"
    )
    
    try:
        await update.message.reply_text(help_text, parse_mode=None)
    except TelegramError:
        await update.message.reply_text(help_text)

# 🛟 أمر المساعدة للمشرفين
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    عرض رسالة المساعدة للمشرفين.
    """
    if not update.message:
        return

    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ ليس لديك صلاحية الوصول إلى هذه الأوامر!")
        return
    
    help_text = (
        "👨‍💼 **أوامر الأدمن**\n\n"
        "🔹 /admin - عرض لوحة التحكم الإدارية\n"
        "🔹 /adminhelp - عرض هذه الرسالة\n"
        "🔹 /stats - عرض إحصائيات البوت\n"
        "🔹 /broadcast - بث رسالة لجميع المستخدمين\n"
        "🔹 /training_data - الحصول على ملف التدريب\n\n"
        "🛠 **إدارة المستخدمين:**\n"
        "- يمكنك تفعيل/حظر المستخدمين من لوحة التحكم\n"
        "- يمكنك الرد على تذاكر الدعم الفني\n\n"
        "⚙️ **إعدادات البوت:**\n"
        "- يمكنك تفعيل/إلغاء وضع طلب التفعيل من لوحة التحكم\n\n"
        "📊 استخدم لوحة التحكم للإدارة الكاملة للبوت."
    )
    
    try:
        await update.message.reply_text(help_text, parse_mode=None)
    except TelegramError:
        await update.message.reply_text(help_text)

# 📩 طلب التفعيل
async def request_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    معالجة زر طلب التفعيل عن طريق إرسال طلب إلى المشرفين.
    """
    if not update.callback_query:
        return
    
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كان نشطًا بالفعل
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT is_active FROM users WHERE chat_id = ?', (chat_id,))
            user_data = await cursor.fetchone()
            
            if user_data and user_data[0]:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="✅ حسابك مفعل بالفعل!"
                )
                return
    except Exception as e:
        logger.error(f"خطأ في التحقق من حالة التفعيل: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى."
        )
        return
    
    # التحقق مما إذا كان قد طلب بالفعل
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT 1 FROM activation_requests WHERE chat_id = ?', (chat_id,))
            if await cursor.fetchone():
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🔄 لديك طلب تفعيل قيد المراجعة بالفعل!"
                )
                return
    except Exception as e:
        logger.error(f"خطأ في التحقق من طلبات التفعيل: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى."
        )
        return
    
    # تسجيل طلب التفعيل
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                'INSERT INTO activation_requests (chat_id, request_time) VALUES (?, ?)',
                (chat_id, datetime.datetime.now().isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"خطأ في تسجيل طلب التفعيل: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ حدث خطأ أثناء معالجة طلبك. يرجى المحاولة مرة أخرى."
        )
        return
    
    # إعلام المستخدم
    await context.bot.send_message(
        chat_id=chat_id,
        text="📬 تم إرسال طلب التفعيل إلى الإدارة"
    )
    
    # إرسال إشعار إلى جميع المشرفين
    request_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for admin_id in ADMIN_IDS:
        try:
            keyboard = [
                [
                    InlineKeyboardButton("✅ تفعيل", callback_data=f'activate_{chat_id}'),
                    InlineKeyboardButton("❌ رفض", callback_data=f'reject_{chat_id}'),
                    InlineKeyboardButton("⛔ حظر", callback_data=f'ban_{chat_id}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            admin_message = (
                "📩 **طلب تفعيل جديد**\n\n"
                f"👤 **الاسم:** {user.first_name} {user.last_name or ''}\n"
                f"📛 **اليوزر:** @{user.username or 'غير متوفر'}\n"
                f"🆔 **ايدي الحساب:** `{chat_id}`\n"
                f"⏰ **وقت الطلب:** {request_time}\n\n"
                "🔘 اختر أحد الخيارات:"
            )
            
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                reply_markup=reply_markup,
                parse_mode=None
            )
        except TelegramError as e:
            logger.error(f"فشل في إعلام المشرف {admin_id} بطلب التفعيل: {e}")
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إعلام المشرف {admin_id}: {e}")

# 🛡 معالج إجراءات المشرف
async def handle_admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    معالجة إجراءات المشرف مثل تفعيل أو رفض أو حظر المستخدمين.
    """
    if not update.callback_query:
        return ConversationHandler.END
        
    query = update.callback_query
    await query.answer()
    data = query.data
    admin_id = update.effective_user.id
    
    if admin_id not in ADMIN_IDS:
        await query.edit_message_text(text="⛔ ليس لديك صلاحية للقيام بهذا الإجراء!")
        return ConversationHandler.END
    
    if data == 'activate_user':
        # عرض طلبات التفعيل المعلقة أو مطالبة بمعرف المستخدم
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute('''
                    SELECT u.chat_id, u.first_name, u.last_name, u.username 
                    FROM activation_requests ar 
                    JOIN users u ON ar.chat_id = u.chat_id
                ''')
                requests = await cursor.fetchall()
            
            if not requests:
                await query.edit_message_text(
                    text="📭 لا توجد طلبات تفعيل معلقة.\n"
                         "📝 يمكنك إدخال معرف المستخدم (chat_id) يدويًا:"
                )
                context.user_data['awaiting_chat_id'] = True
                return ACTIVATE_USER
            
            keyboard = [
                [InlineKeyboardButton(
                    f"{row[1]} {row[2] or ''} (@{row[3] or 'غير متوفر'})",
                    callback_data=f'activate_{row[0]}'
                )] for row in requests
            ]
            keyboard.append([InlineKeyboardButton("📝 إدخال معرف يدويًا", callback_data='manual_activate')])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text="📩 **طلبات التفعيل المعلقة**\n\nاختر مستخدمًا لتفعيله:",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"خطأ في جلب طلبات التفعيل: {e}")
            await query.edit_message_text(text="⚠️ حدث خطأ أثناء جلب طلبات التفعيل.")
        return ConversationHandler.END
    
    elif data == 'manual_activate':
        await query.edit_message_text(
            text="📝 الرجاء إرسال معرف المستخدم (chat_id) لتفعيله:"
        )
        context.user_data['awaiting_chat_id'] = True
        return ACTIVATE_USER
    
    elif data.startswith('activate_'):
        try:
            chat_id = int(data.split('_')[1])
        except ValueError:
            await query.edit_message_text(text="❌ خطأ: معرف المستخدم غير صالح!")
            return ConversationHandler.END
        
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute('SELECT 1 FROM users WHERE chat_id = ?', (chat_id,))
                if not await cursor.fetchone():
                    await query.edit_message_text(text="❌ خطأ: المستخدم غير موجود!")
                    return ConversationHandler.END
                
                await db.execute('UPDATE users SET is_active = 1 WHERE chat_id = ?', (chat_id,))
                await db.execute('DELETE FROM activation_requests WHERE chat_id = ?', (chat_id,))
                await db.commit()
            
            # إعلام المستخدم
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎉 **تم تفعيل حسابك بنجاح!**\n\n"
                         "✅ يمكنك الآن استخدام جميع مميزات البوت.\n"
                         "💎 استخدم /help لرؤية الأوامر المتاحة.\n\n"
                         "شكراً لثقتك بنا! 🤗"
                )
            except TelegramError as e:
                logger.error(f"فشل في إعلام المستخدم {chat_id} بالتفعيل: {e}")
            except Exception as e:
                logger.error(f"خطأ غير متوقع في إعلام المستخدم {chat_id}: {e}")
            
            await query.edit_message_text(text=f"✅ تم تفعيل الحساب {chat_id}")
        except Exception as e:
            logger.error(f"خطأ في تفعيل المستخدم: {e}")
            await query.edit_message_text(text="⚠️ حدث خطأ أثناء تفعيل الحساب.")
        return ConversationHandler.END
    
    elif data.startswith('reject_'):
        chat_id = int(data.split('_')[1])
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute('DELETE FROM activation_requests WHERE chat_id = ?', (chat_id,))
                await db.commit()
            
            await query.edit_message_text(text=f"❌ تم رفض طلب التفعيل للحساب {chat_id}")
        except Exception as e:
            logger.error(f"خطأ في رفض طلب التفعيل: {e}")
            await query.edit_message_text(text="⚠️ حدث خطأ أثناء رفض طلب التفعيل.")
        return ConversationHandler.END
    
    elif data.startswith('ban_'):
        chat_id = int(data.split('_')[1])
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute('UPDATE users SET is_banned = 1 WHERE chat_id = ?', (chat_id,))
                await db.commit()
            
            # إعلام المستخدم
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🚫 **تم حظر حسابك من استخدام البوت.**\n\n"
                         "إذا كنت تعتقد أن هذا خطأ، يمكنك التواصل مع الدعم الفني."
                )
            except TelegramError as e:
                logger.error(f"فشل في إعلام المستخدم المحظور {chat_id}: {e}")
            except Exception as e:
                logger.error(f"خطأ غير متوقع في إعلام المستخدم المحظور {chat_id}: {e}")
            
            await query.edit_message_text(text=f"🚫 تم حظر الحساب {chat_id}")
        except Exception as e:
            logger.error(f"خطأ في حظر المستخدم: {e}")
            await query.edit_message_text(text="⚠️ حدث خطأ أثناء حظر الحساب.")
        return ConversationHandler.END
    
    return ConversationHandler.END

# 📝 معالجة إدخال معرف المستخدم يدويًا للتفعيل
async def handle_manual_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    معالجة معرف المستخدم المدخل يدويًا من قبل المشرف لتفعيل المستخدم.
    """
    if not context.user_data.get('awaiting_chat_id') or not update.message:
        return ConversationHandler.END
    
    chat_id = update.message.text
    admin_id = update.effective_user.id
    
    try:
        chat_id = int(chat_id)
    except ValueError:
        await update.message.reply_text("❌ خطأ: يرجى إدخال معرف رقمي صالح!")
        return ConversationHandler.END
    
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT 1 FROM users WHERE chat_id = ?', (chat_id,))
            if not await cursor.fetchone():
                await update.message.reply_text("❌ خطأ: المستخدم غير موجود!")
                return ConversationHandler.END
            
            await db.execute('UPDATE users SET is_active = 1 WHERE chat_id = ?', (chat_id,))
            await db.execute('DELETE FROM activation_requests WHERE chat_id = ?', (chat_id,))
            await db.commit()
        
        # إعلام المستخدم
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎉 **تم تفعيل حسابك بنجاح!**\n\n"
                     "✅ يمكنك الآن استخدام جميع مميزات البوت.\n"
                     "💎 استخدم /help لرؤية الأوامر المتاحة.\n\n"
                     "شكراً لثقتك بنا! 🤗"
            )
        except TelegramError as e:
            logger.error(f"فشل في إعلام المستخدم {chat_id} بالتفعيل: {e}")
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إعلام المستخدم {chat_id}: {e}")
        
        await update.message.reply_text(f"✅ تم تفعيل الحساب {chat_id}")
    except Exception as e:
        logger.error(f"خطأ في تفعيل المستخدم يدويًا: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تفعيل الحساب.")
    
    context.user_data.pop('awaiting_chat_id', None)
    return ConversationHandler.END

# 🎛 أمر المشرف
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    عرض لوحة تحكم المشرف مع الإحصائيات وأزرار الإجراءات.
    """
    if not update.message:
        return

    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ ليس لديك صلاحية الوصول إلى لوحة التحكم!")
        return
    
    # الحصول على الإحصائيات
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
            active_users = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM users')
            total_users = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM activation_requests')
            pending_requests = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM tickets WHERE status = "open"')
            open_tickets = (await cursor.fetchone())[0]
    except Exception as e:
        logger.error(f"خطأ في جلب الإحصائيات: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب الإحصائيات.")
        return
    
    # نص الزر بناءً على متطلب التفعيل الحالي
    activation_btn_text = "❌ إلغاء وضع التفعيل" if REQUIRE_ACTIVATION else "✅ تفعيل وضع التفعيل"
    
    keyboard = [
        [InlineKeyboardButton("⛔ حظر مستخدم", callback_data='ban_user')],
        [InlineKeyboardButton("✅ تفعيل حساب", callback_data='activate_user')],
        [InlineKeyboardButton(activation_btn_text, callback_data='toggle_activation')],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='show_stats')],
        [
            InlineKeyboardButton("📢 إذاعة للمشتركين", callback_data='broadcast_active'),
            InlineKeyboardButton("📣 إذاعة للجميع", callback_data='broadcast_all')
        ],
        [InlineKeyboardButton("🎫 التذاكر المفتوحة", callback_data=f'list_tickets_{open_tickets}')],
        [InlineKeyboardButton("📁 ملف التدريب", callback_data='get_training_data')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    stats_text = (
        f"👥 **إجمالي المستخدمين:** {total_users}\n"
        f"✅ **المستخدمون النشطون:** {active_users}\n"
        f"🔄 **طلبات التفعيل المعلقة:** {pending_requests}\n"
        f"🎫 **التذاكر المفتوحة:** {open_tickets}\n"
        f"🔘 **وضع التفعيل:** {'مفعل' if REQUIRE_ACTIVATION else 'معطل'}"
    )
    
    try:
        await update.message.reply_text(
            f"🛠 **لوحة تحكم الأدمن**\n\n{stats_text}\n\n"
            "اختر أحد الخيارات:",
            reply_markup=reply_markup,
            parse_mode=None
        )
    except TelegramError as e:
        logger.error(f"فشل في إرسال لوحة التحكم إلى {user_id}: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال لوحة التحكم إلى {user_id}: {e}")

# 🔄 تبديل متطلب التفعيل
async def toggle_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    تبديل إعداد متطلب التفعيل.
    """
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    admin_id = update.effective_user.id
    
    if admin_id not in ADMIN_IDS:
        await query.edit_message_text(text="⛔ ليس لديك صلاحية للقيام بهذا الإجراء!")
        return
    
    global REQUIRE_ACTIVATION
    REQUIRE_ACTIVATION = not REQUIRE_ACTIVATION
    
    status = "مفعل" if REQUIRE_ACTIVATION else "معطل"
    await query.edit_message_text(text=f"✅ تم تغيير وضع التفعيل إلى: {status}")
    
    # تحديث لوحة التحكم
    try:
        await admin_command(update, context)
    except Exception as e:
        logger.error(f"خطأ في تحديث لوحة التحكم: {e}")

# 📊 عرض الإحصائيات
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    عرض إحصائيات البوت التفصيلية للمشرفين.
    """
    # معالجة كل من الأمر واستعلام رد الاتصال
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        message = query.message
    else:
        if not update.message:
            return
        chat_id = update.effective_chat.id
        message = update.message
    
    if chat_id not in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⛔ ليس لديك صلاحية الوصول إلى هذه المعلومات!"
        )
        return
    
    # الحصول على إحصائيات مفصلة
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # إحصائيات المستخدمين
            cursor = await db.execute('SELECT COUNT(*) FROM users')
            total_users = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
            active_users = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
            banned_users = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM activation_requests')
            pending_requests = (await cursor.fetchone())[0]
            
            # مستخدمون جدد اليوم
            today = datetime.datetime.now().date().isoformat()
            cursor = await db.execute('SELECT COUNT(*) FROM users WHERE date(join_date) = ?', (today,))
            new_users_today = (await cursor.fetchone())[0]
            
            # إحصائيات التذاكر
            cursor = await db.execute('SELECT COUNT(*) FROM tickets WHERE status = "open"')
            open_tickets = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM tickets WHERE status = "closed"')
            closed_tickets = (await cursor.fetchone())[0]
    except Exception as e:
        logger.error(f"خطأ في جلب الإحصائيات التفصيلية: {e}")
        error_msg = "⚠️ حدث خطأ أثناء جلب الإحصائيات."
        if update.callback_query and query.message:
            await query.message.reply_text(error_msg)
        elif update.message:
            await update.message.reply_text(error_msg)
        return
    
    stats_text = (
        "📊 **إحصائيات البوت التفصيلية**\n\n"
        "👥 **المستخدمون:**\n"
        f"- إجمالي المستخدمين: {total_users}\n"
        f"- المستخدمون النشطون: {active_users}\n"
        f"- المستخدمون المحظورون: {banned_users}\n"
        f"- طلبات التفعيل المعلقة: {pending_requests}\n"
        f"- مستخدمون جدد اليوم: {new_users_today}\n\n"
        "🎫 **التذاكر:**\n"
        f"- التذاكر المفتوحة: {open_tickets}\n"
        f"- التذاكر المغلقة: {closed_tickets}\n\n"
        f"⚙️ **إعدادات البوت:**\n"
        f"- وضع التفعيل: {'مفعل' if REQUIRE_ACTIVATION else 'معطل'}"
    )
    
    try:
        if update.callback_query:
            await query.edit_message_text(
                text=stats_text,
                parse_mode=None
            )
        else:
            await message.reply_text(
                text=stats_text,
                parse_mode=None
            )
    except TelegramError as e:
        logger.error(f"فشل في إرسال الإحصائيات إلى المشرف {chat_id}: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال الإحصائيات إلى المشرف {chat_id}: {e}")

# 🎫 نظام تذاكر الدعم
async def support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    بدء عملية إنشاء تذكرة دعم.
    """
    if not update.callback_query:
        return ConversationHandler.END
        
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎫 **نظام التذاكر الفنية**\n\n"
                 "📝 الرجاء إرسال رسالتك التي تحتوي على:\n"
                 "1. وصف المشكلة\n"
                 "2. أي رسائل خطأ تظهر لك\n"
                 "3. ما الذي كنت تحاول القيام به عند حدوث المشكلة\n\n"
                 "سيقوم أحد ممثلي الدعم بالرد عليك في أقرب وقت ممكن.",
            parse_mode=None
        )
    except TelegramError as e:
        logger.error(f"فشل في إرسال مطالبة التذكرة إلى {chat_id}: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال مطالبة التذكرة إلى {chat_id}: {e}")
    
    context.user_data['creating_ticket'] = True
    return TICKET_HANDLING

async def handle_ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    معالجة وتخزين رسالة تذكرة الدعم من المستخدم.
    """
    if not context.user_data.get('creating_ticket') or not update.message:
        return ConversationHandler.END
    
    chat_id = update.effective_chat.id
    message_text = update.message.text
    
    # حفظ التذكرة في قاعدة البيانات
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                'INSERT INTO tickets (chat_id, message, created_at) VALUES (?, ?, ?)',
                (chat_id, message_text, datetime.datetime.now().isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"خطأ في حفظ التذكرة: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء حفظ تذكرتك. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END
    
    # إعلام المشرفين
    for admin_id in ADMIN_IDS:
        try:
            keyboard = [
                [InlineKeyboardButton("📩 الرد على التذكرة", callback_data=f'reply_ticket_{chat_id}')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🎫 **تذكرة دعم جديدة**\n\n"
                     f"👤 المستخدم: {update.effective_user.mention_markdown()}\n"
                     f"🆔 ID: `{chat_id}`\n\n"
                     f"📝 الرسالة:\n{message_text}",
                reply_markup=reply_markup,
                parse_mode=None
            )
        except TelegramError as e:
            logger.error(f"فشل في إرسال التذكرة إلى المشرف {admin_id}: {e}")
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إرسال التذكرة إلى المشرف {admin_id}: {e}")
    
    try:
        await update.message.reply_text(
            "✅ **تم استلام تذكرتك بنجاح!**\n\n"
            "سيتم الرد عليك في أقرب وقت ممكن. شكراً لصبرك.",
            parse_mode=None
        )
    except TelegramError as e:
        logger.error(f"فشل في تأكيد استلام التذكرة إلى {chat_id}: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في تأكيد استلام التذكرة إلى {chat_id}: {e}")
    
    context.user_data.pop('creating_ticket', None)
    return ConversationHandler.END

# 📢 نظام البث
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    بدء عملية البث عن طريق مطالبة المشرف برسالة.
    """
    if not update.callback_query:
        return ConversationHandler.END
        
    query = update.callback_query
    await query.answer()
    
    if query.data == 'broadcast_active':
        context.user_data['broadcast_type'] = 'active'
    else:
        context.user_data['broadcast_type'] = 'all'
    
    try:
        await query.edit_message_text(
            text="📢 الرجاء إرسال الرسالة التي تريد بثها:\n"
                 "(يمكن أن تكون نص، صورة، أو فيديو)"
        )
    except TelegramError as e:
        logger.error(f"فشل في مطالبة برسالة البث: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في مطالبة برسالة البث: {e}")
    
    return BROADCASTING

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    معالجة إدخال رسالة البث وإرسالها إلى المستخدمين المحددين.
    """
    if not update.message:
        return ConversationHandler.END

    broadcast_type = context.user_data.get('broadcast_type')
    message = update.message
    
    if not broadcast_type:
        return ConversationHandler.END
    
    # العدادات
    success = 0
    failed = 0
    
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('SELECT chat_id, is_active FROM users')
            users = await cursor.fetchall()
            
            for chat_id, is_active in users:
                try:
                    # التحقق مما إذا كان المستخدم يطابق نوع البث
                    if broadcast_type == 'active' and not is_active:
                        continue
                    
                    # إعادة توجيه الرسالة
                    if message.text:
                        await context.bot.send_message(chat_id=chat_id, text=message.text)
                    elif message.photo:
                        await context.bot.send_photo(
                            chat_id=chat_id, 
                            photo=message.photo[-1].file_id, 
                            caption=message.caption
                        )
                    elif message.video:
                        await context.bot.send_video(
                            chat_id=chat_id, 
                            video=message.video.file_id, 
                            caption=message.caption
                        )
                    else:
                        continue
                    
                    success += 1
                    # تأخير بسيط لتجنب تجاوز معدل الحد
                    await asyncio.sleep(0.1)
                except TelegramError as e:
                    logger.error(f"فشل في البث إلى {chat_id}: {e}")
                    failed += 1
                except Exception as e:
                    logger.error(f"خطأ غير متوقع في البث إلى {chat_id}: {e}")
                    failed += 1
    except Exception as e:
        logger.error(f"خطأ في جلب المستخدمين للبث: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المستخدمين.")
        return ConversationHandler.END
    
    try:
        await update.message.reply_text(
            f"📢 **نتيجة الإذاعة**\n\n"
            f"✅ تمت بنجاح لـ {success} مستخدم\n"
            f"❌ فشل الإرسال لـ {failed} مستخدم",
            parse_mode=None
        )
    except TelegramError as e:
        logger.error(f"فشل في إرسال نتيجة البث: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال نتيجة البث: {e}")
    
    return ConversationHandler.END

# 📁 أمر المشرف للحصول على بيانات التدريب مع معالجة المهلة
async def get_training_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    إرسال ملف بيانات التدريب إلى المشرف مع معالجة أفضل للأخطاء وإدارة المهلة
    """
    try:
        query = update.callback_query
        if query:
            await query.answer()
            user_id = query.from_user.id
            message = query.message
        else:
            if not update.message:
                return
            user_id = update.effective_user.id
            message = update.message
            
        if user_id not in ADMIN_IDS:
            if message:
                await message.reply_text("⛔ ليس لديك صلاحية الوصول إلى هذه الأوامر!")
            return
        
        try:
            if not os.path.exists(TRAINING_DATA_FILE):
                with open(TRAINING_DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump({"conversations": []}, f, ensure_ascii=False, indent=2)
            
            # إرسال المستند مع مهلة أطول (60 ثانية)
            await context.bot.send_document(
                chat_id=user_id,
                document=open(TRAINING_DATA_FILE, 'rb'),
                filename='training_data.json',
                caption="📁 ملف التدريب الحالي للذكاء الاصطناعي",
                read_timeout=60,
                write_timeout=60,
                connect_timeout=60
            )
            
            if query and message:
                try:
                    await message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
                    
        except TimeoutError:
            error_msg = "⏳ انتهى وقت الانتظار أثناء محاولة إرسال الملف. يرجى المحاولة مرة أخرى."
            if message:
                await message.reply_text(error_msg)
            elif query and query.message:
                await query.message.reply_text(error_msg)
            logger.error(f"انتهت المهلة أثناء إرسال بيانات التدريب إلى المشرف {user_id}")
        except Exception as e:
            logger.error(f"خطأ في إرسال بيانات التدريب إلى المشرف {user_id}: {e}")
            error_msg = "❌ حدث خطأ أثناء محاولة إرسال ملف التدريب."
            if message:
                await message.reply_text(error_msg)
            elif query and query.message:
                await query.message.reply_text(error_msg)
                
    except Exception as e:
        logger.error(f"خطأ في معالج get_training_data: {e}")

# معالج الأخطاء
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    تسجيل الأخطاء الناتجة عن التحديثات.
    """
    logger.error('حدث خطأ أثناء معالجة التحديث: %s', context.error, exc_info=context.error)
    
    if isinstance(update, Update) and update.effective_message:
        chat_id = update.effective_message.chat_id
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ عذرًا، حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى لاحقًا."
            )
        except Exception as e:
            logger.error(f"فشل في إرسال رسالة الخطأ: {e}")

# معالجة لوحة التحكم
async def handle_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    معالجة استدعاءات لوحة التحكم عن طريق إعادة عرض لوحة التحكم.
    """
    if not update.callback_query or not update.callback_query.message:
        return
        
    query = update.callback_query
    await query.answer()
    await admin_command(update, context)

# إلغاء المحادثة
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    إلغاء المحادثة الحالية.
    """
    if not update.message:
        return ConversationHandler.END
    
    try:
        await update.message.reply_text('تم الإلغاء.')
    except TelegramError as e:
        logger.error(f"فشل في إرسال رسالة الإلغاء: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال رسالة الإلغاء: {e}")
    
    context.user_data.pop('awaiting_chat_id', None)
    context.user_data.pop('creating_ticket', None)
    return ConversationHandler.END

# 🛠 الوظيفة الرئيسية
async def main() -> None:
    """
    بدء تشغيل بوت Telegram وتهيئة جميع المعالجات.
    """
    # تهيئة قاعدة البيانات
    await init_db()
    
    # إنشاء التطبيق وتمرير رمز بوتك.
    application = Application.builder().token(TOKEN).build()
    
    # إضافة معالجات الأوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("adminhelp", admin_help))
    application.add_handler(CommandHandler("training_data", get_training_data))
    
    # إضافة معالجات استعلام رد الاتصال
    application.add_handler(CallbackQueryHandler(request_activation, pattern='^request_activation$'))
    application.add_handler(CallbackQueryHandler(handle_admin_actions, pattern='^(activate|reject|ban|manual_activate|_user)'))
    application.add_handler(CallbackQueryHandler(handle_admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(show_stats, pattern='^show_stats$'))
    application.add_handler(CallbackQueryHandler(support_ticket, pattern='^support_ticket$'))
    application.add_handler(CallbackQueryHandler(start_broadcast, pattern='^broadcast_(active|all)$'))
    application.add_handler(CallbackQueryHandler(toggle_activation, pattern='^toggle_activation$'))
    application.add_handler(CallbackQueryHandler(get_training_data, pattern='^get_training_data$'))
    
    # إضافة معالجات المحادثة
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(support_ticket, pattern='^support_ticket$'),
            CallbackQueryHandler(start_broadcast, pattern='^broadcast_(active|all)$'),
            CallbackQueryHandler(handle_admin_actions, pattern='^(activate_user|manual_activate)$')
        ],
        states={
            TICKET_HANDLING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ticket_message)],
            BROADCASTING: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast_message)],
            ACTIVATE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_activation)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_chat=True,
        per_user=True,
        per_message=True
    )
    application.add_handler(conv_handler)
    
    # إضافة معالج الرسائل لمحادثات الذكاء الاصطناعي (يتعامل مع النصوص والصور والملفات)
    application.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.Document.ALL, 
        handle_message
    ))
    
    # إضافة معالج الأخطاء
    application.add_error_handler(error_handler)
    
    # بدء تشغيل البوت
    await application.run_polling()

if __name__ == '__main__':
    # تشغيل البوت في حلقة الأحداث الحالية، مناسبة لبيئات مثل Pydroid 3
    loop = asyncio.get_event_loop()
    try:
        # جدولة المهمة الرئيسية كوروتين كمهمة في الحلقة الحالية
        loop.create_task(main())
        # الحفاظ على استمرار تشغيل الحلقة إلى أجل غير مسمى في البيئات التفاعلية
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        logger.error(f"خطأ في تشغيل البوت: {e}")
    finally:
        # تجنب إغلاق الحلقة في Pydroid 3 لمنع حدوث RuntimeError
        pass
        