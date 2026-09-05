import os
os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
os.environ.setdefault("SDL_FBDEV", "/dev/fb0")

import io, json, math, threading, time
from datetime import datetime
from urllib.request import urlopen

import pygame
try:
    import RPi.GPIO as GPIO
except ImportError:
    class FakeGPIO:
        BCM = IN = PUD_UP = HIGH = LOW = None
        def setmode(self, *_): pass
        def setup(self, *_args, **_kwargs): pass
        def input(self, *_): return 1
        def cleanup(self): pass
    GPIO = FakeGPIO()

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_URL = "http://127.0.0.1:5000/data"
WIDTH, HEIGHT = 1024, 600
FPS = 30
PIN = 17

C = {"bg":"#121212","card":"#171819","border":"#232427","inner":"#1c1e21","accent":"#48a0dc",
     "positive":"#32cd32","negative":"#e03232","primary":"#e8e8e8","secondary":"#8a8a8a","white":"#ffffff",
     "yellow":"#f0c84b"}

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
pygame.display.set_caption("Smart Desk Dashboard")
clock = pygame.time.Clock()

FONT_FILE = os.path.join(BASE_DIR, "BebasNeue-Regular.ttf")
def font(size):
    try: return pygame.font.Font(FONT_FILE, size)
    except Exception: return pygame.font.SysFont("DejaVu Sans", size)

F = {s: font(s) for s in (72,64,48,36,30,28,22,18,17,15,14,13,12,11,10,9)}

data = {"canvas":[],"calendar":[],"weather":{},"spotify":{"is_playing":False},"markets":[],"news":[],"afl":{}}
data_lock = threading.Lock()
art_cache = {}
page = 1
fixture_index = 0
fixture_menu = False
button_down_at = None
last_press = 0
news_scroll = 0.0


def col(name): return pygame.Color(C.get(name, name))
def draw_card(rect, radius=12):
    pygame.draw.rect(screen, col("card"), rect, border_radius=radius)
    pygame.draw.rect(screen, col("border"), rect, width=1, border_radius=radius)

def text(txt, xy, size=18, color="primary", anchor="topleft"):
    surf = F[size].render(str(txt), True, col(color))
    r = surf.get_rect()
    setattr(r, anchor, xy); screen.blit(surf, r)
    return r

def truncate(txt, max_width, size):
    txt = str(txt or "")
    if F[size].size(txt)[0] <= max_width: return txt
    while txt and F[size].size(txt + "…")[0] > max_width: txt = txt[:-1]
    return txt + "…"

def fetch_data():
    global data
    while True:
        try:
            d = requests.get(DATA_URL, timeout=3).json()
            with data_lock: data = d
        except Exception: pass
        time.sleep(3)

def weather_name(code):
    return {0:"Clear",1:"Clear",2:"Partly Cloudy",3:"Partly Cloudy",45:"Fog",48:"Fog",51:"Rain",53:"Rain",55:"Rain",61:"Rain",63:"Rain",65:"Rain",71:"Snow",73:"Snow",75:"Snow",80:"Rain",81:"Rain",82:"Rain",95:"Storm",96:"Storm",99:"Storm"}.get(code,"Weather")

def draw_weather_icon(x,y,code,scale=1):
    cloud = col("white"); blue = col("accent"); grey = col("secondary"); yellow = col("yellow")
    if code in (0,1):
        pygame.draw.circle(screen, yellow, (int(x),int(y)), int(22*scale), width=3)
        for a in range(0,360,45):
            dx,dy=math.cos(math.radians(a))*34*scale, math.sin(math.radians(a))*34*scale
            pygame.draw.line(screen,yellow,(x+dx*.75,y+dy*.75),(x+dx,y+dy),width=max(1,int(3*scale)))
    else:
        pygame.draw.circle(screen, cloud if code not in (95,96,99) else grey,(int(x-18*scale),int(y+4*scale)),int(18*scale))
        pygame.draw.circle(screen, cloud if code not in (95,96,99) else grey,(int(x+4*scale),int(y-7*scale)),int(25*scale))
        pygame.draw.rect(screen, cloud if code not in (95,96,99) else grey,(x-28*scale,y+4*scale,58*scale,25*scale),border_radius=int(12*scale))
        if code in (51,53,55,61,63,65,80,81,82):
            for dx in (-14,2,18): pygame.draw.line(screen,blue,(x+dx*scale,y+35*scale),(x+(dx-5)*scale,y+49*scale),width=max(2,int(3*scale)))
        if code in (95,96,99):
            pygame.draw.polygon(screen,yellow,[(x+4*scale,y+27*scale),(x+17*scale,y+27*scale),(x+7*scale,y+43*scale),(x+16*scale,y+43*scale),(x-1*scale,y+64*scale),(x+3*scale,y+47*scale),(x-5*scale,y+47*scale)])

def page1():
    now = datetime.now()
    draw_card(pygame.Rect(20,18,984,110)); text(now.strftime("%H:%M"),(45,30),72); text(now.strftime("%A, %d %B %Y"),(675,44),22); text("Melbourne, AU",(675,78),18,"secondary")
    draw_card(pygame.Rect(20,145,482,435)); text("▣  ASSIGNMENTS",(40,164),22,"accent")
    y=205
    with data_lock: assignments=list(data.get("canvas",[])); events=list(data.get("calendar",[]))
    for a in assignments[:10]:
        pygame.draw.circle(screen,col("accent"),(48,y+8),4); text(truncate(a.get("name"),330,13),(62,y),13); text(a.get("due_formatted",""),(62,y+20),9,"secondary"); y+=39
    draw_card(pygame.Rect(522,145,482,435)); text("▣  CALENDAR",(542,164),22,"accent")
    y=205; lastday=""
    for e in events:
        dt=e.get("start_iso","")[:10]
        if dt!=lastday: text(dt,(545,y),12,"yellow"); y+=22; lastday=dt
        pygame.draw.circle(screen,col("yellow" if e.get("all_day") else "accent"),(550,y+7),4)
        text(truncate(e.get("title"),390,11),(565,y),11); text(e.get("start_formatted",""),(565,y+17),9,"secondary"); y+=38
        if y>552: break

def page2():
    draw_card(pygame.Rect(20,20,984,560))
    with data_lock: s=dict(data.get("spotify",{}))
    if not s.get("is_playing") and not s.get("track_name"):
        text("Nothing Playing",(512,280),36,"secondary","center"); return
    art=s.get("album_art_url")
    if art:
        try:
            if art not in art_cache: art_cache[art]=pygame.image.load(io.BytesIO(urlopen(art,timeout=5).read())).convert()
            img=pygame.transform.smoothscale(art_cache[art],(310,310)); screen.blit(img,(55,145))
        except Exception: pass
    text(truncate(s.get("track_name",""),560,30),(400,170),30); text(truncate(s.get("artist_name",""),560,17),(400,218),17,"accent"); text(truncate(s.get("album_name",""),560,13),(400,247),13,"secondary")
    prog=max(0,min(1,(s.get("progress_ms") or 0)/max(1,s.get("duration_ms") or 1)))
    pygame.draw.rect(screen,col("inner"),pygame.Rect(400,320,540,8),border_radius=4); pygame.draw.rect(screen,col("accent"),pygame.Rect(400,320,int(540*prog),8),border_radius=4)
    text(f"{(s.get('progress_ms',0)//1000)//60}:{(s.get('progress_ms',0)//1000)%60:02d}",(400,338),10,"secondary"); dur=s.get("duration_ms",0)//1000; text(f"{dur//60}:{dur%60:02d}",(940,338),10,"secondary","topright")

def page3():
    draw_card(pygame.Rect(20,20,984,220)); draw_card(pygame.Rect(20,260,984,320))
    with data_lock: afl=dict(data.get("afl",{})); games=afl.get("games",[])
    game=games[0] if games else {}
    teams=[game.get("hteam","BRI"),game.get("ateam","CAR")]; scores=[game.get("hscore",0),game.get("ascore",0)]
    text(str(teams[0])[:4].upper(),(160,65),26,"accent","center"); text(str(scores[0]),(160,102),40); text("HOME",(160,155),9,"secondary","center")
    text(str(teams[1])[:4].upper(),(864,65),26,"accent","center"); text(str(scores[1]),(864,102),40); text("AWAY",(864,155),9,"secondary","center")
    text("LIVE" if game.get("complete") is False else "UPCOMING",(512,70),14,"yellow","center"); text(str(game.get("round","")),(512,105),11,"secondary","center")
    pygame.draw.rect(screen,col("accent"),pygame.Rect(45,195,934,3))
    stats=[("Disposals","—"),("Marks","—"),("Tackles","—"),("Inside 50s","—"),("Clearances","—"),("Score Shots","—")]
    for i,(label,val) in enumerate(stats):
        x=65+(i%3)*315; y=300+(i//3)*120; text(label,(x,y),14,"secondary"); text(val,(x,y+28),28)
    if fixture_menu:
        pygame.draw.rect(screen,col("bg"),pygame.Rect(240,105,544,390),border_radius=14); pygame.draw.rect(screen,col("border"),pygame.Rect(240,105,544,390),1,border_radius=14)
        opts=["Brisbane vs Carlton — LIVE","Richmond vs Sydney — LIVE","Collingwood vs Essendon — UPCOMING","View Ladder"]
        for i,o in enumerate(opts): text(("> " if i==fixture_index else "  ")+o,(275,145+i*55),18,"accent" if i==fixture_index else "primary")
        text("Short press: next    Long press: select",(275,370),12,"secondary")

def page4():
    draw_card(pygame.Rect(20,20,984,560))
    with data_lock: w=dict(data.get("weather",{}))
    cur=w.get("current",{}); code=cur.get("weather_code",0); text(f"{round(cur.get('temperature_2m',0))}°",(55,65),80); text(weather_name(code),(65,155),28,"accent"); text(f"Feels {round(cur.get('apparent_temperature',0))}°",(65,198),17,"secondary"); draw_weather_icon(315,110,code,1.3)
    daily=w.get("forecast",[]); high=daily[0].get("high") if daily else None; low=daily[0].get("low") if daily else None; text(f"H {round(high) if high is not None else '—'}°   L {round(low) if low is not None else '—'}°",(65,245),18)
    meta=[("Wind",f"{cur.get('wind_speed_10m','—')} km/h"),("UV",str(cur.get('uv_index','—'))),("Sunrise",str(w.get('sunrise',''))[-8:]),("Sunset",str(w.get('sunset',''))[-8:]),("Rain Today",f"{w.get('rain_today_mm',0)} mm")]
    y=70
    for k,v in meta: text(k,(535,y),14,"secondary"); text(v,(760,y),16); y+=48
    text("7-DAY",(65,350),18,"accent")
    for i,d in enumerate(daily[:7]):
        x=65+i*130; text(d.get("date","-")[5:],(x,390),12,"secondary"); draw_weather_icon(x+40,430,d.get("weather_code",0),.45); text(f"{round(d.get('high',0))}°",(x+18,475),15); text(f"{round(d.get('low',0))}°",(x+68,475),15,"secondary"); text(f"{d.get('precip_probability','—')}%",(x+35,510),10,"accent")

def page5():
    global news_scroll
    draw_card(pygame.Rect(20,20,455,560)); draw_card(pygame.Rect(495,20,509,560)); text("MARKETS",(42,42),22,"accent")
    with data_lock: markets=list(data.get("markets",[])); news=list(data.get("news",[]))
    for i,m in enumerate(markets[:6]):
        x=40+(i%2)*215; y=85+(i//2)*145; r=pygame.Rect(x,y,195,120); pygame.draw.rect(screen,col("inner"),r,border_radius=10)
        text(truncate(m.get("label",""),170,13),(x+12,y+12),13); ch=m.get("change_pct",0) or 0; text(f"{ch:+.2f}%",(x+12,y+39),13,"positive" if ch>=0 else "negative"); p=m.get("price"); text("—" if p is None else f"{p:,.2f}",(x+12,y+66),15)
    text("HEADLINES",(518,42),22,"accent")
    y=84-news_scroll
    for n in news:
        title=truncate(n.get("title",""),455,12); text(title,(518,y),12); text(n.get("source",""),(518,y+19),9,"secondary"); y+=48
    news_scroll += .25
    if y < 500: news_scroll=0

def main():
    global page, fixture_menu, fixture_index, button_down_at, last_press
    GPIO.setmode(GPIO.BCM); GPIO.setup(PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    threading.Thread(target=fetch_data,daemon=True).start()
    try:
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT: raise KeyboardInterrupt
            state=GPIO.input(PIN); now=time.monotonic()
            if state == GPIO.LOW and button_down_at is None: button_down_at=now
            if state == GPIO.HIGH and button_down_at is not None:
                held=now-button_down_at; button_down_at=None
                if now-last_press >= .25:
                    last_press=now
                    if held >= 1.5 and page==3:
                        fixture_menu=not fixture_menu
                    elif fixture_menu and page==3:
                        fixture_index=(fixture_index+1)%4
                    elif held < 1.5:
                        page=page%5+1
            screen.fill(col("bg"))
            try: [page1,page2,page3,page4,page5][page-1]()
            except Exception: text("Dashboard error",(512,280),36,"negative","center")
            pygame.display.flip(); clock.tick(FPS)
    finally:
        GPIO.cleanup(); pygame.quit()

if __name__=="__main__": main()
