import discord
from discord.ext import tasks, commands
import aiohttp
import json
import os
import io
import threading
from datetime import datetime
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
# KONFIGURACJA
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
# POMOCNICZE: CENY I IKONY
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
        async with session.get(url, timeout=8) as resp:
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

async def fetch_item_image(session, item_type, quality=1, size=217):
    """Pobiera oficjalną ikonę przedmiotu z render.albiononline.com"""
    if not item_type:
        return None
    url = f"https://render.albiononline.com/v1/item/{item_type}.png?quality={quality}&size={size}"
    try:
        async with session.get(url, timeout=6) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img
    except Exception:
        pass
    return None

async def fetch_asset_image(session, url):
    """Pobiera ikony pomocnicze (np. waluty/fame)"""
    try:
        async with session.get(url, timeout=4) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        pass
    return None

# ==========================================
# GENERATOR OBRAZKA – LAYOUT KILLBOARD#1 1:1
# ==========================================
async def generate_kill_image(session, killer_name, victim_name, killer_guild, victim_guild, 
                                killer_equip, victim_equip, victim_inventory, 
                                kill_fame=0, total_value=0, time_str=""):

    canvas_w, canvas_h = 840, 560
    bg_color = (218, 206, 185, 255)    # Charakterystyczne pergaminowe tło KillBoard#1
    slot_bg   = (198, 185, 162, 255)
    slot_border = (160, 145, 125, 255)

    img = Image.new("RGBA", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    # Fonty (z bezpiecznym fallbackiem)
    try:
        font_name   = ImageFont.truetype("arial.ttf", 17)
        font_guild  = ImageFont.truetype("arial.ttf", 12)
        font_stats  = ImageFont.truetype("arial.ttf", 14)
        font_small  = ImageFont.truetype("arial.ttf", 11)
        font_tiny   = ImageFont.truetype("arial.ttf", 10)
        font_death  = ImageFont.truetype("arial.ttf", 12)
        font_inv    = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font_name = font_guild = font_stats = font_small = font_tiny = font_death = font_inv = ImageFont.load_default()

    # -------------------------------------------------
    # 1. NICKI + GILDIE (Zabójca i Ofiara)
    # -------------------------------------------------
    draw.text((45, 18), killer_name, fill=(20, 20, 20), font=font_name)
    draw.text((45, 38), killer_guild or "No Guild", fill=(90, 85, 75), font=font_guild)

    v_name_w = draw.textlength(victim_name, font=font_name)
    v_guild_w = draw.textlength(victim_guild or "No Guild", font=font_guild)
    draw.text((canvas_w - 45 - v_name_w, 18), victim_name, fill=(20, 20, 20), font=font_name)
    draw.text((canvas_w - 45 - v_guild_w, 38), victim_guild or "No Guild", fill=(90, 85, 75), font=font_guild)

    # -------------------------------------------------
    # 2. SLOTY EKWIPUNKU (Układ 3x4 + Mount)
    # -------------------------------------------------
    slot_size = 54
    gap = 5

    # Lewa strona (Zabójca)
    k_base_x = 45
    k_base_y = 68
    k_slots = {
        "Bag":      (k_base_x,                     k_base_y),
        "Head":     (k_base_x + slot_size + gap,   k_base_y),
        "Cape":     (k_base_x + 2*(slot_size + gap), k_base_y),

        "MainHand": (k_base_x,                     k_base_y + slot_size + gap),
        "Armor":    (k_base_x + slot_size + gap,   k_base_y + slot_size + gap),
        "OffHand":  (k_base_x + 2*(slot_size + gap), k_base_y + slot_size + gap),

        "Food":     (k_base_x,                     k_base_y + 2*(slot_size + gap)),
        "Shoes":    (k_base_x + slot_size + gap,   k_base_y + 2*(slot_size + gap)),
        "Potion":   (k_base_x + 2*(slot_size + gap), k_base_y + 2*(slot_size + gap)),

        "Mount":    (k_base_x + slot_size + gap,   k_base_y + 3*(slot_size + gap) + 4),
    }

    # Prawa strona (Ofiara)
    v_base_x = canvas_w - 45 - 3*slot_size - 2*gap
    v_base_y = 68
    v_slots = {
        "Bag":      (v_base_x,                     v_base_y),
        "Head":     (v_base_x + slot_size + gap,   v_base_y),
        "Cape":     (v_base_x + 2*(slot_size + gap), v_base_y),

        "MainHand": (v_base_x,                     v_base_y + slot_size + gap),
        "Armor":    (v_base_x + slot_size + gap,   v_base_y + slot_size + gap),
        "OffHand":  (v_base_x + 2*(slot_size + gap), v_base_y + slot_size + gap),

        "Food":     (v_base_x,                     v_base_y + 2*(slot_size + gap)),
        "Shoes":    (v_base_x + slot_size + gap,   v_base_y + 2*(slot_size + gap)),
        "Potion":   (v_base_x + 2*(slot_size + gap), v_base_y + 2*(slot_size + gap)),

        "Mount":    (v_base_x + slot_size + gap,   v_base_y + 3*(slot_size + gap) + 4),
    }

    async def draw_equipment_slots(slots_dict, equip_data):
        for slot_name, (x, y) in slots_dict.items():
            draw.rounded_rectangle(
                [x, y, x + slot_size, y + slot_size],
                radius=4,
                fill=slot_bg,
                outline=slot_border,
                width=2
            )

            item = equip_data.get(slot_name) if equip_data else None
            if item and item.get("Type"):
                item_img = await fetch_item_image(session, item["Type"], item.get("Quality", 1), size=160)
                if item_img:
                    item_img = item_img.resize((slot_size + 8, slot_size + 8), Image.Resampling.LANCZOS)
                    img.paste(item_img, (x - 4, y - 4), item_img)

                cnt = item.get("Count", 1)
                if cnt > 1:
                    draw.text((x + 4, y + slot_size - 14), str(cnt), fill=(0, 0, 0, 180), font=font_tiny)
                    draw.text((x + 3, y + slot_size - 15), str(cnt), fill=(255, 255, 255), font=font_tiny)

    await draw_equipment_slots(k_slots, killer_equip)
    await draw_equipment_slots(v_slots, victim_equip)

    # -------------------------------------------------
    # 3. ŚRODEK – FAME / SILVER / DEATH (Z ORYGINALNYMI IKONAMI)
    # -------------------------------------------------
    center_x = canvas_w // 2

    # Pobieranie oryginalnych ikon z CDN Albiona dla Fame i Silver
    fame_icon_url = "https://render.albiononline.com/v1/item/T8_QUESTITEM_TOKEN_ROYAL_CHAMPION.png"
    silver_icon_url = "https://render.albiononline.com/v1/item/T8_RESOURCES_ORE_LEVEL3.png"

    fame_img_obj = await fetch_asset_image(session, fame_icon_url)
    silver_img_obj = await fetch_asset_image(session, silver_icon_url)

    # --- FAME ---
    if fame_img_obj:
        fi = fame_img_obj.resize((22, 22), Image.Resampling.LANCZOS)
        img.paste(fi, (center_x - 65, 74), fi)
    else:
        draw.polygon([(center_x - 54, 74), (center_x - 44, 84), (center_x - 54, 94), (center_x - 64, 84)], fill=(160, 35, 35))

    draw.text((center_x - 38, 73), f"{kill_fame:,}", fill=(30, 25, 20), font=font_stats)
    if kill_fame > 0:
        draw.text((center_x - 38, 91), f"3 × {kill_fame // 3:,}", fill=(100, 95, 85), font=font_small)

    # --- SILVER ---
    if silver_img_obj:
        si = silver_img_obj.resize((22, 22), Image.Resampling.LANCZOS)
        img.paste(si, (center_x - 65, 128), si)
    else:
        draw.ellipse([center_x - 65, 128, center_x - 45, 148], fill=(200, 160, 40))

    draw.text((center_x - 38, 128), f"{total_value:,}", fill=(30, 25, 20), font=font_stats)

    # --- DEATH (Kółko ze czaszką) ---
    death_y = 195
    radius = 24
    draw.ellipse(
        [center_x - radius, death_y - radius, center_x + radius, death_y + radius],
        fill=(45, 42, 38)
    )
    draw.text((center_x - 20, death_y - 6), "DEATH", fill=(240, 240, 240), font=font_death)

    if time_str:
        t_w = draw.textlength(time_str, font=font_small)
        draw.text((center_x - t_w // 2, death_y + 32), time_str, fill=(90, 85, 75), font=font_small)

    # -------------------------------------------------
    # 4. VICTIM'S INVENTORY (9 kolumn x 3 wiersze)
    # -------------------------------------------------
    inv_title_y = 315
    inv_title_w = draw.textlength("VICTIM'S INVENTORY", font=font_inv)
    draw.text((center_x - inv_title_w // 2, inv_title_y), "VICTIM'S INVENTORY", fill=(75, 70, 60), font=font_inv)

    inv_slot = 46
    inv_gap = 5
    max_cols = 9
    max_rows = 3

    items = [i for i in (victim_inventory or []) if i and i.get("Type")]
    items = items[:max_cols * max_rows]

    total_slots = max_cols * max_rows
    start_x = (canvas_w - (max_cols * inv_slot + (max_cols - 1) * inv_gap)) // 2
    start_y = inv_title_y + 24

    for idx in range(total_slots):
        col = idx % max_cols
        row = idx // max_cols
        x = start_x + col * (inv_slot + inv_gap)
        y = start_y + row * (inv_slot + inv_gap)

        draw.rounded_rectangle(
            [x, y, x + inv_slot, y + inv_slot],
            radius=4,
            fill=slot_bg,
            outline=slot_border,
            width=1
        )

        if idx < len(items):
            item = items[idx]
            item_img = await fetch_item_image(session, item["Type"], item.get("Quality", 1), size=140)
            if item_img:
                item_img = item_img.resize((inv_slot + 6, inv_slot + 6), Image.Resampling.LANCZOS)
                img.paste(item_img, (x - 3, y - 3), item_img)

            cnt = item.get("Count", 1)
            if cnt > 1:
                draw.text((x + 3, y + inv_slot - 14), str(cnt), fill=(0, 0, 0, 160), font=font_tiny)
                draw.text((x + 2, y + inv_slot - 15), str(cnt), fill=(255, 255, 255), font=font_tiny)

    # -------------------------------------------------
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
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
            async with session.get(url, timeout=12) as resp:
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
            if len(seen_event_ids) > 2500:
                seen_event_ids.pop()

            killer = event.get("Killer", {})
            victim = event.get("Victim", {})
            killer_name = killer.get("Name", "")
            victim_name = victim.get("Name", "")
            participants = event.get("Participants", [])
            participant_names = [p.get("Name") for p in participants if p.get("Name")]

            is_killer = killer_name.lower() in tracked_lower
            is_victim = victim_name.lower() in tracked_lower
            is_participant = any(p.lower() in tracked_lower for p in participant_names if p)

            if not (is_killer or is_victim or is_participant):
                continue

            victim_equip = victim.get("Equipment", {})
            killer_equip = killer.get("Equipment", {})
            victim_inventory = [item for item in victim.get("Inventory", []) if item]

            all_items = [item.get("Type") for item in victim_equip.values() if item]
            all_items += [item.get("Type") for item in victim_inventory if item]
            prices = await get_item_prices(session, all_items, region)
            
            equip_value = calculate_equipment_value(victim_equip, prices)
            inv_value = sum(prices.get(i.get("Type"), 0) * i.get("Count", 1) for i in victim_inventory)
            total_value = equip_value + inv_value
            kill_fame = event.get("TotalVictimKillFame", 0)

            k_guild = killer.get("GuildName") or ""
            v_guild = victim.get("GuildName") or ""
            time_raw = event.get("TimeStamp", "")
            time_formatted = ""
            if time_raw:
                try:
                    dt = datetime.fromisoformat(time_raw.replace("Z", "+00:00"))
                    time_formatted = dt.strftime("%H:%M %d %b %Y")
                except Exception:
                    time_formatted = time_raw[:16].replace("T", " ")

            threshold = config.get("high_value_threshold", 15000000)

            img_buffer = await generate_kill_image(
                session, killer_name, victim_name, k_guild, v_guild,
                killer_equip, victim_equip, victim_inventory,
                kill_fame, total_value, time_formatted
            )
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
            except Exception:
                pass

            if total_value >= threshold and big_channel:
                img_buffer.seek(0)
                big_file = discord.File(fp=img_buffer, filename="killboard.png")
                await big_channel.send(
                    content="🚨 **BIG KILL / LEGENDARNY LOOT!** 🚨",
                    file=big_file,
                    embed=embed
                )

# ==========================================
# KOMENDY
# ==========================================
@bot.tree.command(name="test_kill", description="Testowy killboard w stylu KillBoard#1")
async def test_kill(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    mock_killer_equip = {
        "Bag":      {"Type": "T4_BAG", "Quality": 1},
        "Head":     {"Type": "T5_HEAD_PLATE_HELM", "Quality": 2},
        "Cape":     {"Type": "T4_CAPE", "Quality": 1},
        "MainHand": {"Type": "T8_MAIN_SWORD", "Quality": 3},
        "Armor":    {"Type": "T5_ARMOR_CLOTH_SET", "Quality": 1},
        "OffHand":  {"Type": "T4_OFF_SHIELD", "Quality": 1},
        "Food":     {"Type": "T6_MEAL_STEW", "Quality": 1},
        "Shoes":    {"Type": "T5_SHOES_LEATHER", "Quality": 1},
        "Potion":   {"Type": "T4_POTION_HEAL", "Quality": 1},
        "Mount":    {"Type": "T5_MOUNT_HORSE", "Quality": 1}
    }

    mock_victim_equip = {
        "Bag":      {"Type": "T5_BAG", "Quality": 1},
        "Head":     {"Type": "T4_HEAD_LEATHER", "Quality": 1},
        "Cape":     {"Type": "T4_CAPE", "Quality": 1},
        "MainHand": {"Type": "T4_MAIN_SWORD", "Quality": 1},
        "Armor":    {"Type": "T4_ARMOR_PLATE", "Quality": 1},
        "OffHand":  {"Type": "T4_OFF_SHIELD", "Quality": 1},
        "Food":     {"Type": "T6_MEAL_STEW", "Quality": 1},
        "Shoes":    {"Type": "T4_SHOES_PLATE", "Quality": 1},
        "Potion":   {"Type": "T4_POTION_HEAL", "Quality": 1},
        "Mount":    {"Type": "T3_MOUNT_HORSE", "Quality": 1}
    }

    mock_inventory = [
        {"Type": "T4_MAIN_SWORD", "Count": 1},
        {"Type": "T4_MAIN_SWORD", "Count": 9},
        {"Type": "T4_MAIN_SWORD", "Count": 11},
        {"Type": "T5_ARMOR_LEATHER", "Count": 24},
        {"Type": "T6_MEAL_STEW", "Count": 1},
        {"Type": "T6_ORE", "Count": 198},
        {"Type": "T6_ORE", "Count": 18},
        {"Type": "T6_METAL", "Count": 14},
        {"Type": "T6_METAL", "Count": 56},
        {"Type": "T5_METAL", "Count": 142}
    ]

    async with aiohttp.ClientSession() as session:
        img_buffer = await generate_kill_image(
            session=session,
            killer_name="AwesomeNinja",
            victim_name="MitchDagger",
            killer_guild="BYOT",
            victim_guild="G3rm Craft",
            killer_equip=mock_killer_equip,
            victim_equip=mock_victim_equip,
            victim_inventory=mock_inventory,
            kill_fame=2853543,
            total_value=102530668,
            time_str="22:49 03 Jan 2022"
        )
        
        file = discord.File(fp=img_buffer, filename="killboard.png")
        embed = discord.Embed(
            title="🧪 Test Killboard (KillBoard#1 Style)",
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
        print("⚔️ Pętla sprawdzająca kille uruchomiona!", flush=True)

if __name__ == "__main__":
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ BŁĄD: Brak DISCORD_TOKEN!", flush=True)
    else:
        bot.run(TOKEN)
