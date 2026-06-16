import os
import logging
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8868474021:AAFuT8wnMxq8EdC9keC4o19uMLa2C5e3BQg"

# تخزين مؤقت للبيانات لكل محادثة
user_data = {}

# دالة لاستخراج معلومات الفيديو والصيغ المتاحة
def extract_formats(url: str):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'no_color': True,
        'simulate': True,
        'no_cookies': True,               # لا يستخدم كعكات
        'cookies_from_browser': None,     # تعطيل متصفح الكعكات
        'extract_flat': False,
        'socket_timeout': 30,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info

# تصنيف الجودات المتاحة لعرضها بشكل منظم
def build_quality_buttons(info):
    formats = info.get('formats', [])
    # نجمع الجودات الفريدة (فيديو + صوت)
    qualities = {}
    audio_formats = []
    for f in formats:
        if f.get('vcodec') == 'none':
            # صوت فقط
            abr = f.get('abr')
            if abr:
                audio_formats.append(f)
        else:
            height = f.get('height')
            if height and height >= 144:
                # إذا كانت الصيغة تحتوي على صوت فهي مفضلة
                has_audio = f.get('acodec') != 'none'
                key = f"{height}p{' 🔊' if has_audio else ''}"
                if key not in qualities or (has_audio and not qualities[key].get('acodec') != 'none'):
                    qualities[key] = f

    buttons = []
    # أزرار الجودات
    for label, fmt in sorted(qualities.items(), key=lambda x: int(x[0].split('p')[0]) if 'p' in x[0] else 0, reverse=True):
        height = fmt.get('height')
        format_id = fmt.get('format_id')
        buttons.append([InlineKeyboardButton(f"🎥 {label}", callback_data=f"vid_{format_id}")])
    # صوت فقط
    if audio_formats:
        best_audio = max(audio_formats, key=lambda f: f.get('abr', 0))
        buttons.append([InlineKeyboardButton("🎵 تحميل الصوت (MP3)", callback_data=f"aud_{best_audio['format_id']}")])
    # زر الإلغاء
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return buttons

# الأمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 أرسل رابط فيديو يوتيوب لتحميله.\n"
        "يمكنك اختيار الجودة أو تحميل الصوت فقط."
    )

# استقبال الروابط
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    msg = await update.message.reply_text("⏳ جاري جلب معلومات الفيديو...")
    try:
        info = extract_formats(url)
        user_data[chat_id] = {'info': info, 'url': url}
        title = info.get('title', 'بدون عنوان')
        duration = info.get('duration')
        duration_str = f"{duration//60}:{duration%60:02d}" if duration else "غير معروف"
        thumb = info.get('thumbnail')
        caption = f"📹 *{title}*\n⏱ المدة: {duration_str}\nاختر الجودة:"

        # إرسال الصورة المصغرة مع أزرار الاختيار
        buttons = build_quality_buttons(info)
        reply_markup = InlineKeyboardMarkup(buttons)
        if thumb:
            await context.bot.send_photo(chat_id, thumb, caption=caption, parse_mode='Markdown', reply_markup=reply_markup)
            await msg.delete()
        else:
            await msg.edit_text(caption, parse_mode='Markdown', reply_markup=reply_markup)
    except Exception as e:
        logger.exception("Error extracting info")
        await msg.edit_text(f"❌ فشل جلب المعلومات. تأكد من الرابط.\nالتفاصيل: {str(e)[:200]}")

# معالجة اختيار الجودة
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "cancel":
        await query.edit_message_caption("تم الإلغاء.")
        return

    if chat_id not in user_data:
        await query.edit_message_text("انتهت الجلسة. أرسل الرابط مرة أخرى.")
        return

    uinfo = user_data[chat_id]
    url = uinfo['url']
    info = uinfo['info']
    format_id = data.split('_')[1]
    type_ = data.split('_')[0]  # vid or aud

    # حذف رسالة الاختيار السابقة أو تعديلها
    await query.edit_message_caption("⏳ جاري التحميل...")

    ydl_opts = {
        'outtmpl': '%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'no_cookies': True,
        'cookies_from_browser': None,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        },
        'merge_output_format': 'mp4',
        'socket_timeout': 30,
    }

    if type_ == 'aud':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        file_extension = '.mp3'
    else:
        ydl_opts['format'] = f'{format_id}+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'
        file_extension = '.mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            filename = ydl.prepare_filename(info)
        # بعد المعالجة قد يتغير الامتداد
        if type_ == 'aud':
            final_filename = filename.rsplit('.', 1)[0] + '.mp3'
        else:
            final_filename = filename.replace('.webm', '.mp4').replace('.mkv', '.mp4')

        # التحقق من حجم الملف
        file_size = os.path.getsize(final_filename)
        max_size = 50 * 1024 * 1024  # 50 ميجابايت
        if file_size > max_size:
            os.remove(final_filename)
            await query.edit_message_caption(
                "❌ حجم الملف أكبر من الحد المسموح (50 ميجابايت). جرب جودة أقل."
            )
            return

        # إرسال الملف
        with open(final_filename, 'rb') as f:
            if type_ == 'aud':
                await context.bot.send_audio(
                    chat_id,
                    f,
                    title=info.get('title', 'صوت'),
                    performer=info.get('uploader', 'غير معروف')
                )
            else:
                await context.bot.send_video(
                    chat_id,
                    f,
                    caption=f"🎬 {info.get('title', '')}",
                    supports_streaming=True
                )

        # تنظيف
        os.remove(final_filename)
        await query.edit_message_caption("✅ تم التحميل!")
    except Exception as e:
        logger.exception("Download error")
        await query.edit_message_caption(f"❌ فشل التحميل.\n{str(e)[:300]}")

# التشغيل الرئيسي
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_callback))
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
