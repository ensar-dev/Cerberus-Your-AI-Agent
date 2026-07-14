
import os
import wave
import tempfile
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
RECORD_SECONDS = 5
GROQ_MODEL = "llama-3.3-70b-versatile"

CONVERSATION_HISTORY = []
SYSTEM_PROMPT = """Senin adın Cerberus. Kullanıcının kişisel sesli asistanısın.
Seni Muhammed Ensar Demirtaş yaptı. Kim yaptığın sorulursa bu ismi söyle.
Kısa, doğal, sohbet diliyle Türkçe cevap ver. Sesli okunacak, o yüzden
uzun listeler veya markdown kullanma, düz konuşma dili kullan."""


def log(msg):
    print(f"[Cerberus] {msg}")


def ses_kaydet(sure=RECORD_SECONDS):
    log(f"Konuş! ({sure} saniye kaydediyorum)")
    audio = sd.rec(int(sure * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                    channels=1, dtype='int16')
    sd.wait()
    tmp_path = tempfile.mktemp(suffix=".wav")
    with wave.open(tmp_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return tmp_path


_whisper_model = None

def sesi_yaziya_cevir(wav_path):
    global _whisper_model
    from faster_whisper import WhisperModel

    if _whisper_model is None:
        log("Whisper yükleniyor (ilk seferde biraz sürebilir)...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

    segments, _ = _whisper_model.transcribe(wav_path, language="tr")
    metin = " ".join([s.text.strip() for s in segments])
    log(f"Anladığım: {metin}")
    return metin


def ai_ile_konus(kullanici_metni):
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        log("HATA: GROQ_API_KEY bulunamadı.")
        return "API anahtarım ayarlanmamış."

    client = Groq(api_key=api_key)
    CONVERSATION_HISTORY.append({"role": "user", "content": kullanici_metni})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + CONVERSATION_HISTORY[-20:]

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=500,
        messages=messages,
    )
    cevap = response.choices[0].message.content
    CONVERSATION_HISTORY.append({"role": "assistant", "content": cevap})
    log(f"Cerberus: {cevap}")
    return cevap


def sesli_soyle(metin):
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty('rate', 175)
    engine.say(metin)
    engine.runAndWait()


def main():
    log("Cerberus v0 (Groq) hazır. ENTER'a basıp konuş, çıkmak için Ctrl+C.")
    while True:
        try:
            input("\n>>> Konuşmak için ENTER'a bas...")
            wav_path = ses_kaydet()
            metin = sesi_yaziya_cevir(wav_path)

            if not metin.strip():
                log("Bir şey anlamadım, tekrar dene.")
                continue

            cevap = ai_ile_konus(metin)
            sesli_soyle(cevap)

        except KeyboardInterrupt:
            log("Görüşürüz dayı!")
            break
        except Exception as e:
            log(f"Hata oldu: {e}")


if __name__ == "__main__":
    main()
