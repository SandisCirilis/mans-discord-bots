import discord
from discord.ext import commands
import yt_dlp
import asyncio
import requests
import re
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

# Mūzikas rinda un dati
queue = []
current_song = {"title": "Nekas neskan", "user": ""}
history = []

# --- IZLABOTIE YTDL IESTATĪJUMI ---
ytdl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'cookiefile': 'www.youtube.com_cookies.txt', 
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web', 'ios'],
            'skip': ['dash', 'hls']
        }
    },
    'ignoreerrors': True,
    'source_address': '0.0.0.0' # Palīdz ar IPv6/IPv4 savienojumu uz serveriem
}

ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_opts)

# --- PALĪGFUNKCIJA STATUSA MAIŅAI ---
async def update_bot_status(is_playing=False):
    try:
        if is_playing:
            await bot.change_presence(activity=discord.Game(name="Vergoju 🐒"))
        else:
            await bot.change_presence(activity=discord.Game(name="Chilloju 🌈"))
    except:
        pass

# --- FLASK WEB SERVERA DAĻA ---
app = Flask('')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/pause')
def pause_web():
    for vc in bot.voice_clients:
        if vc.is_playing(): 
            vc.pause()
            bot.loop.create_task(update_bot_status(False))
    return "OK", 200

@app.route('/skip')
def skip_web():
    for vc in bot.voice_clients:
        vc.stop()
    return "OK", 200

@app.route('/resume')
def resume_web():
    for vc in bot.voice_clients:
        if vc.is_paused(): 
            vc.resume()
            bot.loop.create_task(update_bot_status(True))
    return "OK", 200

@app.route('/stop_music')
def stop_web():
    queue.clear()
    for vc in bot.voice_clients:
        bot.loop.create_task(vc.disconnect())
    bot.loop.create_task(update_bot_status(False))
    return "OK", 200

@app.route('/now_playing')
def now_playing():
    return {"current": current_song, "history": history}

@app.route('/send_via_bot')
def send_via_bot():
    text = request.args.get('text')
    chan1 = bot.get_channel(1376938381098877010) 
    chan2 = bot.get_channel(1455640301015269386) 
    if text:
        if chan1:
            bot.loop.create_task(chan1.send(text))
        if chan2:
            bot.loop.create_task(chan2.send(text))
        return "Ziņa nosūtīta!", 200
    return "Kļūda: Nav teksta", 400

@app.route('/get_online_users')
def get_online_users():
    if not bot.guilds: return jsonify([])
    guild = bot.guilds[0] 
    online_members = []
    for member in guild.members:
        if not member.bot:
            online_members.append({
                "name": member.display_name,
                "status": str(member.status),
                "avatar": str(member.display_avatar.url)
            })
    return jsonify(online_members)

@app.route('/play_web')
def play_from_web():
    q = request.args.get('query')
    if not q: return "Tukšs vaicājums", 400
    asyncio.run_coroutine_threadsafe(process_web_request(q), bot.loop)
    return "OK", 200

@app.route('/get_lyrics')
def get_lyrics():
    global current_song
    dziesma = current_song.get('title', 'Nekas neskan')
    if dziesma == "Nekas neskan":
        return jsonify({"lyrics": "Pašlaik nekas netiek atskaņots."})
    if not GEMINI_KEY:
        return jsonify({"lyrics": "Gemini API atslēga nav konfigurēta."})
    try:
        # LABOTS MODEĻA NOSAUKUMS
        model = genai.GenerativeModel('models/gemini-1.5-flash')
        prompt = f"Atrodi un uzraksti dziesmas '{dziesma}' vārdus. Ja nevari atrast, uzraksti kopsavilkumu par dziesmu latviski."
        response = model.generate_content(prompt)
        return jsonify({"lyrics": response.text if response.text else "Neizdevās atrast."})
    except Exception as e:
        return jsonify({"lyrics": f"Kļūda: {e}"})

# --- DISCORD KOMANDAS ---

@bot.command(name='ai')
async def ai_chat(ctx, *, jautajums):
    if not GEMINI_KEY:
        return await ctx.send("❌ AI nav pieejams (trūkst atslēgas).")
    async with ctx.typing():
        try:
            # LABOTS MODEĻA NOSAUKUMS
            model = genai.GenerativeModel('models/gemini-1.5-flash')
            response = model.generate_content(f"Atbildi latviski: {jautajums}")
            if response.text:
                full_text = response.text
                for i in range(0, len(full_text), 1900):
                    await ctx.send(full_text[i:i+1900])
            else:
                await ctx.send("AI neatbildēja.")
        except Exception as e:
            await ctx.send(f"❌ Kļūda: {e}")

@bot.command(name='play')
async def play(ctx, *, search):
    if not ctx.author.voice:
        return await ctx.send("❌ Tev jābūt balss kanālā!")
    
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if not voice:
        try:
            voice = await ctx.author.voice.channel.connect(timeout=20.0, self_deaf=True)
        except Exception as e:
            return await ctx.send(f"❌ Nevarēju pieslēgties kanālam: {e}")

    async with ctx.typing():
        await add_to_queue_internal(voice, search, ctx.author.display_name)
        # Ziņojums tiks nosūtīts tikai tad, ja meklēšana izdosies
        await ctx.send(f"✅ **Mēģinu pievienot:** (**{search}**)")

@bot.command(name='skip')
async def skip(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send("⏭ Izlaists!")

@bot.command(name='stop', aliases=['pisnah', 'stop_music', 'izslēgt'])
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await update_bot_status(False)
        await ctx.send("Sapratu saimniek, pazūdu")

@bot.command(name='salvis')
async def salvis(ctx):
    try:
        with open('salvis.png', 'rb') as f:
            await ctx.send(file=discord.File(f))
    except FileNotFoundError:
        await ctx.send("Kļūda: Fails 'salvis.png' netika atrasts!")

@bot.command(name='raitis')
async def raitis(ctx):
    try:
        with open('raitis.mp4', 'rb') as f:
            await ctx.send(content="Video no Jeffrey Epstein failiem 🎥", file=discord.File(f))
    except FileNotFoundError:
        await ctx.send("❌ Kļūda: Fails 'raitis.mp4' netika atrasts!")

# --- IEKŠĒJĀ LOĢIKA ---

async def auto_join_logic():
    if bot.voice_clients: return bot.voice_clients[0]
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            if len(channel.members) > 0:
                return await channel.connect(self_deaf=True)
    return None

async def process_web_request(query):
    vc = await auto_join_logic()
    if vc:
        await add_to_queue_internal(vc, query, "Dashboard")

async def add_to_queue_internal(voice, search, username):
    global current_song
    try:
        loop = bot.loop or asyncio.get_event_loop()
        # Ievietota papildu aizsardzība pret NoneType
        info = await loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False, process=True))
        
        if info is None:
            print(f"❌ YouTube neatgrieza nekādus datus priekš: {search}")
            return

        if 'entries' in info:
            if not info['entries']:
                print("❌ Meklēšana nedeva rezultātus.")
                return
            video_data = info['entries'][0]
        else:
            video_data = info
        
        if video_data is None:
            print("❌ Video dati ir tukši.")
            return
        
        url = video_data.get('url')
        title = video_data.get('title', 'Nezināma dziesma')
        
        if not url:
            print("❌ Nav pieejams atskaņojams URL.")
            return

        song = {'url': url, 'title': title, 'user': username}
        
        if voice.is_playing() or voice.is_paused():
            queue.append(song)
        else:
            current_song = song
            source = discord.FFmpegPCMAudio(url, executable="ffmpeg", **ffmpeg_opts)
            voice.play(source, after=lambda e: bot.loop.create_task(check_queue_internal(voice)))
            await update_bot_status(True)
            print(f"🎶 Šobrīd atskaņoju: {title}")
    except Exception as e:
        print(f"Kritiska kļūda add_to_queue: {e}")

async def check_queue_internal(voice):
    global current_song
    if len(queue) > 0:
        if current_song["title"] != "Nekas neskan":
            history.insert(0, current_song)
            if len(history) > 5: history.pop()
        current_song = queue.pop(0)
        source = discord.FFmpegPCMAudio(current_song['url'], executable="ffmpeg", **ffmpeg_opts)
        voice.play(source, after=lambda e: bot.loop.create_task(check_queue_internal(voice)))
        await update_bot_status(True)
    else:
        if current_song["title"] != "Nekas neskan":
            history.insert(0, current_song)
            if len(history) > 5: history.pop()
        current_song = {"title": "Nekas neskan", "user": ""}
        await update_bot_status(False)

@bot.event
async def on_ready():
    print(f'Bots {bot.user} ir gatavs!')
    await update_bot_status(False)

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    t = Thread(target=run)
    t.daemon = True 
    t.start()
    
    if DISCORD_TOKEN:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"Kritiskā bota kļūda: {e}")
    else:
        print("KĻŪDA: Nav atrasts DISCORD_TOKEN!")
