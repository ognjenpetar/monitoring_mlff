# MLFF Monitoring – Uputstvo za pokretanje

## Pokretanje

Dvaput klikni na `run.bat` ili pokreni:
```
python app.py
```

---

## Prvo podešavanje (jednom)

### 1. Email notifikacije

Otvori **Podešavanja** u aplikaciji i popuni:

| Polje | Vrednost |
|---|---|
| SMTP server | `smtp.gmail.com` |
| SMTP port | `587` |
| Email pošiljalac | tvoja Gmail adresa npr. `monitoring.mlff@gmail.com` |
| Lozinka | **App Password** (ne obična lozinka) |
| Email primalac | `ognjen.petar.todorovic@oriontelekom.rs` |

**Kako napraviti Gmail App Password:**
1. Idi na [myaccount.google.com/security](https://myaccount.google.com/security)
2. Uključi 2-Step Verification
3. Traži "App passwords" → odaberi "Mail" + "Windows Computer"
4. Google generiše 16-karakternu lozinku – upiši je u polje Lozinka

---

### 2. Telegram notifikacije

**Kako napraviti Telegram bota:**
1. U Telegramu pronađi kontakt **@BotFather** i pošalji mu `/newbot`
2. Prati uputstva (ime bota, username bota) — na kraju dobiješ **Bot token**
   (izgleda kao `123456789:AABBCCddEEFF...`)
3. Upiši taj token u aplikaciji, u Podešavanjima, polje **Bot token**

**Kako dobiti svoj Chat ID:**
1. Pošalji svom botu bilo koju poruku (npr. `/start`) u Telegramu
2. U aplikaciji, u Podešavanjima, klikni **"Dohvati moj Chat ID"**
3. Aplikacija automatski pronađe i doda tvoj Chat ID na listu primalaca

---

## Isključeni uređaji

Sledeći uređaji su uvek DOWN jer nisu montirani i **neće aktivirati alarm**:
- `SCPA1046-L-UPS`
- `SCPA1046-L-IOL`

Prikazani su u tabeli sivom bojom.

---

## Zavisnosti

```
pip install requests beautifulsoup4
```
(automatski se instaliraju pri prvom pokretanju `run.bat`)

---

## Cloud verzija (radi 24/7, bez potrebe da tvoj računar bude upaljen)

Desktop aplikacija (`app.py`) radi samo dok je ručno pokrenuta. Za neprekidan
monitoring (statistika, alarm na 60 min nedostupnosti, alarm za gubitak struje
preko UPS-a, dnevni izveštaj) koristi se headless servis u `cloud verzija/`,
pokrenut na maloj cloud VM sa statičnim javnim IP-om koji Orion mrežni tim
whitelistuje (`mlff.sdn.rs` je dostupan samo sa whitelistovanih IP adresa).

- Kreiranje besplatne VM (Oracle Cloud Free Tier), korak po korak: [`ORACLE_CLOUD_SETUP.md`](ORACLE_CLOUD_SETUP.md)
- Deploy servisa na VM (Docker): [`cloud verzija/DEPLOY.md`](cloud%20verzija/DEPLOY.md)
- Detaljna specifikacija svih planiranih funkcija (statistika, alarmi, Telegram
  komande `/live` `/stat` `/juce`, dnevni izveštaj): [`docs/superpowers/specs/2026-07-14-mlff-monitoring-v2-design.md`](docs/superpowers/specs/2026-07-14-mlff-monitoring-v2-design.md)
