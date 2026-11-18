import os, re, sys, time, math, json, glob, signal, subprocess, requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
MOD = os.environ.get("TUS_MOD", "soru").strip()
KONU = os.environ.get("TUS_KONU", "Tüm Nöroloji").strip()
SORU_SAYISI = max(5, min(100, int(os.environ.get("SORU_SAYISI", "30") or "30")))

class SureDoldu(Exception):
    pass

def alarm_handler(signum, frame):
    raise SureDoldu("Zaman asimi")

signal.signal(signal.SIGALRM, alarm_handler)

def konu_icerigi_oku(dosya_adi, secili_konu, limit=14000):
    if not os.path.isfile(dosya_adi):
        return ""
    with open(dosya_adi, "r", encoding="utf-8", errors="ignore") as f:
        metin = f.read()
    if not metin.strip():
        return ""

    bolumler = re.split(r'(=== KONU: .+? ===)', metin)
    secilen_parcalar = []
    hedefler = [k.strip().lower() for k in secili_konu.split("+") if k.strip()]

    for i in range(1, len(bolumler), 2):
        baslik = bolumler[i]
        icerik = bolumler[i+1] if i + 1 < len(bolumler) else ""
        if any(h in baslik.lower() for h in hedefler) or "tüm nöroloji" in secili_konu.lower():
            secilen_parcalar.append(baslik + "\n" + icerik)

    if secilen_parcalar:
        birlesik = "\n\n".join(secilen_parcalar)
    else:
        anahtarlar = [w.lower() for w in re.findall(r'\w+', secili_konu) if len(w) > 3]
        satirlar = [s for s in metin.split("\n") if any(k in s.lower() for k in anahtarlar)]
        birlesik = "\n".join(satirlar) if satirlar else metin

    # Vurgulu satirlari en uste tasi ki yapay zeka once onlari gorsun
    vurgulular = [s for s in birlesik.split("\n") if "VURGULU" in s]
    normaller = [s for s in birlesik.split("\n") if "VURGULU" not in s]
    oncelikli = ("\n".join(vurgulular) + "\n\n" + "\n".join(normaller)).strip()
    return oncelikli[:limit]

SLAYT_BAGLAM = konu_icerigi_oku("noroloji_kaynak.txt", KONU, 13000)
CIKMIS_BAGLAM = konu_icerigi_oku("noroloji_cikmis.txt", KONU, 10000)

def groq_aktif_modelleri_bul():
    if not GROQ_API_KEY:
        return []
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=10)
        if r.status_code == 200:
            ids = [m["id"] for m in r.json().get("data", []) if not any(x in m["id"] for x in ["whisper", "guard", "tts", "8b"])]
            oncelik = [m for m in ids if any(k in m for k in ["70b", "120b", "qwen", "gpt-oss", "deepseek", "kimi"])]
            diger = [m for m in ids if m not in oncelik]
            return (oncelik + diger)[:4]
    except Exception:
        pass
    return ["llama-3.3-70b-versatile"]

GROQ_MODELLERI = groq_aktif_modelleri_bul()

def json_kurtar(ham_metin, anahtar_liste="sorular", zorunlu_alan="soru"):
    if not ham_metin:
        return []
    temiz = re.sub(r'```json|```', '', ham_metin).strip()
    temiz = re.sub(r'\\u(?![0-9a-fA-F]{4})', r'\\\\u', temiz)
    try:
        veri = json.loads(temiz, strict=False)
        if isinstance(veri, dict) and veri.get(anahtar_liste):
            return veri[anahtar_liste]
    except Exception:
        pass
    kurtarilan = []
    dec = json.JSONDecoder(strict=False)
    desen = r'\{\s*"etiket"' if anahtar_liste == "sorular" else r'\{\s*"on"'
    for pos in [m.start() for m in re.finditer(desen, temiz)]:
        try:
            obj, _ = dec.raw_decode(temiz[pos:])
            if isinstance(obj, dict) and obj.get(zorunlu_alan):
                kurtarilan.append(obj)
        except Exception:
            continue
    return kurtarilan

def yapay_zeka_cagir(faz_no, sistem, icerik, anahtar_liste="sorular", zorunlu_alan="soru"):
    for g_model in GROQ_MODELLERI:
        try:
            print(f"[Faz {faz_no}] Groq ({g_model}) deneniyor...", flush=True)
            signal.alarm(25)
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": g_model,
                    "temperature": 0.25,
                    "max_tokens": 8000,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": sistem}, {"role": "user", "content": icerik}]
                },
                timeout=(8, 22)
            )
            signal.alarm(0)
            if r.status_code == 200:
                ogeler = json_kurtar(r.json()["choices"][0]["message"]["content"], anahtar_liste, zorunlu_alan)
                if ogeler:
                    print(f"[Faz {faz_no}] Groq ({g_model}) TAMAM! ({len(ogeler)} oge)", flush=True)
                    return ogeler
            else:
                time.sleep(1.5)
        except Exception:
            signal.alarm(0)
    return []

kaynak_notu = (
    f"\nSLAYT İÇERİĞİ VE HOCA VURGULARI ([VURGULU] ETİKETLİ SATIRLARA ÖNCELİK VER):\n\"\"\"\n{SLAYT_BAGLAM}\n\"\"\"\n\n"
    f"ÇIKMIŞ NÖROLOJİ FÖYÜ SORULARI:\n\"\"\"\n{CIKMIS_BAGLAM}\n\"\"\"\n"
)

os.makedirs("noro_bankasi", exist_ok=True)
temiz_konu = re.sub(r"[^a-zA-Z0-9]", "_", KONU.translate(str.maketrans("İIĞÜŞÖÇıiğüşöç", "iigusociigusoc")))[:55]

if MOD == "anki":
    kartlar = []
    gorulen = set()
    tur = 0
    while len(kartlar) < SORU_SAYISI and tur < math.ceil(SORU_SAYISI / 12) + 3:
        istenen = min(15, SORU_SAYISI - len(kartlar))
        onceki = "; ".join([k["on"][:40] for k in kartlar[-12:]])
        sistem = (
            "Sen İnönü Tıp Dönem 5 Nöroloji Stajı ve TUS baş eğitmenisin. "
            f"Seçilen konu(lar): '{KONU}' için tam {istenen} adet TUS ve Staj Anki Flashcard üret. "
            "ÖNCELİK KURALI: Kaynaktaki '[VURGULU]' etiketli hoca çizimlerine ve Çıkmış Nöroloji Föyündeki sorulara en az %75 ağırlık ver. "
            "Kasıntı zorluk yapma; konunun sınavda sorulabilecek her bir farklı spotunu net soru-cevap kartlarına dönüştür. "
            "Metin içinde asla çift tırnak (\") veya LaTeX ($) kullanma. Sadece saf JSON döndür: "
            '{"kartlar":[{"konu_etiketi":"Slayt / Konu","on":"Net aktif hatırlama sorusu?","arka":"Açık cevap ve klinik/anatomik mekanizma.","spot":"Altın Nöroloji Spotu","referans":"Hoca Vurgusu / Çıkmış Föy"}]}'
        )
        icerik = f"KONU: {KONU}\n{kaynak_notu}\nDaha önce sorulanları tekrar etme: [{onceki}]\nTam {istenen} adet benzersiz Anki kartı üret."
        gelen = yapay_zeka_cagir(tur + 1, sistem, icerik, "kartlar", "on")
        for c in gelen:
            anahtar = re.sub(r"\W+", "", c.get("on", "").lower())[:45]
            if anahtar and anahtar not in gorulen and c.get("arka"):
                gorulen.add(anahtar)
                kartlar.append(c)
        tur += 1
        if len(kartlar) < SORU_SAYISI:
            time.sleep(1.5)

    kartlar = kartlar[:SORU_SAYISI]
    if not kartlar:
        sys.exit(1)
    dosya_adi = f"noro_bankasi/ANKI__{temiz_konu}.json"
    with open(dosya_adi, "w", encoding="utf-8") as f:
        json.dump({"tip": "anki", "konu": KONU, "toplam_kart": len(kartlar), "guncelleme": time.strftime("%d.%m.%Y %H:%M"), "kartlar": kartlar}, f, ensure_ascii=False, indent=2)
else:
    fazlar = [
        ("FAZ 1: ÇIKMIŞ FÖY BİREBİR & HOCA TUZAKLARI", "Doğrudan Çıkmış Nöroloji Föyündeki soruları ve hocaların klasik tuzaklarını soru haline getir. Etiketine 'ÇIKMIŞ FÖY' yaz."),
        ("FAZ 2: [VURGULU] HOCA ÇİZİMLERİ & SLAYT SPOTLARI", "Slaytlarda '[VURGULU]' olarak işaretlenmiş satırlardan doğrudan staj ve TUS soruları üret. Etiketine 'VURGULU SLAYT' yaz."),
        ("FAZ 3: LOKALİZASYON & KLİNİK VAKA ANALİZİ", "Klinik bulgulardan lezyon yerini, etkilenen arteri veya kraniyal siniri bulduran kısa vaka soruları."),
        ("FAZ 4: AYIRICI TANI, TEDAVİ & ACİL NÖROLOJİ", "İlaç seçimleri, kontrendikasyonlar, EEG/EMG/BOS bulguları ve nörolojik aciller.")
    ]
    tum_sorular = []
    gorulen = set()
    tur = 0
    while len(tum_sorular) < SORU_SAYISI and tur < math.ceil(SORU_SAYISI / 10) + 3:
        istenen = min(12, SORU_SAYISI - len(tum_sorular))
        faz_adi, faz_aciklama = fazlar[tur % len(fazlar)]
        onceki = "; ".join([s["soru"][:45] for s in tum_sorular[-12:]])
        sistem = (
            "Sen İnönü Tıp Dönem 5 Nöroloji Stajı ve TUS soru yazarısın. "
            f"'{KONU}' konusu için '{faz_adi}' odağında tam {istenen} adet 5 şıklı (A-E) soru üret. "
            "ÖNCELİK KURALI: '[VURGULU]' etiketli slayt satırlarına ve Çıkmış Nöroloji Föyündeki sorulara öncelik ver. Kasıntı zorluk yapma, konunun tüm farklı spotlarını eksiksiz tara. "
            "ZORUNLU OTOKONTROL: Soru kökü, doğru şık ('dogru_index') ve açıklama metninin tıbbi açıdan %100 tutarlı olduğunu doğrula. "
            "Metin içinde asla çift tırnak (\") veya LaTeX ($) kullanma. Sadece saf JSON döndür: "
            '{"sorular":[{"etiket":"ÇIKMIŞ FÖY veya VURGULU SLAYT","soru":"Soru metni?","siklar":["A) ...","B) ...","C) ...","D) ...","E) ..."],"dogru_index":0,"aciklama":"Detaylı klinik ve anatomik açıklama.","spot":"Altın Nöroloji Spotu","referans":"Slayt No / Çıkmış Föy"}]}'
        )
        icerik = f"KONU: {KONU}\nODAK: {faz_adi} ({faz_aciklama})\n{kaynak_notu}\nDaha önce değinilen noktaları tekrar etme: [{onceki}]\nTam {istenen} adet benzersiz soru üret."
        gelen = yapay_zeka_cagir(tur + 1, sistem, icerik, "sorular", "soru")
        for q in gelen:
            anahtar = re.sub(r"\W+", "", q.get("soru", "").lower())[:50]
            if anahtar and anahtar not in gorulen and len(q.get("siklar", [])) == 5:
                gorulen.add(anahtar)
                tum_sorular.append(q)
        tur += 1
        if len(tum_sorular) < SORU_SAYISI:
            time.sleep(1.5)

    tum_sorular = tum_sorular[:SORU_SAYISI]
    if not tum_sorular:
        sys.exit(1)
    dosya_adi = f"noro_bankasi/Noroloji__{temiz_konu}.json"
    with open(dosya_adi, "w", encoding="utf-8") as f:
        json.dump({"ders": "Nöroloji", "konu": KONU, "toplam_soru": len(tum_sorular), "guncelleme": time.strftime("%d.%m.%Y %H:%M"), "sorular": tum_sorular}, f, ensure_ascii=False, indent=2)

# SON 4 SORU PAKETI VE SON 4 ANKI DESTESI DISINDAKI ESKILERI TEMIZLE
tum_jsonlar = sorted(glob.glob("noro_bankasi/*.json"), key=os.path.getmtime, reverse=True)
anki_dosyalar = [p for p in tum_jsonlar if os.path.basename(p).startswith("ANKI__")]
soru_dosyalar = [p for p in tum_jsonlar if not os.path.basename(p).startswith("ANKI__")]

for eski in (anki_dosyalar[4:] + soru_dosyalar[4:]):
    if os.path.abspath(eski) != os.path.abspath(dosya_adi):
        try:
            os.remove(eski)
        except Exception:
            pass

subprocess.run(["git", "config", "user.name", "VIP-Noroloji-Bot"], check=False)
subprocess.run(["git", "config", "user.email", "tus@vip.local"], check=False)
subprocess.run(["git", "add", "-A", "noro_bankasi"], check=False)
subprocess.run(["git", "commit", "-m", f"VIP Noroloji ({MOD.upper()}): {KONU}"], check=False)
subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
subprocess.run(["git", "push", "origin", "main"], check=False)
