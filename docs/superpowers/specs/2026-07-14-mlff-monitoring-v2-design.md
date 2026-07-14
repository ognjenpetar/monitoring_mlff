# MLFF Monitoring v2 — statistika, pragovi alarma, Telegram komande, dnevni izveštaj, cloud deployment

Datum: 2026-07-14
Status: odobreno u brainstorming fazi, spreman za implementacioni plan

## Kontekst

Postojeći sistem ima dve paralelne implementacije:
- `app.py` (root) — desktop GUI (Tkinter), radi samo dok korisnik ručno drži prozor otvoren.
- `cloud verzija/service.py` — headless verzija bez GUI-ja, zamišljena za rad na serveru/VM-u, trenutno sa zastarelim `scraper.py`/`notifier.py` (stara URL adresa `ot.sdn.rs/portali/`, stara struktura tabele od 6 kolona).
- `stabilna verzija/` — arhivska kopija, van scope-a ovog rada, ne dira se.

U prethodnom koraku je popravljen root `scraper.py`: nova adresa `https://mlff.sdn.rs`, nova struktura tabele (4 kolone: portal, hostname+IP, status, trajanje), i zaobiđen lokalni SSL bug (`ASN1` greška pri `load_default_certs()` na ovoj mašini).

Statistika, pragovi alarma i dnevni izveštaj imaju smisla samo ako aplikacija radi neprekidno (24/7), ne samo dok je GUI otvoren na nečijem računaru. Odluka: sva nova funkcionalnost ide u `cloud verzija/service.py`, koji se deployuje na Oracle Cloud Free Tier VM. `app.py` (GUI) se ne menja dalje u ovom radu.

## Cilj

1. Nezavisni on/off prekidači za tri tipa Telegram/email obaveštenja.
2. Trajna statistika uptime/downtime po uređaju (SQLite).
3. Alarm kad je uređaj DOWN duže od praga (60 min), sa ponavljanjem na 2h.
4. Poseban alarm za UPS uređaje (gubitak struje na lokaciji), sa kraćim pragom (3 min) i ponavljanjem na 2h.
5. Telegram komande na zahtev: `/live`, `/stat`, `/juce`.
6. Automatski dnevni izveštaj u 09:01 (Europe/Belgrade) za prethodni dan.
7. Deployment plan: Oracle Cloud Free Tier VM + VPN pristup internoj Orion mreži + Docker Compose.

## Van scope-a

- Izmene u `app.py` (GUI) osim onih već urađenih (URL/scraper fix).
- Brisanje ili menjanje `stabilna verzija/`.
- Web dashboard / HTTP endpoint za statistiku (odlučeno: samo Telegram komande).
- Podrška za email kanal na novim alarmima (60-min prag, UPS alarm) — eksplicitno samo Telegram, po zahtevu korisnika. Email ostaje samo na postojećem per-event kanalu.

## Konsolidacija koda (preduslov)

`cloud verzija/scraper.py` i `cloud verzija/notifier.py` su zastareli duplikati root verzija. Pre dodavanja novih funkcija:
- `cloud verzija/scraper.py` se sinhronizuje sa popravljenim root `scraper.py` (nova URL, nova struktura tabele, no-verify SSL adapter).
- `cloud verzija/notifier.py` se sinhronizuje sa root `notifier.py` (trenutno identični, samo za svaki slučaj).
- `cloud verzija/service.py` postaje glavna, jedina verzija koja se dalje razvija i deployuje.

## 1. Konfiguracija — novi tuplovi

Prošireni env-based config (`cloud verzija/service.py: get_config()`), postojeći env se zadržava, dodaju se:

| Env var | Default | Značenje |
|---|---|---|
| `NOTIFY_THRESHOLD_ALERT` | `true` | Uključuje 60-min prag alarm (Telegram) |
| `NOTIFY_UPS_ALERT` | `true` | Uključuje UPS alarm (Telegram) |
| `DOWN_THRESHOLD_MINUTES` | `60` | Prag za "uređaj nedostupan predugo" |
| `UPS_ALERT_DELAY_MINUTES` | `3` | Koliko UPS mora biti DOWN pre alarma (filtrira gličeve) |
| `ALERT_REPEAT_MINUTES` | `120` | Interval ponavljanja podsetnika (za oba tipa alarma) |
| `DAILY_REPORT_TIME` | `09:01` | Vreme slanja dnevnog izveštaja (24h format, Europe/Belgrade) |
| `TIMEZONE` | `Europe/Belgrade` | Timezone za sve vremenske proračune (dan počinje/završava po ovom TZ) |

Postojeći `NOTIFY_EMAIL`/`NOTIFY_TELEGRAM` ostaju kao prekidač za **per-event** obaveštenja (svaka promena statusa), nezavisno od gornja dva nova prekidača.

## 2. Statistika — model podataka

Nova SQLite baza `stats.db` (fajl na disku VM-a, mountovan kao Docker volume `./data:/app/data`, putanja podesiva preko `STATS_DB_PATH`).

Tabela `status_periods`:

| kolona | tip | opis |
|---|---|---|
| `id` | INTEGER PK autoincrement | |
| `hostname` | TEXT | ključ uređaja (isti kao `Device.key`) |
| `status` | TEXT | `UP` / `DOWN` (raw status string se ne čuva, samo normalizovano) |
| `start_ts` | TEXT (ISO8601, UTC) | početak perioda |
| `end_ts` | TEXT (ISO8601, UTC), NULL ako je period u toku | kraj perioda |

Logika upisa (u glavnoj `run()` petlji servisa, na svakoj promeni statusa uređaja):
- Zatvori prethodni otvoren period za taj `hostname` (`end_ts = now`).
- Otvori novi period sa `start_ts = now`, `status = novi status`.
- Uređaji iz `EXCLUDED_HOSTNAMES` (`SCPA1046-L-UPS`, `SCPA1046-L-IOL`) se **ne upisuju** u statistiku — trajno su isključeni iz svega.
- Prvi ciklus posle pokretanja servisa (nema prethodnog stanja) otvara početni period za svaki uređaj bez upisa "promene".

Upiti za statistiku (helper funkcije u novom modulu `stats.py`):
- `day_stats(date, tz)` → za dati datum (00:00–24:00 po `TIMEZONE`): po uređaju — ukupno DOWN vreme (sekunde, sa presekom perioda na granice dana), broj odvojenih DOWN perioda (outages), uptime % (`(86400 - downtime) / 86400`). Periodi koji sežu preko granice dana se sabiraju samo za deo unutar traženog dana.
- `live_status()` → trenutni status svakog uređaja (iz poslednjeg poll ciklusa u memoriji, ne iz baze) + trajanje trenutnog perioda.

## 3. Alarm pravila

Servis, na svakom poll ciklusu (60s, nepromenjeno):

**60-min prag** (`notify_threshold_alert`):
- Za svaki DOWN uređaj (osim isključenih) čije je trenutno DOWN trajanje ≥ `DOWN_THRESHOLD_MINUTES` I (nikad poslat alarm za ovaj DOWN period ILI je prošlo ≥ `ALERT_REPEAT_MINUTES` od poslednjeg alarma za taj period) → pošalji Telegram poruku, upamti `last_alert_sent_at` za taj (hostname, start_tsperioda) par u memoriji (rečnik, resetuje se kad uređaj ode UP).

**UPS alarm** (`notify_ups_alert`):
- Isto pravilo, ali: filtrira uređaje čiji `hostname.endswith("-UPS")` i nisu u `EXCLUDED_HOSTNAMES`, prag je `UPS_ALERT_DELAY_MINUTES` umesto 60, tekst poruke naglašava "moguć gubitak struje na lokaciji".
- Ako uređaj zadovoljava i UPS pravilo i 60-min pravilo (retko, jer je UPS prag mnogo kraći), šalju se **oba** alarma nezavisno — različite poruke, različiti tajmeri.

Oba tipa alarma su nezavisna od per-event (`NOTIFY_TELEGRAM`) prekidača — imaju svoje sopstvene toggle-ove.

## 4. Telegram komande na zahtev

Servis dodatno pokreće petlju koja poluje `getUpdates` (isti mehanizam kao postojeći `get_telegram_updates`, sa `offset` parametrom da se ne obrađuju iste poruke dvaput). Obrađuju se samo poruke od `chat_id`-jeva koji su u `TELEGRAM_CHAT_IDS` — ostale se ignorišu (bez odgovora).

- `/live` → snapshot trenutnog stanja: broj UP/DOWN, lista trenutno DOWN uređaja sa trajanjem.
- `/stat` → `day_stats()` za danas (00:00 do sada, po `TIMEZONE`).
- `/juce` → `day_stats()` za juče (pun dan, 00:00–24:00).

Format odgovora — tekstualna tabela slična postojećem `build_notification_text`, npr:
```
=== Statistika: 13.07.2026 ===
Ukupno: 124 uredjaja aktivnih
Mrezni uptime: 98.4%

Uredjaji sa najvise downtime-a:
  SCPA1055-R-UPS     2h 14m DOWN  (2 prekida)  99.1% uptime
  ...
```

## 5. Dnevni izveštaj

Nova pozadinska petlja (thread ili provera unutar glavne petlje na svaki ciklus) koja proverava da li je trenutno vreme (po `TIMEZONE`) jednako `DAILY_REPORT_TIME` i da izveštaj za taj dan još nije poslat (čuva se `last_report_date` u memoriji + upisano u malu pomoćnu tabelu `sent_reports(date)` u istoj SQLite bazi, da preživi restart kontejnera i spreči duple izveštaje ako se servis restartuje baš u tom minutu).
- Sadržaj = identičan `/juce` odgovoru za prethodni dan.
- Šalje se svim `TELEGRAM_CHAT_IDS`, nezavisno od `NOTIFY_TELEGRAM`/`NOTIFY_THRESHOLD_ALERT`/`NOTIFY_UPS_ALERT` prekidača (dnevni izveštaj je uvek uključen — nema toggle za njega, po zahtevu).

## 6. Deployment plan

### Infrastruktura
- **Oracle Cloud Free Tier**, Always Free VM (Ampere A1, Ubuntu 22.04). Kartica se unosi samo radi verifikacije identiteta; Always Free resursi se ne naplaćuju.

### VPN pristup internoj mreži
- Interna stranica `mlff.sdn.rs` dostupna je samo kroz Orion VPN (Palo Alto **GlobalProtect**, portal `gp.oriontelekom.rs`, potvrđeno od korisnika — samo korisničko ime + lozinka, **bez MFA**).
- Klijent: `openconnect --protocol=gp gp.oriontelekom.rs --user=<nalog>`, lozinka prosleđena preko stdin-a (ne kao argument u plain textu).
- Radi kao poseban Docker kontejner (`vpn`) sa `--cap-add=NET_ADMIN --device=/dev/net/tun`, restart policy `unless-stopped` + health-check koji restartuje kontejner ako tunel padne.
- `monitor` kontejner (servis) koristi `network_mode: "service:vpn"` da sav njegov saobraćaj ka `mlff.sdn.rs` ide kroz tunel; Telegram (`api.telegram.org`) i SMTP (`smtp.gmail.com`) idu direktno preko normalnog interneta VM-a (isti network namespace, samo različita destinacija — VPN ruta pokriva samo internu Orion podmrežu).
- Preporuka (ne blokira implementaciju): zatražiti od kolege poseban service nalog za VPN (ne lični `ognjen.petar` nalog), radi razdvajanja od korisnikove svakodnevne sesije i lakšeg upravljanja pristupom. Ako nije praktično, koristi se postojeći lični nalog.
- Napomena: GlobalProtect sesija (sa slike) ima "Login Lifetime" ~29 dana. Health-check/restart mehanizam za `vpn` kontejner treba da hvata i slučaj isteka sesije (openconnect izlazi sa greškom → restart policy pokušava ponovo → ako je istekla sesija, treba alarm/log da neko ručno obnovi ako je potrebna ponovna autentifikacija).

### Docker Compose (u `cloud verzija/`)
- `vpn` servis (openconnect image ili custom Dockerfile).
- `monitor` servis (postojeći `service.py` + nove funkcije), `network_mode: "service:vpn"`, volume `./data:/app/data` za `stats.db`.
- `.env` fajl (van git-a, `.gitignore` već postoji u `cloud verzija/`) sa SMTP/Telegram/VPN kredencijalima.

### Update procedura
Ista kao postojeća u `DEPLOY.md` (`docker compose down && docker compose build && docker compose up -d`), dopunjena napomenom o `vpn` servisu.

## Testiranje

- Statistika: unit testovi za `stats.py` (upis perioda, `day_stats()` sa periodima koji seku granicu dana, isključeni uređaji se ne upisuju).
- Alarm pragovi: testovi za logiku "prvi put posle praga" i "ponavljanje posle N minuta", uključujući reset kad uređaj ode UP.
- Telegram komande: test parsiranja `getUpdates` odgovora i filtriranja po `chat_id`.
- End-to-end na VM-u: ručna provera da `/live`, `/stat`, `/juce` rade posle deploya, i da VPN tunel stvarno rutira ka `mlff.sdn.rs` (curl iz `monitor` kontejnera).
