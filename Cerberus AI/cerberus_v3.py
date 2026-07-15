import os
import re
import json
import wave
import base64
import asyncio
import tempfile
import subprocess
import webbrowser
import datetime
import requests
import pyautogui
import numpy as np
import sounddevice as sd
import webrtcvad

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
SILENCE_LIMIT_MS = 1800
MIN_SPEECH_MS = 300
MAX_RECORD_SECONDS = 25
VAD_AGGRESSIVENESS = 1

GROQ_MODEL = "openai/gpt-oss-120b"
EDGE_VOICE = "tr-TR-AhmetNeural"
HAFIZA_DOSYASI = os.path.join(os.path.expanduser("~"), "cerberus_hafiza.json")
MAX_HAFIZA_MESAJ = 60

CONVERSATION_HISTORY = []

SYSTEM_PROMPT = """Senin adın Cerberus. Kullanıcının kişisel sesli asistanısın.
Seni Ensar Demirtaş yaptı. Kim yaptığın sorulursa bu ismi söyle.

Türkçeyi gerçek bir insan gibi, günlük konuşma diliyle kullan. Resmi, robotik
veya çeviri kokan cümleler kurma. Kısa ve net konuş, gereksiz tekrar yapma,
kullanıcının söylediğini papağan gibi tekrarlama. Sesli okunacak, o yüzden
madde işaretleri veya markdown kullanma.

TOOL KURALLARI:
- Kullanıcı açıkça bir görev istediğinde uygun tool'u çağır.
- Saat, tarih veya gün sorulduğunda MUTLAKA saat_ve_tarih_soyle çağır, tahmin etme.
- ekrana_tikla fonksiyonunu SADECE kullanıcı net bir x,y koordinatı verdiyse çağır, sen kendin koordinat uydurma. Senin ekranı görme yeteneğin yok.
- "selam", "naber", "nasılsın" gibi sohbet cümlelerinde hiçbir tool çağırma.
- Tool çağırman gerekiyorsa SADECE sistemin sağladığı fonksiyon çağırma mekanizmasıyla yap, cevap metninin içine asla <function=...> gibi yazı yazma."""


def log(msg):
    print(f"[Cerberus] {msg}")


SAHTE_TOOL_REGEX = re.compile(r"<function=([a-zA-Z_][a-zA-Z0-9_]*)>\s*(\{.*?\})\s*</function>", re.DOTALL)


def metinden_sahte_tool_cagrilarini_ayikla(content: str):
    if not content:
        return [], content
    eslesmeler = SAHTE_TOOL_REGEX.findall(content)
    temiz_metin = SAHTE_TOOL_REGEX.sub("", content).strip()
    cagrilar = []
    for isim, json_str in eslesmeler:
        try:
            args = json.loads(json_str)
        except json.JSONDecodeError:
            args = {}
        cagrilar.append((isim, args))
    return cagrilar, temiz_metin


def hafizayi_yukle():
    global CONVERSATION_HISTORY
    if os.path.exists(HAFIZA_DOSYASI):
        try:
            with open(HAFIZA_DOSYASI, "r", encoding="utf-8") as f:
                CONVERSATION_HISTORY = json.load(f)
            log(f"Geçmiş hafıza yüklendi ({len(CONVERSATION_HISTORY)} mesaj).")
        except Exception as e:
            log(f"Hafıza dosyası okunamadı, sıfırdan başlanıyor: {e}")
            CONVERSATION_HISTORY = []
    else:
        CONVERSATION_HISTORY = []


def hafizayi_kaydet():
    try:
        kaydedilecek = CONVERSATION_HISTORY[-MAX_HAFIZA_MESAJ:]
        with open(HAFIZA_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(kaydedilecek, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Hafıza kaydedilemedi: {e}")


UYGULAMA_HARITASI = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "notepad": "notepad",
    "not defteri": "notepad",
    "hesap makinesi": "calc",
    "hesap makinası": "calc",
    "calculator": "calc",
    "explorer": "explorer",
    "dosya gezgini": "explorer",
    "word": "winword",
    "excel": "excel",
    "spotify": "spotify",
    "paint": "mspaint",
    "görev yöneticisi": "taskmgr",
    "task manager": "taskmgr",
}

GUN_ISIMLERI = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
AY_ISIMLERI = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

_spotify_token_cache = {"token": None, "expires_at": 0}


def uygulama_ac(uygulama_adi: str) -> str:
    key = uygulama_adi.strip().lower()
    komut = UYGULAMA_HARITASI.get(key, key)
    try:
        subprocess.Popen(komut, shell=True)
        return f"{uygulama_adi} açıldı."
    except Exception as e:
        return f"{uygulama_adi} açılamadı: {e}"


def klasor_ac(yol: str) -> str:
    try:
        klasor = os.path.expandvars(os.path.expanduser(yol))
        subprocess.Popen(f'explorer "{klasor}"', shell=True)
        return f"{yol} klasörü açıldı."
    except Exception as e:
        return f"Klasör açılamadı: {e}"


def web_arama_yap(sorgu: str) -> str:
    url = f"https://www.google.com/search?q={sorgu.replace(' ', '+')}"
    webbrowser.open(url)
    return f"'{sorgu}' için tarayıcıda arama açıldı."


def web_sitesi_ac(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    webbrowser.open(url)
    return f"{url} açıldı."


def youtube_video_ac(sorgu: str) -> str:
    try:
        r = requests.get(
            "https://www.youtube.com/results",
            params={"search_query": sorgu},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', r.text)
        if not match:
            webbrowser.open(f"https://www.youtube.com/results?search_query={sorgu.replace(' ', '+')}")
            return f"'{sorgu}' için YouTube arama sonuçları açıldı, otomatik video seçilemedi."
        video_id = match.group(1)
        webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")
        return f"'{sorgu}' için ilk YouTube videosu açıldı."
    except Exception as e:
        webbrowser.open(f"https://www.youtube.com/results?search_query={sorgu.replace(' ', '+')}")
        return f"YouTube araması açıldı, video otomatik seçilemedi: {e}"


def spotify_token_al():
    now = datetime.datetime.now().timestamp()
    if _spotify_token_cache["token"] and now < _spotify_token_cache["expires_at"]:
        return _spotify_token_cache["token"]
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
            timeout=6,
        )
        data = r.json()
        token = data.get("access_token")
        _spotify_token_cache["token"] = token
        _spotify_token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
        return token
    except Exception:
        return None


def spotify_sarki_ac(sarki_adi: str) -> str:
    token = spotify_token_al()
    if not token:
        sorgu = sarki_adi.replace(" ", "%20")
        try:
            os.startfile(f"spotify:search:{sorgu}")
        except Exception:
            webbrowser.open(f"https://open.spotify.com/search/{sorgu}")
        return f"Spotify API key ayarlanmamış, '{sarki_adi}' için sadece arama açıldı."
    try:
        r = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": sarki_adi, "type": "track", "limit": 1},
            timeout=6,
        )
        sonuc = r.json()
        items = sonuc.get("tracks", {}).get("items", [])
        if not items:
            return f"'{sarki_adi}' Spotify'da bulunamadı."
        track = items[0]
        uri = track["uri"]
        isim = track["name"]
        sanatci = track["artists"][0]["name"]
        try:
            os.startfile(uri)
        except Exception:
            webbrowser.open(track["external_urls"]["spotify"])
        return f"{sanatci} - {isim} Spotify'da açıldı ve çalıyor."
    except Exception as e:
        return f"Spotify şarkı açılamadı: {e}"


def medya_kontrol(eylem: str) -> str:
    haritalama = {
        "oynat": "playpause",
        "devam_et": "playpause",
        "duraklat": "playpause",
        "durdur": "playpause",
        "sonraki": "nexttrack",
        "sonraki_sarki": "nexttrack",
        "onceki": "prevtrack",
        "onceki_sarki": "prevtrack",
        "sesi_ac": "volumeup",
        "ses_artir": "volumeup",
        "sesi_kis": "volumedown",
        "ses_azalt": "volumedown",
        "sessize_al": "volumemute",
        "sesi_kapat": "volumemute",
    }
    tus = haritalama.get(eylem.strip().lower())
    if not tus:
        return f"Bilinmeyen medya komutu: {eylem}"
    pyautogui.press(tus)
    return f"{eylem} komutu gönderildi."


def hava_durumu_soyle(sehir: str) -> str:
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": sehir, "count": 1, "language": "tr"},
            timeout=6,
        ).json()
        if not geo.get("results"):
            return f"{sehir} için konum bulunamadı."
        yer = geo["results"][0]
        lat, lon = yer["latitude"], yer["longitude"]
        hava = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code", "timezone": "auto"},
            timeout=6,
        ).json()
        sicaklik = hava["current"]["temperature_2m"]
        return f"{sehir} için şu an sıcaklık {sicaklik} derece."
    except Exception as e:
        return f"Hava durumu alınamadı: {e}"


def ekran_goruntusu_al() -> str:
    goruntu = pyautogui.screenshot()
    dosya_adi = f"cerberus_ekran_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    masaustu = os.path.join(os.path.expanduser("~"), "Desktop")
    yol = os.path.join(masaustu, dosya_adi) if os.path.isdir(masaustu) else os.path.join(os.path.expanduser("~"), dosya_adi)
    goruntu.save(yol)
    return f"Ekran görüntüsü alındı: {yol}"


def panoya_kopyala(metin: str) -> str:
    import pyperclip
    pyperclip.copy(metin)
    return "Panoya kopyalandı."


def ekrana_tikla(x: int, y: int) -> str:
    pyautogui.click(x, y)
    return f"({x}, {y}) konumuna tıklandı."


def bilgisayari_kilitle() -> str:
    subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
    return "Bilgisayar kilitlendi."


def bilgisayari_uyut() -> str:
    subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
    return "Bilgisayar uyku moduna alınıyor."


def saat_ve_tarih_soyle() -> str:
    simdi = datetime.datetime.now()
    gun_adi = GUN_ISIMLERI[simdi.weekday()]
    ay_adi = AY_ISIMLERI[simdi.month - 1]
    return f"Şu an saat {simdi.strftime('%H:%M')}, bugün {simdi.day} {ay_adi} {simdi.year}, {gun_adi} günü."


TOOLS = [
    {"type": "function", "function": {"name": "uygulama_ac", "description": "Bilgisayarda bir uygulama açar (chrome, notepad, hesap makinesi, spotify, word, excel vs.)", "parameters": {"type": "object", "properties": {"uygulama_adi": {"type": "string", "description": "Açılacak uygulamanın adı"}}, "required": ["uygulama_adi"]}}},
    {"type": "function", "function": {"name": "klasor_ac", "description": "Bilgisayarda belirtilen yoldaki klasörü Dosya Gezgini'nde açar", "parameters": {"type": "object", "properties": {"yol": {"type": "string", "description": "Açılacak klasörün yolu, örn. 'C:\\Users\\ensar\\Desktop' veya '~'"}}, "required": ["yol"]}}},
    {"type": "function", "function": {"name": "web_arama_yap", "description": "Varsayılan tarayıcıda Google'da bir arama açar", "parameters": {"type": "object", "properties": {"sorgu": {"type": "string", "description": "Aranacak kelime veya cümle"}}, "required": ["sorgu"]}}},
    {"type": "function", "function": {"name": "web_sitesi_ac", "description": "Belirtilen web sitesini doğrudan tarayıcıda açar (arama yapmadan)", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "Açılacak site, örn. 'youtube.com', 'github.com'"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "youtube_video_ac", "description": "YouTube'da arama yapıp bulunan ilk videoyu doğrudan açar", "parameters": {"type": "object", "properties": {"sorgu": {"type": "string", "description": "Aranacak video konusu, örn. 'MrBeast Minecraft'"}}, "required": ["sorgu"]}}},
    {"type": "function", "function": {"name": "spotify_sarki_ac", "description": "Spotify'da belirtilen şarkıyı bulup gerçekten çalmaya başlatır", "parameters": {"type": "object", "properties": {"sarki_adi": {"type": "string", "description": "Şarkı adı, istersen sanatçısıyla birlikte"}}, "required": ["sarki_adi"]}}},
    {"type": "function", "function": {"name": "medya_kontrol", "description": "Aktif medya oynatıcısını kontrol eder: oynat, duraklat, sonraki, onceki, ses_artir, ses_azalt, sessize_al", "parameters": {"type": "object", "properties": {"eylem": {"type": "string", "description": "Yapılacak eylem: oynat, duraklat, sonraki, onceki, ses_artir, ses_azalt, sessize_al"}}, "required": ["eylem"]}}},
    {"type": "function", "function": {"name": "hava_durumu_soyle", "description": "Belirtilen şehir için güncel hava durumunu (sıcaklık) söyler", "parameters": {"type": "object", "properties": {"sehir": {"type": "string", "description": "Şehir adı, örn. 'Adana', 'İstanbul'"}}, "required": ["sehir"]}}},
    {"type": "function", "function": {"name": "ekran_goruntusu_al", "description": "Ekranın görüntüsünü alıp masaüstüne kaydeder", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "panoya_kopyala", "description": "Verilen metni panoya (clipboard) kopyalar", "parameters": {"type": "object", "properties": {"metin": {"type": "string", "description": "Panoya kopyalanacak metin"}}, "required": ["metin"]}}},
    {"type": "function", "function": {"name": "ekrana_tikla", "description": "Ekranda belirtilen x,y koordinatına tıklar. SADECE kullanıcı net koordinat verdiyse kullan.", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}}},
    {"type": "function", "function": {"name": "bilgisayari_kilitle", "description": "Bilgisayar ekranını kilitler", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "bilgisayari_uyut", "description": "Bilgisayarı uyku moduna alır", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "saat_ve_tarih_soyle", "description": "Şu anki gerçek saat, tarih ve haftanın hangi günü olduğunu döner", "parameters": {"type": "object", "properties": {}}}},
]

TOOL_FONKSIYONLARI = {
    "uygulama_ac": uygulama_ac,
    "klasor_ac": klasor_ac,
    "web_arama_yap": web_arama_yap,
    "web_sitesi_ac": web_sitesi_ac,
    "youtube_video_ac": youtube_video_ac,
    "spotify_sarki_ac": spotify_sarki_ac,
    "medya_kontrol": medya_kontrol,
    "hava_durumu_soyle": hava_durumu_soyle,
    "ekran_goruntusu_al": ekran_goruntusu_al,
    "panoya_kopyala": panoya_kopyala,
    "ekrana_tikla": ekrana_tikla,
    "bilgisayari_kilitle": bilgisayari_kilitle,
    "bilgisayari_uyut": bilgisayari_uyut,
    "saat_ve_tarih_soyle": saat_ve_tarih_soyle,
}


def ses_kaydet():
    log("Konuş! (susunca otomatik duracak)")
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frames = []
    silence_ms = 0
    speech_ms = 0
    speaking_started = False
    max_frames = int(MAX_RECORD_SECONDS * 1000 / FRAME_MS)

    stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, dtype='int16', channels=1)
    stream.start()
    for _ in range(max_frames):
        data, _ = stream.read(FRAME_SAMPLES)
        frames.append(data)
        is_speech = vad.is_speech(data, SAMPLE_RATE)
        if is_speech:
            speaking_started = True
            speech_ms += FRAME_MS
            silence_ms = 0
        elif speaking_started:
            silence_ms += FRAME_MS
            if speech_ms >= MIN_SPEECH_MS and silence_ms >= SILENCE_LIMIT_MS:
                break
    stream.stop()
    stream.close()

    audio_bytes = b"".join(frames)
    tmp_path = tempfile.mktemp(suffix=".wav")
    with wave.open(tmp_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_bytes)
    return tmp_path, speech_ms


_whisper_model = None


def sesi_yaziya_cevir(wav_path):
    global _whisper_model
    from faster_whisper import WhisperModel
    if _whisper_model is None:
        log("Whisper yükleniyor (CPU)...")
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = _whisper_model.transcribe(wav_path, language="tr", vad_filter=True)
    metin = " ".join([s.text.strip() for s in segments])
    log(f"Anladığım: {metin}")
    return metin


def sesli_soyle(metin):
    import edge_tts
    from playsound import playsound
    out_path = tempfile.mktemp(suffix=".mp3")

    async def _uret():
        communicate = edge_tts.Communicate(metin, EDGE_VOICE)
        await communicate.save(out_path)

    asyncio.run(_uret())
    playsound(out_path)


def ai_ile_konus(kullanici_metni):
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        log("HATA: GROQ_API_KEY bulunamadı.")
        return "API anahtarım ayarlanmamış."

    client = Groq(api_key=api_key)
    CONVERSATION_HISTORY.append({"role": "user", "content": kullanici_metni})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + CONVERSATION_HISTORY[-20:]

    for _ in range(5):
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=800,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })

            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                try:
                    raw_args = tool_call.function.arguments
                    fn_args = json.loads(raw_args) if raw_args else {}
                    if fn_args is None:
                        fn_args = {}
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}

                log(f"Görev çalıştırılıyor: {fn_name}({fn_args})")
                fn = TOOL_FONKSIYONLARI.get(fn_name)
                try:
                    sonuc = fn(**fn_args) if fn else f"Bilinmeyen fonksiyon: {fn_name}"
                except Exception as e:
                    sonuc = f"Görev çalıştırılırken hata oldu: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": sonuc,
                })
            continue

        else:
            sahte_cagrilar, temiz_metin = metinden_sahte_tool_cagrilarini_ayikla(msg.content)

            if sahte_cagrilar:
                messages.append({"role": "assistant", "content": msg.content})
                for isim, args in sahte_cagrilar:
                    log(f"(metin-tabanlı) Görev çalıştırılıyor: {isim}({args})")
                    fn = TOOL_FONKSIYONLARI.get(isim)
                    try:
                        sonuc = fn(**args) if fn else f"Bilinmeyen fonksiyon: {isim}"
                    except Exception as e:
                        sonuc = f"Görev çalıştırılırken hata oldu: {e}"
                    messages.append({
                        "role": "user",
                        "content": f"[Sistem: {isim} çalıştırıldı. Sonuç: {sonuc}. Şimdi kullanıcıya bunu normal cümlelerle, tool yazısı olmadan söyle.]",
                    })
                continue

            cevap = temiz_metin if temiz_metin else msg.content
            CONVERSATION_HISTORY.append({"role": "assistant", "content": cevap})
            log(f"Cerberus: {cevap}")
            return cevap

    return "Görevi tamamlarken bir sorun oldu, tekrar dener misin?"


def main():
    hafizayi_yukle()
    log("Cerberus hazır")
    log("Konuşmak için boş ENTER'a bas, yazarak konuşmak için mesajını yazıp ENTER'a bas.")
    while True:
        try:
            kullanici_girdisi = input("\n>>> ").strip()

            if kullanici_girdisi == "":
                wav_path, speech_ms = ses_kaydet()
                if speech_ms < MIN_SPEECH_MS:
                    log("Ses algılanamadı, tekrar dene.")
                    continue
                metin = sesi_yaziya_cevir(wav_path)
                if not metin.strip():
                    log("Whisper bir şey anlayamadı, tekrar dene.")
                    continue
            else:
                metin = kullanici_girdisi

            cevap = ai_ile_konus(metin)
            hafizayi_kaydet()
            sesli_soyle(cevap)

        except KeyboardInterrupt:
            log("Görüşürüz dayı")
            break
        except Exception as e:
            log(f"Hata oldu: {e}")


if __name__ == "__main__":
    main()
