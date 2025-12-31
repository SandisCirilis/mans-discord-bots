import discord
from discord.ext import commands
import yt_dlp
import asyncio
import google.generativeai as genai
from flask import Flask, render_template, request, jsonify
from threading import Thread
import os

# --- KONFIGURĀCIJA ---
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_KEY = os.getenv('GEMINI_KEY')

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix='!', intents=intents)

queue = []
current_song = {"title": "Nekas neskan", "user": ""}
history = []

# --- UZLABOTI YTDL IESTATĪJUMI ---
ytdl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'cookiefile': 'www.youtube.com_cookies.txt',
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'android'],
            'player_skip': ['configs', 'webpage']
        }
    }
}

ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_opts)

async def update_bot_status(is_playing=False):
    try:
        status = "Vergoju 🐒" if is_playing else "Chilloju 🌈"
        await bot.change_presence(activity=discord.Game(name=status))
    except: pass

# --- FLASK WEB DAĻA ---
app = Flask('')

@app.route('/')
def home(): return render_template('index.html')

@app.route('/now_playing')
def now_playing(): return {"current": current_song, "history": history}

@app.route('/get_lyrics')
def get_lyrics():
    dziesma = current_song.get('title', 'Nekas neskan')
    if not GEMINI_KEY or dziesma == "Nekas neskan":
        return jsonify({"lyrics": "Nav pieejams."})
    try:
        # ŠEIT IR LABOTS MODEĻA NOSAUKUMS STABILITĀTEI
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(f"Uzraksti dziesmas '{dziesma}' vārdus.")
        return jsonify({"lyrics": response.text})
    except:
        return jsonify({"lyrics": "Kļūda AI savienojumā."})

# --- DISCORD KOMANDAS ---

@bot.command(name='ai')
async def ai_chat(ctx, *, jautajums):
    if not GEMINI_KEY: return await ctx.send("❌ AI nav konfigurēts.")
    async with ctx.typing():
        try:
            # IZMANTOJAM gemini-1.5-flash BEZ models/ PREFIKSA (jaunākā bibliotēka to saprot)
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(f"Atbildi īsi un latviski: {jautajums}")
            await ctx.send(response.text[:2000])
        except Exception as e:
            # Ja joprojām met 404, mēģinām veco nosaukumu automātiski
            try:
                model = genai.GenerativeModel('gemini-pro')
                response = model.generate_content(f"Atbildi latviski: {jautajums}")
                await ctx.send(response.text[:2000])
            except:
                await ctx.send(f"❌ AI Kļūda: Google neatpazīst modeli šajā reģionā.")

@bot.command(name='play')
async def play(ctx, *, search):
    if not ctx.author.voice: return await ctx.send("❌ Tev jābūt balss kanālā!")
    
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if not voice:
        voice = await ctx.author.voice.channel.connect(timeout=20.0, self_deaf=True)

    async with ctx.typing():
        await add_to_queue_internal(voice, search, ctx.author.display_name)
        await ctx.send(f"✅ Pievienots: **{search}**")

@bot.command(name='stop')
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await update_bot_status(False)
        await ctx.send("Atā!")

# --- IEKŠĒJĀ LOĢIKA ---

async def add_to_queue_internal(voice, search, username):
    global current_song
    try:
        loop = bot.loop or asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False, process=True))
        
        if not info: return
        if 'entries' in info: info = info['entries'][0]
        
        url = info.get('url')
        title = info.get('title', 'Nezināma dziesma')
        
        song = {'url': url, 'title': title, 'user': username}
        
        if voice.is_playing():
            queue.append(song)
        else:
            current_song = song
            # FFmpeg palaišana ar visām drošības opcijām
            source = discord.FFmpegPCMAudio(url, **ffmpeg_opts)
            voice.play(source, after=lambda e: bot.loop.create_task(check_queue_internal(voice)))
            await update_bot_status(True)
    except Exception as e:
        print(f"Kļūda: {e}")

async def check_queue_internal(voice):
    global current_song
    if queue:
        current_song = queue.pop(0)
        source = discord.FFmpegPCMAudio(current_song['url'], **ffmpeg_opts)
        voice.play(source, after=lambda e: bot.loop.create_task(check_queue_internal(voice)))
        await update_bot_status(True)
    else:
        current_song = {"title": "Nekas neskan", "user": ""}
        await update_bot_status(False)

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    t = Thread(target=run)
    t.daemon = True
    t.start()
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
