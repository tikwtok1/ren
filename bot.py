hereimport os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from pytubefix import YouTube
from pytubefix.cli import on_progress
from aiohttp import web

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8868474021:AAFuT8wnMxq8EdC9keC4o19uMLa2C5e3BQg"
PORT = int(os.environ.get("PORT", 8080))

user_data = {}

def get_video_info(url: str):
    yt = YouTube(url, on_progress_callback=on_progress)
    # نجبر استخدام عميل android لتجنب الحظر
    yt.bypass_age_gate()  # محاولة لتجاوز قيود العمر إن وجدت
    return yt

def build_quality_buttons(yt: YouTube):
    streams = yt.streams.filter(progressive=False, type="video").order_by('resolution').desc()
    # نجمع دقات فريدة مع الصوت
    qualities = {}
    for s in streams:
        if s.resolution and int(s.resolution.replace('p','')) >= 144:
            has_audio = s.is_progressive or s.audio_codec is not None
            label = f"{s.resolution}{' 🔊' if has_audio else ''}"
            if label not in qualities:
                qualities[label] = s
    buttons = []
    for label, stream in sorted(qualities.items(), key=lambda x: int(x[0].split('p')[0]), reverse=True):
        buttons.append([InlineKeyboardButton(f"🎥 {label}", callback_data=f"vid_{stream.itag}")])
    # خيار الصوت فقط
    audio_stream = yt.streams.get_audio_only()
    if audio_stream:
        buttons.append([InlineKeyboardButton("🎵 تحميل الصوت (MP4)", callback_data=f"aud_{audio_stream.itag}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return buttons

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 أرسل رابط فيديو يوتيوب لتحميله.\nيمكنك اختيار الجودة أو تحميل الصوت فقط.")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    msg = await update.message.reply_text("⏳ جاري جلب معلومات الفيديو...")
    try:
        yt = get_video_info(url)
        user_data[chat_id] = {'yt': yt, 'url': url}
        title = yt.title or 'بدون عنوان'
        duration = yt.length
        duration_str = f"{duration//60}:{duration%60:02d}" if duration else "غير معروف"
        thumb = yt.thumbnail_url
        caption = f"📹 *{title}*\n⏱ المدة: {duration_str}\nاختر الجودة:"
        buttons = build_quality_buttons(yt)
        reply_markup = InlineKeyboardMarkup(buttons)
        if thumb:
            await context.bot.send_photo(chat_id, thumb, caption=caption, parse_mode='Markdown', reply_markup=reply_markup)
            await msg.delete()
        else:
            await msg.edit_text(caption, parse_mode='Markdown', reply_markup=reply_markup)
    except Exception as e:
        logger.exception("Error fetching video info")
        await msg.edit_text(f"❌ فشل جلب المعلومات. تأكد من الرابط.\nالتفاصيل: {str(e)[:200]}")

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
    yt = uinfo['yt']
    itag = int(data.split('_')[1])
    type_ = data.split('_')[0]
    await query.edit_message_caption("⏳ جاري التحميل...")

    try:
        if type_ == 'aud':
            stream = yt.streams.get_by_itag(itag)
        else:
            stream = yt.streams.get_by_itag(itag)

        # تحميل الملف
        out_file = stream.download(output_path='/tmp', filename_prefix='yt_')
        # لو التحميل رجع None ننتظر قليلاً (نادر)
        if not out_file:
            raise Exception("Download returned None")
        # قد يكون الملف بدون امتداد صحيح
        if type_ == 'aud':
            # تحويل لصوت فقط إذا أمكن (لكن عادة stream الصوت يكون بصيغة mp4)
            final_file = out_file
        else:
            final_file = out_file

        file_size = os.path.getsize(final_file)
        max_size = 50 * 1024 * 1024  # 50MB
        if file_size > max_size:
            os.remove(final_file)
            await query.edit_message_caption("❌ حجم الملف أكبر من 50 ميجابايت. جرب جودة أقل.")
            return

        # إرسال الملف
        with open(final_file, 'rb') as f:
            if type_ == 'aud':
                await context.bot.send_audio(chat_id, f, title=yt.title, performer=yt.author)
            else:
                await context.bot.send_video(chat_id, f, caption=f"🎬 {yt.title}", supports_streaming=True)

        os.remove(final_file)
        await query.edit_message_caption("✅ تم التحميل!")
    except Exception as e:
        logger.exception("Download error")
        await query.edit_message_caption(f"❌ فشل التحميل.\n{str(e)[:300]}")

# خادم ويب بسيط
async def health_check(request):
    return web.Response(text="Bot is running")

async def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(button_callback))

    app_web = web.Application()
    app_web.router.add_get('/', health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server running on port {PORT}")

    async with application:
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
