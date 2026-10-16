import discord
from discord.ext import tasks, commands
import aiohttp
import json
import os
import io
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# SERWER HTTP (DLA WEBSERVICE RENDER)
# ==========================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Albion Killbot is running!")

    def log_message(self, format, *args):
        return

def run_web_server():
    server = HTTPServer(("0.0.0.0", 8080), SimpleHandler)
    server.serve_forever()

# ==========================================
# KONFIGURACJA ZAPISU I ODCZYTU
# ==========================================
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "main_channel_id": 1560358432165466303,
    "big_channel_id": None,
    "high_value_threshold": 15000000,
    "server_region": "europe",
    "tracked_players": ["Szynkus"]
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key, val in DEFAULT_CONFIG.items():
                    if key not in data:
                        data[key] = val
                return data
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

config = load_config()
seen_event_ids = set()

API_URLS = {
    "live": "https://gameinfo.albiononline.com/api/gameinfo",
    "europe": "https://gameinfo-ams.albiononline.com/api/gameinfo",
    "asia": "https://gameinfo-sgp.albiononline.com/api/gameinfo"
}

PRICES_API_URLS = {
    "live": "https://west.albion-online-data.com/api/v2/stats/prices",
    "europe": "https://europe.albion-online-data.com/api/v2/stats/prices",
    "asia": "https://east.albion-online-data.com/api/v2/stats/prices"
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# POMOCNICZE: POBIERANIE CEN I WARTOŚCI
# ==========================================
async def get_item_prices(session, item_ids, region):
    if not item_ids:
        return {}
    unique_ids = list(set(item_ids))
    ids_str = ",".join(unique_ids)
    prices_url = PRICES_API_URLS.get(region, PRICES_API_URLS["europe"])
    url = f"{prices_url}/{ids_str}.json"
    
    prices = {}
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                for entry in data:
                    item_id = entry.get("item_id")
                    price = entry.get("sell_price_min") or entry.get("buy_price_max") or 0
                    if price > 0:
                        if item_id not in prices or price < prices[item_id]:
                            prices[item_id] = price
    except Exception as e:
        print(f"[Ceny Error] {e}", flush=True)
    return prices

def calculate_equipment_value(equipment_dict, prices):
    total_val = 0
    if not equipment_dict:
        return 0
    for slot, item in equipment_dict.items():
        if item:
            item_type = item.get("Type")
            count = item.get("Count", 1)
            total_val += prices.get(item_type, 0) * count
    return total_val

# ==========================================
# PĘTLA SPRAWDZAJĄCA KILLBOARD
# ==========================================
@tasks.loop(seconds=30)
async def check_albion_kills():
    global config
    config = load_config()
    
    tracked = config.get("tracked_players", [])
    main_chan_id = config.get("main_channel_id")
    
    if not tracked or not main_chan_id:
        return

    main_channel = bot.get_channel(main_chan_id)
    big_chan_id = config.get("big_channel_id")
    big_channel = bot.get_channel(big_chan_id) if big_chan_id else None

    if not main_channel:
        return

    region = config.get("server_region", "europe")
    base_url = API_URLS.get(region, API_URLS["europe"])

    async with aiohttp.ClientSession() as session:
        url = f"{base_url}/events?limit=51&offset=0"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return
                events = await resp.json()
        except Exception:
            return

        tracked_lower = [t.lower() for t in tracked]

        for event in reversed(events):
            event_id = event.get("EventId")
            if event_id in seen_event_ids:
                continue
                
            seen_event_ids.add(event_id)
            if len(seen_event_ids) > 2000:
                seen_event_ids.pop()

            killer_name = event.get("Killer", {}).get("Name", "")
            victim_name = event.get("Victim", {}).get("Name", "")
            participants = event.get("Participants", [])
            participant_names = [p.get("Name") for p in participants if p.get("Name")]

            is_killer = killer_name.lower() in tracked_lower
            is_victim = victim_name.lower() in tracked_lower
            is_participant = any(p_name.lower() in tracked_lower for p_name in participant_names)

            if not (is_killer or is_victim or is_participant):
                continue

            print(f"[KILLS] WYKRYTO KILL! Zabójca: {killer_name} | Ofiara: {victim_name}", flush=True)

            # Obliczanie wartości lootu
            victim_equip = event.get("Victim", {}).get("Equipment", {})
            victim_inventory = [item for item in event.get("Victim", {}).get("Inventory", []) if item]
            all_items = [item.get("Type") for item in victim_equip.values() if item]
            all_items += [item.get("Type") for item in victim_inventory if item]
            
            prices = await get_item_prices(session, all_items, region)
            equip_value = calculate_equipment_value(victim_equip, prices)
            inv_value = sum(prices.get(i.get("Type"), 0) * i.get("Count", 1) for i in victim_inventory)
            total_value = equip_value + inv_value

            threshold = config.get("high_value_threshold", 15000000)

            # Pobieranie profesjonalnie wyrenderowanej grafiki starcia
            img_url = f"https://render.albionkillbot.com/event/{event_id}.png"
            img_bytes = None
            try:
                async with session.get(img_url, timeout=10) as img_resp:
                    if img_resp.status == 200:
                        img_bytes = await img_resp.read()
            except Exception as e:
                print(f"[RENDER ERROR] {e}", flush=True)

            embed_color = discord.Color.green() if is_killer else discord.Color.red() if is_victim else discord.Color.gold()
            
            embed = discord.Embed(
                title=f"⚔️ {killer_name}  ➔  {victim_name}",
                url=f"https://albiononline.com/killboard/kill/{event_id}",
                color=embed_color
            )

            if img_bytes:
                buffer = io.BytesIO(img_bytes)
                file = discord.File(fp=buffer, filename="killboard.png")
                embed.set_image(url="attachment://killboard.png")
                
                try:
                    await main_channel.send(file=file, embed=embed)
                    print(f"[SUCCESS] Wysłano profesjonalny układ graficzny!", flush=True)
                except Exception as send_error:
                    print(f"[DISCORD ERROR] {send_error}", flush=True)
            else:
                await main_channel.send(embed=embed)

            if total_value >= threshold and big_channel:
                if img_bytes:
                    buffer.seek(0)
                    big_file = discord.File(fp=buffer, filename="killboard.png")
                    await big_channel.send(
                        content=f"🚨 **BIG KILL / LEGENDARNY LOOT (> {threshold/1_000_000:.0f}M)!** 🚨",
                        file=big_file,
                        embed=embed
                    )
                else:
                    await big_channel.send(content=f"🚨 **BIG KILL!** 🚨", embed=embed)

# ==========================================
# KOMENDY DISCORDA (SLASH COMMANDS)
# ==========================================
@bot.tree.command(name="dodaj", description="Dodaj nowego gracza do śledzenia")
async def dodaj(interaction: discord.Interaction, nick: str):
    if nick in config["tracked_players"]:
        await interaction.response.send_message(f"⚠️ Gracz **{nick}** jest już na liście.", ephemeral=True)
        return
    config["tracked_players"].append(nick)
    save_config(config)
    await interaction.response.send_message(f"✅ Dodano gracza **{nick}** do monitorowania.", ephemeral=False)

@bot.tree.command(name="usun", description="Usuń gracza ze śledzenia")
async def usun(interaction: discord.Interaction, nick: str):
    if nick not in config["tracked_players"]:
        await interaction.response.send_message(f"❌ Gracz **{nick}** nie znajduje się na liście.", ephemeral=True)
        return
    config["tracked_players"].remove(nick)
    save_config(config)
    await interaction.response.send_message(f"🗑️ Usunięto gracza **{nick}**.", ephemeral=False)

@bot.tree.command(name="lista", description="Wyświetl listę śledzonych graczy")
async def lista(interaction: discord.Interaction):
    players = config["tracked_players"]
    players_str = "\n".join([f"• {p}" for p in players]) if players else "Brak"
    main_ch = f"<#{config['main_channel_id']}>" if config['main_channel_id'] else "Nie ustawiono"
    
    embed = discord.Embed(title="⚙️ Konfiguracja Killbota", color=discord.Color.blurple())
    embed.add_field(name="🌍 Region", value=config['server_region'].upper(), inline=True)
    embed.add_field(name="📢 Kanał Główny", value=main_ch, inline=False)
    embed.add_field(name=f"👥 Śledzeni gracze ({len(players)})", value=players_str, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="ustaw_kanaly", description="Ustaw kanał powiadomień")
async def ustaw_kanaly(interaction: discord.Interaction, glowny: discord.TextChannel, duze_kille: discord.TextChannel = None):
    config["main_channel_id"] = glowny.id
    config["big_channel_id"] = duze_kille.id if duze_kille else None
    save_config(config)
    await interaction.response.send_message(f"✅ Ustawiono główny kanał na {glowny.mention}!", ephemeral=False)

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako: {bot.user.name}", flush=True)
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Zsynchronizowano {len(synced)} komend.", flush=True)
    except Exception as e:
        print(f"Błąd synchronizacji: {e}", flush=True)
        
    if not check_albion_kills.is_running():
        check_albion_kills.start()
        print("⚔️ Pętla sprawdzająca kille została uruchomiona!", flush=True)

if __name__ == "__main__":
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ BŁĄD: Brak zmiennej DISCORD_TOKEN!", flush=True)
    else:
        bot.run(TOKEN)
