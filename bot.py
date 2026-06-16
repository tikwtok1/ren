import os
import logging
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from aiohttp import web

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "8868474021:AAFuT8wnMxq8EdC9keC4o19uMLa2C5e3BQg"
PORT = int(os.environ.get("PORT", 8080))

user_data = {}

def extract_formats(url: str):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'simulate': True,
        'no_cookies': True,
        'cookies_from_browser': None,
        'extractor_args': {'youtube': {'player_client': ['android']}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info

def build_quality_buttons(info):
    formats = info.get('formats', [])
    qualities = {}
    audio_formats = []
    for f in formats:
        if f.get('vcodec') == 'none':
            if f.get('abr'):
                audio_formats.append(f)
        else:
            height = f.get('height')
            if height and height >= 144:
                has_audio = f.get('acodec') != 'none'
                key = f"{height}p{' 🔊' if has_audio else ''}"
                if key not in qualities or (has_audio and not qualities[key].get('acodec') != 'none'):
                    qualities[key] = f
    buttons = []
    for label, fmt in sorted(qualities.items(), key=lambda x: int(x[0].split('p')[0]) if 'p' in x[0] else 0, reverse=True):
        buttons.append([InlineKeyboardButton(f"🎥 {label}", callback_data=f"vid_{fmt['format_id']}")])
    if audio_formats:
        best_audio = max(audio_formats, key=lambda f: f.get('abr', 0))
        buttons.append([InlineKeyboardButton("🎵 تحميل الصوت (MP3)", callback_data=f"aud_{best_audio['format_id']}")])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return buttons

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 أرسل رابط فيديو يوتيوب لتحميله.\nيمكنك اختيار الجودة أو تحميل الصوت فقط.")

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
    type_ = data.split('_')[0]
    await query.edit_message_caption("⏳ جاري التحميل...")
    ydl_opts = {
        'outtmpl': '%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'no_cookies': True,
        'cookies_from_browser': None,
        'extractor_args': {'youtube': {'player_client': ['android']}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
        },
        'merge_output_format': 'mp4',
        'socket_timeout': 30,
    }
    if type_ == 'aud':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    else:
        ydl_opts['format'] = f'{format_id}+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            filename = ydl.prepare_filename(info)
        if type_ == 'aud':
            final_filename = filename.rsplit('.', 1)[0] + '.mp3'
        else:
            final_filename = filename.replace('.webm', '.mp4').replace('.mkv', '.mp4')
        file_size = os.path.getsize(final_filename)
        if file_size > 50 * 1024 * 1024:
            os.remove(final_filename)
            await query.edit_message_caption("❌ حجم الملف أكبر من 50 ميجابايت. جرب جودة أقل.")
            return
        with open(final_filename, 'rb') as f:
            if type_ == 'aud':
                await context.bot.send_audio(chat_id, f, title=info.get('title', 'صوت'), performer=info.get('uploader', 'غير معروف'))
            else:
                await context.bot.send_video(chat_id, f, caption=f"🎬 {info.get('title', '')}", supports_streaming=True)
        os.remove(final_filename)
        await query.edit_message_caption("✅ تم التحميل!")
    except Exception as e:
        logger.exception("Download error")
        await query.edit_message_caption(f"❌ فشل التحميل.\n{str(e)[:300]}")

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
