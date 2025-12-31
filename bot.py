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

# Mūzikas dati
queue = []
current_song = {"title": "Nekas neskan", "user": ""}
history = []

# --- YTDL IESTATĪJUMI (Pielāgoti stabilitātei) ---
ytdl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'cookiefile': 'www.youtube.com_cookies.txt', 
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'source_address': '0.0.0.0',
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'android'],
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

# --- FLASK WEB SERVERA DAĻA ---
app = Flask('')

@app.route('/')
def home(): return render_template('index.html')

@app.route('/now_playing')
def now_playing(): return {"current": current_song, "history": history}

@app.route('/get_online_users')
def get_online_users():
    if not bot.guilds: return jsonify([])
    guild = bot.guilds[0]
    online = []
    for m in guild.members:
        if not m.bot:
            online.append({"name": m.display_name, "status": str(m.status), "avatar": str(m.display_avatar.url)})
    return jsonify(online)

@app.route('/get_lyrics')
def get_lyrics():
    dziesma = current_song.get('title', 'Nekas neskan')
    if not GEMINI_KEY or dziesma == "Nekas neskan": return jsonify({"lyrics": "Nav pieejams."})
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(f"Uzraksti dziesmas '{dziesma}' vārdus.")
        return jsonify({"lyrics": response.text})
    except: return jsonify({"lyrics": "Kļūda AI."})

# --- DISCORD KOMANDAS ---

@bot.command(name='ai')
async def ai_chat(ctx, *, jautajums):
    if not GEMINI_KEY: return await ctx.send("❌ AI nav konfigurēts.")
    async with ctx.typing():
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(f"Atbildi latviski: {jautajums}")
            await ctx.send(response.text[:2000])
        except Exception as e:
            await ctx.send(f"❌ AI kļūda: {e}")

@bot.command(name='play')
async def play(ctx, *, search):
    if not ctx.author.voice: return await ctx.send("❌ Tev jābūt balss kanālā!")
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if not voice:
        voice = await ctx.author.voice.channel.connect(timeout=20.0, self_deaf=True)
    async with ctx.typing():
        await add_to_queue_internal(voice, search, ctx.author.display_name)
        await ctx.send(f"✅ Pievienots rindai: **{search}**")

@bot.command(name='skip')
async def skip(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send("⏭ Izlaists!")

@bot.command(name='stop', aliases=['pisnah', 'izslēgt'])
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await update_bot_status(False)
        await ctx.send("Atā! 👋")

@bot.command(name='salvis')
async def salvis(ctx):
    try: await ctx.send(file=discord.File('salvis.png'))
    except: await ctx.send("Attēls 'salvis.png' nav atrasts.")

@bot.command(name='raitis')
async def raitis(ctx):
    try: await ctx.send(content="Epstein faili 🎥", file=discord.File('raitis.mp4'))
    except: await ctx.send("Video 'raitis.mp4' nav atrasts.")

# --- IEKŠĒJĀ LOĢIKA ---

async def add_to_queue_internal(voice, search, username):
    global current_song
    try:
        loop = bot.loop or asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False, process=True))
        if 'entries' in info: info = info['entries'][0]
        
        song = {'url': info['url'], 'title': info['title'], 'user': username}
        
        if voice.is_playing() or voice.is_paused():
            queue.append(song)
        else:
            current_song = song
            source = discord.FFmpegPCMAudio(song['url'], **ffmpeg_opts)
            voice.play(source, after=lambda e: bot.loop.create_task(check_queue_internal(voice)))
            await update_bot_status(True)
    except Exception as e: print(f"Kļūda: {e}")

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

@bot.event
async def on_ready():
    print(f'Bots {bot.user} gatavs!')

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    Thread(target=run).start()
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
