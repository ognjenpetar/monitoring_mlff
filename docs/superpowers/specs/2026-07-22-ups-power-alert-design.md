# MLFF Monitoring — UPS baterijski/AC alarm

Datum: 2026-07-22
Status: odobreno u brainstorming fazi, spreman za implementacioni plan

## Kontekst

Postojeći sistem prati samo mrežnu dostupnost uređaja (UP/DOWN) sa glavne tabele
(`devicesTable`) na `mlff.sdn.rs`. UPS uređaji u toj tabeli (hostname završava na
`-UPS`, npr. `SCPA1055-R-UPS`) prijavljuju samo da li im je **mrežna upravljačka
kartica dostupna** — kad UPS pređe na baterijsko napajanje, kartica često ostaje
dostupna (radi na bateriji), pa bi status u toj tabeli i dalje pisao "UP". Sistem
trenutno **ne može da primeti stvarni gubitak mrežnog napajanja**.

Istraživanjem stranice pronađena je **potpuno odvojena tabela**, `upsTable`
(sekcija "Monitoring UPS-eva"), na **istoj stranici** koju već čitamo — nema
potrebe za dodatnim HTTP zahtevom. Kolone: `Uređaj` (hostname + IP, npr. `UPS-11`
/ `172.23.11.4`), `Status` (badge — potvrđene vrednosti: `AC OK` kad je sve u
redu, `ERR` za trajno pokvaren/nedostupan uređaj poput `UPS-7`; tačan tekst za
"na baterijskom napajanju" nije uživo potvrđen jer nijedan UPS trenutno nije na
bateriji, ali CSS stranice definiše i treću boju badge-a, `badge-warning`,
verovatno za to stanje), `Baterija` (procenat, npr. `87%`), `Runtime` (verovatno
preostala procenjena autonomija, ne vreme već provedeno na bateriji — ne
koristimo ovu kolonu za merenje trajanja, vidi Sekciju 3).

Korisnik je pokazao primer email obaveštenja koje bi želeo (postojeći format iz
prakse, ne generiše ga trenutno naš sistem):

```
Drage kolege,

Sledeći UPS uređaji su prešli na baterijsko napajanje:

UPS-11
IP: 172.23.11.4
Lokacija: Portal 11
Baterija: 100% | Autonomija: 7790 min
Vreme: 2026-07-21 09:45:05
```

i odgovarajuću poruku za oporavak ("vratili na mrežno napajanje / AC OK").

## Cilj

1. Čitaj `upsTable` sa iste stranice (`mlff.sdn.rs`) uz postojeći fetch, bez
   dodatnog HTTP poziva.
2. Prati po uređaju da li je status `AC OK` ili nije; posle **3 minuta**
   neprekidno "nije AC OK", pošalji alarm na **Telegram i email** (oba kanala —
   za razliku od ostalih alarma u sistemu koji su Telegram-only).
3. Ponavljaj podsetnik na `ALERT_REPEAT_MINUTES` (isti kao postojeći, default
   120 min) dok se uređaj ne vrati na `AC OK`.
4. Kad se vrati na `AC OK`, pošalji poruku o oporavku (Telegram + email) sa
   ukupnim trajanjem ispada.
5. Meri trajanje ispada **sami** (od trenutka kad prvo primetimo "nije AC OK"),
   ne oslanjajući se na sajtovu "Runtime" kolonu.
6. Sačuvaj istoriju perioda u bazi (bez nove komande za pregled za sada).
7. `UPS-7` je trajno isključen iz alarma (poznat, trajno pokvaren uređaj).
8. Poštuje `/mute HOSTNAME` i `/mutesve`.

## Van scope-a

- Telegram komanda za pregled istorije ovih perioda (podaci se čuvaju, upit
  se pravi kasnije po potrebi).
- Bilo kakvo automatsko diferenciranje "stvarno na bateriji" naspram "greška u
  komunikaciji" — oba slučaja (bilo koji status ≠ `AC OK`) tretiraju se
  identično kao alarm, sa **tačnim tekstom statusa sa sajta** u poruci umesto
  pretpostavke, da poruka ostane tačna bez obzira koji tačan tekst sajt vrati.
- Izmene `app.py` (desktop GUI) ili `stabilna verzija/`.
- v3 Grupa B (Predlog 03/04/06/08, Ideja 03 — spec
  `docs/superpowers/specs/2026-07-21-mlff-monitoring-v3-alerts-design.md`) —
  taj plan je parkiran u zasebnom worktree-u (`.worktrees/mlff-v3`), nastavlja
  se posle ovog rada.

## 1. `scraper.py` — čitanje `upsTable`

Novi dataclass:

```python
@dataclass
class UpsPowerStatus:
    hostname: str        # npr. "UPS-11"
    ip: str
    status_text: str     # sirov tekst sa sajta: "AC OK", "ERR", ili nepoznata treca vrednost
    battery_pct: int

    @property
    def is_ac_ok(self) -> bool:
        return self.status_text.strip().upper() == "AC OK"

    @property
    def portal_id(self) -> str:
        """Izvodi broj portala iz imena (npr. 'UPS-11' -> '11')."""
        parts = self.hostname.rsplit("-", 1)
        return parts[1] if len(parts) == 2 else self.hostname

    @property
    def key(self) -> str:
        return self.hostname
```

Refaktor postojećeg `fetch_devices()`: razdvaja se HTTP fetch od parsiranja, da
bi nova `fetch_all()` mogla da dohvati stranicu **jednom** i parsira obe tabele
iz istog snapshot-a (konzistentnost — devices i UPS status iz iste sekunde).

- `_get_soup(url, timeout) -> BeautifulSoup` — novi privatni helper, sadrži
  postojeću HTTP-fetch logiku (`_session.get(...)`, `raise_for_status()`,
  `BeautifulSoup(...)`).
- `_parse_devices(soup) -> List[Device]` — postojeća parsing logika iz
  `fetch_devices`, premeštena ovde bez izmena ponašanja (i dalje baca
  `ValueError` ako `devicesTable` ne postoji — ta tabela je kritična).
- `_parse_ups_statuses(soup) -> List[UpsPowerStatus]` — nova parsing logika za
  `upsTable`. Za razliku od `_parse_devices`, **ne baca grešku** ako
  `upsTable` ne postoji na strani (vraća prazan spisak) — ovo je sekundarna
  funkcija, ne treba da obori ceo monitoring ciklus ako sajt privremeno nema tu
  sekciju.
- `fetch_devices(url, timeout=15) -> List[Device]` — javni API ostaje
  identičan (koristi ga `app.py`), sad interno poziva `_get_soup` +
  `_parse_devices`.
- `fetch_all(url, timeout=15) -> Tuple[List[Device], List[UpsPowerStatus]]` —
  nova javna funkcija, jedan HTTP fetch, oba parsiranja. Koristi je
  `cloud verzija/service.py`.

`_parse_ups_statuses` parsira redove `upsTable` analogno postojećoj logici:
`cols[0]` sadrži `<span class="hostname">`/`<span class="dim">` (isto kao
devices tabela), `cols[1]` je status badge (`get_text(strip=True)`), `cols[2]`
je baterija (`"87%"` → `int("87")`, sa `try/except` fallback na `0` ako
parsiranje ne uspe). Redovi sa manje od 3 kolone ili bez hostname-a se
preskaču (isti obrazac kao `_parse_devices`).

## 2. Praćenje stanja i pravilo alarma (`cloud verzija/service.py`)

Novo polje u `ServiceState`: `ups_not_ok_since: Dict[str, datetime]` — isti
princip kao postojeći `down_since` za obične uređaje (prisustvo ključa =
trenutno "nije AC OK"). Novi `ups_power_tracker: AlertTracker` (isti tip kao
`threshold_tracker`/`ups_tracker`/`watchdog_tracker`) za kontrolu ponavljanja.

Nova pura funkcija `check_ups_power(cfg, db_path, state, ups_statuses, now_utc)
-> List[Notification]`, pozvana iz `run()` odmah pored `run_once()` (nezavisna
petlja, poseban podatak):

- Filtrira `ups_statuses` da isključi `EXCLUDED_UPS_HOSTNAMES = {"UPS-7"}`.
- Za svaki preostali `UpsPowerStatus`:
  - Ako je `is_ac_ok` I hostname je u `state.ups_not_ok_since` → **oporavak**:
    ukloni iz `ups_not_ok_since`, zatvori period u bazi
    (`stats.close_ups_power_period`), resetuj `ups_power_tracker`, izračunaj
    ukupno trajanje, pošalji poruku oporavka (Telegram + email) svim
    primaocima.
  - Ako je `is_ac_ok` i hostname NIJE u `ups_not_ok_since` → ništa (normalno
    stanje).
  - Ako NIJE `is_ac_ok` i hostname NIJE u `ups_not_ok_since` → prvi put
    primećeno: upiši `now_utc` u `ups_not_ok_since`, otvori period u bazi
    (`stats.open_ups_power_period`).
  - Ako NIJE `is_ac_ok` (bilo prvi put ili već traje): izračunaj trajanje od
    `ups_not_ok_since[hostname]`; ako je ≥ 3 minuta (novi config
    `UPS_POWER_CONFIRM_MINUTES`, default `3`) I nije mutiran
    (`stats.is_muted_effective`) I `ups_power_tracker.should_alert(...)` (na
    `ALERT_REPEAT_MINUTES`) → pošalji alarm (Telegram + email), zabeleži
    `record_sent`.

Sve provere mute-a koriste postojeći `stats.is_muted_effective(db_path,
hostname, now_utc)` (bez izmena — hostname `"UPS-11"` je samo još jedan
string scope, radi bez ikakvih izmena u `stats.py`-jevoj mute logici).

## 3. Poruke (`cloud verzija/reports.py`)

```python
def format_ups_power_alert(status: UpsPowerStatus, duration_seconds: float) -> str
def format_ups_power_recovered(status: UpsPowerStatus, duration_seconds: float) -> str
```

Format alarma (generičan naslov + tačan status sa sajta, kako je odlučeno —
ne pretpostavljamo tačnu formulaciju za "na bateriji"). Napomena: svaki UPS
ima sopstveni nezavisni tajmer (ne okidaju se nužno zajedno), pa je svaka
poruka o **jednom** uređaju — jednina, ne množina kao u korisnikovom
originalnom primeru (koji je bio generički template):

```
MLFF ALARM - UPS uredjaj nije na mreznom napajanju (AC OK)

UPS-11
IP: 172.23.11.4
Lokacija: Portal 11
Status: ERR
Baterija: 87%
Trenutno trajanje: 5m
```

Format oporavka:

```
MLFF - UPS uredjaj vracen na mrezno napajanje

UPS-11
IP: 172.23.11.4
Lokacija: Portal 11
Trajanje ispada: 42m
```

Oba idu i na Telegram (`Notification("telegram", chat_id, text)`) i na email
(`Notification("email", addr, text, subject=...)`) za svakog primaoca iz
`cfg["telegram_chat_ids"]` i `cfg["email_recipients"]`, nezavisno od
`NOTIFY_EMAIL`/`NOTIFY_TELEGRAM` prekidača (ovaj alarm je kritičan i uvek
aktivan na oba kanala, po zahtevu — nema poseban on/off toggle za sada).

## 4. Istorija u bazi (`cloud verzija/stats.py`)

Nova tabela, isti obrazac kao `status_periods`:

```sql
CREATE TABLE IF NOT EXISTS ups_power_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    status_text TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_ups_power_periods_hostname ON ups_power_periods(hostname);
```

Nove funkcije: `open_ups_power_period(db_path, hostname, status_text, ts)`,
`close_ups_power_period(db_path, hostname, ts)` — analogne postojećim
`open_initial_period`/`record_transition`, ali pojednostavljene (samo
otvori/zatvori, bez `day_stats`-stila upita za sada — to je van scope-a dok
ne zatreba komanda za pregled).

## Konfiguracija — novi env var

| Env var | Default | Značenje |
|---|---|---|
| `UPS_POWER_CONFIRM_MINUTES` | `3` | Koliko dugo UPS mora biti "nije AC OK" pre alarma |

(`ALERT_REPEAT_MINUTES` se ponovo koristi, već postoji.)

## Testiranje

- `scraper.py`: test da `_parse_ups_statuses` ispravno parsira redove
  `upsTable` (uključujući `AC OK`/`ERR` primere iz stvarnog snapshot-a),
  vraća prazan spisak ako tabela ne postoji, `UpsPowerStatus.portal_id`
  ispravno izvodi broj iz imena, `fetch_all` vraća oba spiska iz jednog fetch-a
  (mock `_get_soup` da se proveri da se poziva samo jednom).
- `reports.py`: testovi za `format_ups_power_alert`/`format_ups_power_recovered`
  (sadrže hostname, IP, portal, status, bateriju, trajanje).
- `stats.py`: testovi za `open_ups_power_period`/`close_ups_power_period`.
- `service.py` (`check_ups_power`): testovi za prvi ciklus (bez alarma pre 3
  min), alarm posle 3 min, ponavljanje na `ALERT_REPEAT_MINUTES`, poruka
  oporavka i tačno trajanje, `UPS-7` nikad ne generiše alarm, mute suzbija
  alarm (i pojedinačni i `__ALL__`), i da se šalje i na Telegram i na email
  kanal.
- Ručni smoke test sa pravim podacima (kao za v2/v3) — potvrdi da `fetch_all`
  radi na pravom sajtu i da trenutni `AC OK`/`ERR` primeri iz stvarnog
  snapshot-a parsiraju ispravno.
