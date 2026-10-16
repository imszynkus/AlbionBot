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
# POMOCNICZE: POBIERANIE CEN I IKON
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
    url = f"https://render.albiononline.com/v1/item/{item_type}.png?quality={quality}&size=80"
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img
    except Exception:
        pass
    return None

# ==========================================
# LOKALNY GENERATOR GRAFIKI (PILLOW)
# ==========================================
async def generate_kill_image(session, killer_equip, victim_equip, victim_inventory):
    canvas_w, canvas_h = 750, 420
    bg_color = (24, 25, 28, 255) # Nowoczesne ciemne tło Discord/Albion
    img = Image.new("RGBA", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    slot_size = 64
    spacing = 8
    
    # Układ ekwipunku Albiona w siatce (Kolumna, Wiersz)
    layout = {
        "Head": (1, 0), "Armor": (1, 1), "Shoes": (1, 2),
        "MainHand": (0, 1), "OffHand": (2, 1),
        "Cape": (2, 0), "Mount": (1, 3),
        "Food": (0, 2), "Potion": (0, 0)
    }

    # Rysowanie ekwipunku Zabójcy (Po lewej)
    start_x_killer = 40
    start_y = 30
    for slot, pos in layout.items():
        item = killer_equip.get(slot)
        x = start_x_killer + pos[0] * (slot_size + spacing)
        y = start_y + pos[1] * (slot_size + spacing)
        
        # Ramka slotu
        draw.rounded_rectangle([x, y, x + slot_size, y + slot_size], radius=6, fill=(35, 37, 43, 255), outline=(54, 57, 65, 255), width=2)
        if item and item.get("Type"):
            item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
            if item_img:
                item_img = item_img.resize((slot_size - 6, slot_size - 6))
                img.paste(item_img, (x + 3, y + 3), item_img)

    # Rysowanie ekwipunku Ofiary (Po prawej)
    start_x_victim = 450
    for slot, pos in layout.items():
        item = victim_equip.get(slot)
        x = start_x_victim + pos[0] * (slot_size + spacing)
        y = start_y + pos[1] * (slot_size + spacing)
        
        draw.rounded_rectangle([x, y, x + slot_size, y + slot_size], radius=6, fill=(35, 37, 43, 255), outline=(54, 57, 65, 255), width=2)
        if item and item.get("Type"):
            item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
            if item_img:
                item_img = item_img.resize((slot_size - 6, slot_size - 6))
                img.paste(item_img, (x + 3, y + 3), item_img)

    # Etykieta VS na środku
    draw.text((canvas_w // 2 - 12, 130), "VS", fill=(140, 145, 155, 255))

    # Plecak Ofiary na dole (Loot)
    if victim_inventory:
        draw.text((40, 310), "Plecak / Loot ofiary:", fill=(180, 185, 195, 255))
        inv_slot_size = 48
        inv_spacing = 6
        for idx, item in enumerate(victim_inventory[:10]):
            x = 40 + idx * (inv_slot_size + inv_spacing)
            y = 335
            draw.rounded_rectangle([x, y, x + inv_slot_size, y + inv_slot_size], radius=4, fill=(30, 32, 38, 255), outline=(48, 52, 60, 255))
            if item and item.get("Type"):
                item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
                if item_img:
                    item_img = item_img.resize((inv_slot_size - 4, inv_slot_size - 4))
                    img.paste(item_img, (x + 2, y + 2), item_img)

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

            img_buffer = await generate_kill_image(session, killer_equip, victim_equip, victim_inventory)
            file = discord.File(fp=img_buffer, filename="killboard.png")

            embed_color = discord.Color.green() if is_killer else discord.Color.red() if is_victim else discord.Color.gold()
            embed = discord.Embed(
                title=f"⚔️ {killer_name}  ➔  {victim_name}",
                url=f"https://albiononline.com/killboard/kill/{event_id}",
                color=embed_color
            )
            embed.set_image(url="attachment://killboard.png")

            try:
                await main_channel.send(file=file, embed=embed)
            except Exception as e:
                print(f"[ERROR] {e}", flush=True)

# ==========================================
# KOMENDY DISCORDA (W TYM /TEST_KILL)
# ==========================================
@bot.tree.command(name="test_kill", description="Wygeneruj testowy obrazek killboardu na żywo")
async def test_kill(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    # Przykładowe przedmioty do testu graficznego
    mock_killer_equip = {
        "MainHand": {"Type": "T8_MAIN_SWORD", "Quality": 3},
        "Armor": {"Type": "T8_ARMOR_LEATHER_SET", "Quality": 2},
        "Head": {"Type": "T8_HEAD_LEATHER", "Quality": 1},
        "Shoes": {"Type": "T8_SHOES_LEATHER", "Quality": 1},
        "Cape": {"Type": "T8_CAPE", "Quality": 1}
    }
    mock_victim_equip = {
        "MainHand": {"Type": "T7_MAIN_AXE", "Quality": 2},
        "Armor": {"Type": "T7_ARMOR_PLATE", "Quality": 1},
        "Head": {"Type": "T7_HEAD_PLATE", "Quality": 1},
        "Shoes": {"Type": "T7_SHOES_PLATE", "Quality": 1},
        "Mount": {"Type": "T5_MOUNT_HORSE", "Quality": 1}
    }
    mock_inventory = [
        {"Type": "T8_ORE", "Count": 120},
        {"Type": "T6_POTION_HEAL", "Count": 15},
        {"Type": "T7_MEAL_STEW", "Count": 5},
        {"Type": "T8_BAG", "Count": 1}
    ]

    async with aiohttp.ClientSession() as session:
        img_buffer = await generate_kill_image(session, mock_killer_equip, mock_victim_equip, mock_inventory)
        file = discord.File(fp=img_buffer, filename="killboard.png")
        
        embed = discord.Embed(
            title="🧪 TESTOWY KILLBOARD (Pillow Preview)",
            description="Tak wygląda lokalnie generowany kolaż graficzny ekwipunku i plecaka.",
            color=discord.Color.blurple()
        )
        embed.set_image(url="attachment://killboard.png")
        
        await interaction.followup.send(file=file, embed=embed)

@bot.tree.command(name="dodaj", description="Dodaj gracza do śledzenia")
async def dodaj(interaction: discord.Interaction, nick: str):
    if nick in config["tracked_players"]:
        await interaction.response.send_message(f"⚠️ Gracz **{nick}** już jest śledzony.", ephemeral=True)
        return
    config["tracked_players"].append(nick)
    save_config(config)
    await interaction.response.send_message(f"✅ Dodano gracza **{nick}**.", ephemeral=False)

@bot.tree.command(name="usun", description="Usuń gracza ze śledzenia")
async def usun(interaction: discord.Interaction, nick: str):
    if nick not in config["tracked_players"]:
        await interaction.response.send_message(f"❌ Gracz **{nick}** nie jest śledzony.", ephemeral=True)
        return
    config["tracked_players"].remove(nick)
    save_config(config)
    await interaction.response.send_message(f"🗑️ Usunięto gracza **{nick}**.", ephemeral=False)

@bot.tree.command(name="lista", description="Lista śledzonych graczy")
async def lista(interaction: discord.Interaction):
    players = config["tracked_players"]
    players_str = "\n".join([f"• {p}" for p in players]) if players else "Brak"
    embed = discord.Embed(title="⚙️ Konfiguracja", color=discord.Color.blurple())
    embed.add_field(name="Śledzeni gracze", value=players_str, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="ustaw_kanaly", description="Ustaw kanał powiadomień")
async def ustaw_kanaly(interaction: discord.Interaction, glowny: discord.TextChannel):
    config["main_channel_id"] = glowny.id
    save_config(config)
    await interaction.response.send_message(f"✅ Ustawiono kanał główny na {glowny.mention}!", ephemeral=False)

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
        print("❌ BŁĄD: Brak DISCORD_TOKEN!", flush=True)
    else:
        bot.run(TOKEN)
