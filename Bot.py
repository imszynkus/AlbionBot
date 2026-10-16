import discord
from discord.ext import tasks, commands
import aiohttp
import json
import os
import io
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from PIL import Image, ImageDraw, ImageFont

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
# POMOCNICZE: POBIERANIE CEN I GENEROWANIE GRAFIKI
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

async def fetch_item_image(session, item_type, quality=1):
    if not item_type:
        return None
    url = f"https://render.albiononline.com/v1/item/{item_type}.png?quality={quality}"
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img
    except Exception:
        pass
    return None

async def generate_kill_image(session, killer_equip, victim_equip, victim_inventory):
    # Szerokość i wysokość tła
    canvas_w, canvas_h = 750, 360
    bg_color = (28, 29, 34, 255) # Ciemnoszare tło jak w KillBoard#1
    img = Image.new("RGBA", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    slot_size = 70
    
    # Układ slotów ekwipunku (X, Y)
    layout = {
        "Head": (1, 0), "Armor": (1, 1), "Shoes": (1, 2),
        "MainHand": (0, 1), "OffHand": (2, 1),
        "Cape": (2, 0), "Mount": (1, 3),
        "Food": (0, 2), "Potion": (0, 0)
    }

    # Rysowanie ekwipunku Zabójcy (Po lewej stronie)
    for slot, pos in layout.items():
        item = killer_equip.get(slot)
        x = 40 + pos[0] * (slot_size + 8)
        y = 30 + pos[1] * (slot_size + 8)
        draw.rectangle([x, y, x + slot_size, y + slot_size], fill=(42, 44, 52, 255), outline=(60, 64, 75, 255), width=2)
        if item:
            item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
            if item_img:
                item_img = item_img.resize((slot_size, slot_size))
                img.paste(item_img, (x, y), item_img)

    # Rysowanie ekwipunku Ofiary (Po prawej stronie)
    for slot, pos in layout.items():
        item = victim_equip.get(slot)
        x = 470 + pos[0] * (slot_size + 8)
        y = 30 + pos[1] * (slot_size + 8)
        draw.rectangle([x, y, x + slot_size, y + slot_size], fill=(42, 44, 52, 255), outline=(60, 64, 75, 255), width=2)
        if item:
            item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
            if item_img:
                item_img = item_img.resize((slot_size, slot_size))
                img.paste(item_img, (x, y), item_img)

    # Linia podziału / Napis w środku
    draw.text((canvas_w // 2 - 15, 140), "VS", fill=(200, 200, 200, 255))

    # Plecak Ofiary na dole (Loot)
    if victim_inventory:
        draw.text((40, 280), "Plecak ofiary:", fill=(170, 170, 170, 255))
        inv_slot_size = 50
        for idx, item in enumerate(victim_inventory[:10]):
            x = 40 + idx * (inv_slot_size + 6)
            y = 300
            draw.rectangle([x, y, x + inv_slot_size, y + inv_slot_size], fill=(35, 37, 44, 255), outline=(55, 58, 68, 255))
            if item:
                item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
                if item_img:
                    item_img = item_img.resize((inv_slot_size, inv_slot_size))
                    img.paste(item_img, (x, y), item_img)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

# ==========================================
# PĘTLA SPRAWDZAJĄCA KILLBOARD
# ==========================================
@tasks.loop(seconds=30)
async def check_albion_kills():
    global config
    config = load_config()
    
    tracked = config.get("tracked_players", [])
    main_chan_id = config.get("main_channel_id")
    
    print(f"[PĘTLA] Śledzeni gracze: {tracked} | Kanał ID: {main_chan_id}", flush=True)

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
                    print(f"[API ERROR] Kod odpowiedzi API: {resp.status}", flush=True)
                    return
                events = await resp.json()
                print(f"[API OK] Pobrano {len(events)} ostatnich zdarzeń z serwera ({region.upper()}).", flush=True)
        except Exception as e:
            print(f"[API ERROR] Błąd połączenia: {e}", flush=True)
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

            victim_equip = event.get("Victim", {}).get("Equipment", {})
            killer_equip = event.get("Killer", {}).get("Equipment", {})
            victim_inventory = [item for item in event.get("Victim", {}).get("Inventory", []) if item]

            all_items = [item.get("Type") for item in victim_equip.values() if item]
            all_items += [item.get("Type") for item in victim_inventory if item]
            prices = await get_item_prices(session, all_items, region)
            
            equip_value = calculate_equipment_value(victim_equip, prices)
            inv_value = sum(prices.get(i.get("Type"), 0) * i.get("Count", 1) for i in victim_inventory)
            total_value = equip_value + inv_value
            kill_fame = event.get("TotalVictimKillFame", 0)

            val_str = f"{total_value / 1_000_000:.2f}M" if total_value >= 1_000_000 else f"{total_value / 1_000:.0f}k" if total_value >= 1_000 else "0"
            fame_str = f"{kill_fame / 1_000_000:.2f}M" if kill_fame >= 1_000_000 else f"{kill_fame / 1_000:.0f}k" if kill_fame >= 1_000 else f"{kill_fame}"

            threshold = config.get("high_value_threshold", 15000000)

            # Generowanie obrazka
            img_buffer = await generate_kill_image(session, killer_equip, victim_equip, victim_inventory)
            file = discord.File(fp=img_buffer, filename="killboard.png")

            embed_color = discord.Color.green() if is_killer else discord.Color.red() if is_victim else discord.Color.gold()

            embed = discord.Embed(
                title=f"⚔️ {killer_name}  ━━━  killed  ━━━  {victim_name}",
                url=f"https://albiononline.com/killboard/kill/{event_id}",
                color=embed_color
            )

            k_guild = event.get('Killer', {}).get('GuildName') or 'Brak'
            v_guild = event.get('Victim', {}).get('GuildName') or 'Brak'
            k_ip = round(event.get("Killer", {}).get("AverageItemPower", 0))
            v_ip = round(event.get("Victim", {}).get("AverageItemPower", 0))

            embed.add_field(name="🗡️ Zabójca", value=f"**{killer_name}** (`{k_ip} IP`)\n🏰 *{k_guild}*", inline=True)
            embed.add_field(name="🎯 Ofiara", value=f"**{victim_name}** (`{v_ip} IP`)\n🏰 *{v_guild}*", inline=True)
            embed.add_field(name="📊 Statystyki", value=f"☠️ Fame: `{fame_str}`\n💰 Loot: `{val_str} Silver`", inline=True)

            embed.set_image(url="attachment://killboard.png")
            embed.set_footer(text=f"Albion Online Killbot • Server: {region.upper()} • Event ID: {event_id}")

            try:
                await main_channel.send(file=file, embed=embed)
                print(f"[SUCCESS] Wysłano graficzne powiadomienie o killu!", flush=True)
            except Exception as send_error:
                print(f"[DISCORD ERROR] Błąd wysyłania: {send_error}", flush=True)

            if total_value >= threshold and big_channel:
                img_buffer.seek(0)
                big_file = discord.File(fp=img_buffer, filename="killboard.png")
                await big_channel.send(
                    content=f"🚨 **BIG KILL / LEGENDARNY LOOT (> {threshold/1_000_000:.0f}M)!** 🚨",
                    file=big_file,
                    embed=embed
                )

# ==========================================
# COMMANDS / SLASH COMMANDS
# ==========================================
@bot.tree.command(name="dodaj", description="Dodaj nowego gracza do śledzenia")
async def dodaj(interaction: discord.Interaction, nick: str):
    if nick in config["tracked_players"]:
        await interaction.response.send_message(f"⚠️ Gracz **{nick}** jest już na liście.", ephemeral=True)
        return
    
    config["tracked_players"].append(nick)
    save_config(config)
    await interaction.response.send_message(f"✅ Dodano gracza **{nick}** do monitorowania PK.", ephemeral=False)

@bot.tree.command(name="usun", description="Usuń gracza ze śledzenia")
async def usun(interaction: discord.Interaction, nick: str):
    if nick not in config["tracked_players"]:
        await interaction.response.send_message(f"❌ Gracz **{nick}** nie znajduje się na liście.", ephemeral=True)
        return
    
    config["tracked_players"].remove(nick)
    save_config(config)
    await interaction.response.send_message(f"🗑️ Usunięto gracza **{nick}** ze śledzenia.", ephemeral=False)

@bot.tree.command(name="lista", description="Wyświetl listę śledzonych graczy i konfigurację")
async def lista(interaction: discord.Interaction):
    players = config["tracked_players"]
    players_str = "\n".join([f"• {p}" for p in players]) if players else "Brak graczy"
    
    main_ch = f"<#{config['main_channel_id']}>" if config['main_channel_id'] else "Nie ustawiono"
    big_ch = f"<#{config['big_channel_id']}>" if config['big_channel_id'] else "Brak (wyłączone)"
    threshold_mln = config['high_value_threshold'] / 1_000_000

    embed = discord.Embed(title="⚙️ Konfiguracja Killbota Albion", color=discord.Color.blurple())
    embed.add_field(name="🌍 Region Serwera", value=config['server_region'].upper(), inline=True)
    embed.add_field(name="💰 Próg Big Kill", value=f"{threshold_mln:.1f}M Silver", inline=True)
    embed.add_field(name="📢 Kanał Główny", value=main_ch, inline=False)
    embed.add_field(name="🚨 Kanał Big Kills", value=big_ch, inline=False)
    embed.add_field(name=f"👥 Śledzeni gracze ({len(players)})", value=players_str[:1024], inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="ustaw_kanaly", description="Ustaw kanały powiadomień")
async def ustaw_kanaly(interaction: discord.Interaction, glowny: discord.TextChannel, duze_kille: discord.TextChannel = None):
    config["main_channel_id"] = glowny.id
    config["big_channel_id"] = duze_kille.id if duze_kille else None
    save_config(config)
    
    msg = f"✅ Ustawiono główny kanał powiadomień na {glowny.mention}!"
    if duze_kille:
        msg += f"\n🚨 Ustawiono kanał na kille > {config['high_value_threshold']/1_000_000:.0f}M na {duze_kille.mention}!"
    
    await interaction.response.send_message(msg, ephemeral=False)

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
        print("❌ BŁĄD: Brak zmiennej środowiskowej DISCORD_TOKEN!", flush=True)
    else:
        bot.run(TOKEN)
