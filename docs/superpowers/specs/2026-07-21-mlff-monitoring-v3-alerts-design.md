# MLFF Monitoring v3 — grupisanje alarma, watchdog, mute, sparkline, sedmični izveštaj

Datum: 2026-07-21
Status: odobreno u brainstorming fazi, spreman za implementacioni plan

## Kontekst

Posle v2 (statistika, threshold/UPS alarmi, `/live` `/stat` `/juce`, dnevni izveštaj —
vidi `docs/superpowers/specs/2026-07-14-mlff-monitoring-v2-design.md`), urađena je
analiza celog sistema i predloženo 20 pravaca unapređenja (10 "proverenih rešenja" +
10 "van okvira"). Korisnik je izabrao 7 predloga za implementaciju. Ovi se dele u
dve nezavisne grupe:

- **Grupa A** (Predlog 01 — veb dashboard, Predlog 02 — javna status stranica):
  zahtevaju novu komponentu i otvaranje porta na Oracle VM-u (akcija korisnika u
  Oracle konzoli). **Van scope-a ovog spec-a** — rade se u posebnom ciklusu kasnije.
- **Grupa B** (ovaj spec): Predlog 03 (grupisanje alarma po portalu), Predlog 04
  (sparkline u Telegram porukama), Predlog 06 (watchdog za sam servis), Predlog 08
  (mute/unmute komande), Ideja 03 (sedmični rang pouzdanosti). Sve su izmene
  postojećih modula u `cloud verzija/`, bez novih spoljnih zavisnosti ili
  infrastrukture.

## Cilj

1. Kada 2+ uređaja istog `portal_id` padnu u istom ciklusu, pošalji i agregiranu
   poruku o celom portalu i pojedinačne per-event poruke za svaki uređaj.
2. Ako fetch ka `mlff.sdn.rs` neprekidno ne uspeva 30 minuta, pošalji poseban
   "servis ima problem" alarm, različit od "uređaj je dole".
3. Nove Telegram komande `/mute`, `/mutesve`, `/unmute`, `/unmutesve`, `/muted` za
   privremeno utišavanje alarma (fiksno 3h, može se ranije prekinuti).
4. Tekstualni sparkline (mrežni uptime poslednjih 7 dana) u `/stat` i `/juce`
   odgovorima.
5. Automatski sedmični izveštaj (rang lista uređaja po downtime-u) svakog
   ponedeljka u 09:01, uz generalizaciju `format_day_report` na proizvoljan
   broj dana.

## Van scope-a

- Grupa A (veb dashboard, javna status stranica) — poseban spec kasnije.
- Bilo kakva izmena `app.py` (desktop GUI) ili `stabilna verzija/`.
- Mutiranje dnevnog/sedmičnog izveštaja — mute utiče samo na alarme
  (per-event, threshold, UPS, watchdog), ne na zakazane izveštaje.

## 1. `mutes` tabela (`cloud verzija/stats.py`)

Nova tabela u istoj SQLite bazi:

```sql
CREATE TABLE IF NOT EXISTS mutes (
    scope TEXT PRIMARY KEY,   -- hostname, ili '__ALL__' za globalni mute
    expires_at TEXT NOT NULL  -- ISO8601 UTC
);
```

Nove funkcije u `stats.py`:
- `mute(db_path, scope, expires_at)` — upiši/prepiši (INSERT OR REPLACE) mute za `scope`.
- `unmute(db_path, scope)` — obriši red za `scope` (no-op ako ne postoji).
- `is_muted(db_path, scope, now) -> bool` — `True` ako postoji red za `scope` ILI za `__ALL__` sa `expires_at > now`. Isteklih redova se ne mora fizički brisati na svakoj proveri (čisti se lenjo — vidi ispod), samo se ignorišu u poređenju.
- `list_active_mutes(db_path, now) -> List[dict]` — svi redovi sa `expires_at > now`, sortirano po `expires_at`, oblik `{"scope": str, "expires_at": datetime}`.
- `purge_expired_mutes(db_path, now)` — obriši sve redove sa `expires_at <= now` (poziva se na početku svakog poll ciklusa, drži tabelu malom).

`__ALL__` ne može biti pravi hostname (uređaji na `mlff.sdn.rs` nemaju donje crte niti taj format), pa nema kolizije.

## 2. Grupisanje alarma po portalu (Predlog 03)

U `run_once()` (`service.py`), posle postojeće per-event notifikacije logike:

- Grupiši `changed` (uređaje koji su promenili status ovog ciklusa) po `portal_id`.
- Za svaki portal gde su **svi aktivni** uređaji tog `portal_id` (unutar `active` — dakle posle `EXCLUDED_HOSTNAMES` filtera, ne unutar sirovog `devices`) trenutno DOWN, i gde ima **2 ili više** takvih aktivnih uređaja, i gde je **bar jedan** od njih upravo prešao u DOWN ovog ciklusa (deo `changed`) — pošalji dodatnu agregiranu Telegram poruku: `format_portal_down_alert(portal_id, hostnames)`. Portali sa manje od 2 aktivna uređaja (npr. portal 7, gde su oba uređaja u `EXCLUDED_HOSTNAMES`) nikad ne generišu agregat.
- Ova agregirana poruka se šalje **pored** postojećih pojedinačnih per-event poruka za te uređaje, ne umesto njih.
- Poštuje mute: ako je portal-agregat u pitanju, preskače se ako je **bilo koji** od pogođenih hostname-ova mutiran ili je `__ALL__` aktivan (konzervativno — bolje da se prećuti agregat nego da pogrešno pomene mutiran uređaj).
- Uslov "svi uređaji tog portala su DOWN" sprečava lažne agregate kad je npr. samo UPS pao a IOL/OVI1 rade normalno (to ostaje običan pojedinačni alarm).

`reports.py` dobija `format_portal_down_alert(portal_id: str, hostnames: List[str]) -> str`.

## 3. Watchdog za sam servis (Predlog 06)

Novo polje u `ServiceState`: `fetch_failure_since: Optional[datetime] = None`.

U `run()` (ne u `run_once()`, pošto prati I/O uspeh/neuspeh fetch-a):
- Kad `fetch_devices()` baci izuzetak: ako je `fetch_failure_since is None`, postavi na `now_utc`. Izračunaj trajanje neprekidnog neuspeha; ako je ≥ 30 minuta, i (nikad poslat watchdog alarm ILI prošlo ≥ `ALERT_REPEAT_MINUTES` od poslednjeg) i nije mutiran `__ALL__` — pošalji `format_watchdog_alert(duration_seconds)` na Telegram, koristeći **treći, poseban `AlertTracker`** (`state.watchdog_tracker`) da se ponavljanje prati nezavisno od threshold/UPS alarma.
- Kad fetch uspe: resetuj `fetch_failure_since = None` i `state.watchdog_tracker.reset("__watchdog__")`.

Nova env varijabla: `WATCHDOG_THRESHOLD_MINUTES` (default `30`). Bez novog on/off toggle-a — watchdog je uvek uključen (previše bitan da bi imao poseban prekidač), ali se poštuje `/mutesve`.

`reports.py` dobija `format_watchdog_alert(duration_seconds: float) -> str`.

## 4. Mute/unmute Telegram komande (Predlog 08)

Proširenje `telegram_poll.py`: `COMMANDS` postaje `{"/live", "/stat", "/juce", "/mute", "/mutesve", "/unmute", "/unmutesve", "/muted"}`. `/mute` i `/unmute` su komande **sa argumentom** (hostname posle razmaka), za razliku od ostalih koje su samostalne reči — `parse_command`/`extract_commands` se proširuju da vrate i argument kad postoji (npr. `{"command": "/mute", "arg": "SCPA1055-R-UPS"}`).

Fiksno trajanje mute-a: `MUTE_DURATION_MINUTES` env var, default `180` (3h).

Ponašanje u `_handle_command` (`service.py`):
- `/mutesve` → `stats.mute(db_path, "__ALL__", now_utc + 3h)`, odgovori "Sva obaveštenja utišana do HH:MM (dd.mm)."
- `/mute HOSTNAME` → proveri da hostname postoji među poznatim uređajima (iz `state.active_devices`, case-insensitive poređenje); ako ne postoji, odgovori sa greškom i ne upisuj ništa. Ako postoji, `stats.mute(db_path, hostname, now_utc + 3h)`, odgovori sa potvrdom.
- `/unmutesve` → `stats.unmute(db_path, "__ALL__")`, potvrda (ili "Nije ni bilo aktivno utišavanje." ako nije postojalo).
- `/unmute HOSTNAME` → `stats.unmute(db_path, hostname)`, potvrda.
- `/muted` → `stats.list_active_mutes(db_path, now_utc)`, formatiraj listu (`format_muted_list`) — "Nema aktivnih utišavanja." ako je prazna.

Sve provere alarma (per-event, threshold, UPS, watchdog, portal-agregat) u `run_once()`/`run()` prvo pozivaju `stats.purge_expired_mutes()` na početku ciklusa, pa proveravaju `stats.is_muted(db_path, "__ALL__", now_utc)` i/ili `stats.is_muted(db_path, hostname, now_utc)` pre slanja. Mute **ne utiče** na upis u `stats.db` (statistika se i dalje beleži tačno) — utiče samo na to da li se šalje Telegram poruka.

`reports.py` dobija `format_muted_list(mutes: List[dict]) -> str`.

## 5. Sparkline u `/stat` i `/juce` (Predlog 04)

`reports.py` dobija `format_sparkline(values: List[float]) -> str` — pretvara listu procenata (0-100) u Unicode sparkline string koristeći 8 nivoa (`▁▂▃▄▅▆▇█`), mapirano linearno na min/max unutar prosleđene liste (ili na 0-100 fiksni opseg — koristićemo fiksni opseg 0-100 pošto je uptime % uvek u tom rasponu, jednostavnije i uporedivo između poziva).

U `_handle_command` za `/stat` i `/juce` (`service.py`): izračunaj mrežni uptime % za svakog od poslednjih 7 dana pozivom `stats.day_stats()` sedam puta sa uzastopnim `local_day_bounds()` prozorima, izvuci mrežni uptime % iz svakog (isti proračun kao u `format_day_report`), i dodaj red `Poslednjih 7 dana: ▃▅▇▇▆█▇` ispod postojećeg "Mrezni uptime: X%" reda u odgovoru. Za `/juce`, "sedam dana" su 7 punih dana zaključno sa jučerašnjim (najnovija tačka u sparkline-u = juče). Za `/stat`, "sedam dana" su 6 prethodnih punih dana plus danas-do-sada kao poslednja (7.) tačka — dosledno sa time da `/stat` inače prikazuje parcijalan, tekući dan.

## 6. Generalizacija `format_day_report` + sedmični izveštaj (Ideja 03)

`format_day_report(title, day_stats_by_host, period_days: int = 1)` — dodaje se
parametar `period_days` (default `1`, zadržava postojeće ponašanje za `/stat`/`/juce`/dnevni
izveštaj). Umesto hardkodovanog `total_seconds = 86400`, koristi se
`total_seconds = 86400 * period_days`. Ovim se otklanja upozorenje iz ranijeg code
review-a (funkcija je bila dokumentovana kao "samo za jedan dan").

`stats.py` dobija `local_week_bounds(week_start_date: date, tz: ZoneInfo) -> Tuple[datetime, datetime]` — vraća granice za 7 dana počevši od `week_start_date` (poziva `local_day_bounds` za prvi i poslednji dan i kombinuje).

U `run_once()`, novi blok analogan dnevnom izveštaju: ako je lokalno vreme ≥
`WEEKLY_REPORT_TIME` (env var, default `09:01`) I danas je ponedeljak
(`now_local.weekday() == 0`) I sedmični izveštaj za tu nedelju još nije poslat
(nova `stats.was_weekly_report_sent(db_path, week_start_date)` / `mark_weekly_report_sent`,
analogno postojećem `was_report_sent`/`mark_report_sent` za dnevni, ali u posebnoj
tabeli `sent_weekly_reports(week_start_date)`) — izračunaj `day_stats()` za
prethodnih 7 dana (ponedeljak-nedelja pre ovog ponedeljka) i pošalji
`format_day_report(f"Sedmicni izvestaj: {week_start} - {week_end}", data, period_days=7)`.

Sedmični izveštaj **nije mutable** (isto kao dnevni — mute utiče samo na alarme).

## Konfiguracija — novi env var-ovi

| Env var | Default | Značenje |
|---|---|---|
| `WATCHDOG_THRESHOLD_MINUTES` | `30` | Koliko dugo fetch mora neprekidno da ne uspeva pre watchdog alarma |
| `MUTE_DURATION_MINUTES` | `180` | Fiksno trajanje `/mute` i `/mutesve` |
| `WEEKLY_REPORT_TIME` | `09:01` | Vreme slanja sedmičnog izveštaja (ponedeljkom) |

## Testiranje

- `stats.py`: testovi za `mute`/`unmute`/`is_muted`/`list_active_mutes`/`purge_expired_mutes` (uključujući `__ALL__` slučaj), `local_week_bounds`, `was_weekly_report_sent`/`mark_weekly_report_sent`.
- `reports.py`: testovi za `format_portal_down_alert`, `format_watchdog_alert`, `format_muted_list`, `format_sparkline` (uključujući granične vrednosti 0% i 100%), i `format_day_report` sa `period_days=7`.
- `telegram_poll.py`: testovi da `parse_command`/`extract_commands` ispravno izvlače argument za `/mute HOSTNAME` i `/unmute HOSTNAME`, i da rade i bez argumenta za `/mutesve`/`/unmutesve`/`/muted`.
- `service.py` (`run_once`): testovi za portal-agregat (2+ uređaja istog portala padnu istovremeno → agregat + pojedinačne poruke; samo 1 padne → nema agregata; UPS padne a ostali rade → nema agregata), watchdog (fires posle 30 min, ponavlja se na `ALERT_REPEAT_MINUTES`, resetuje se na uspešan fetch), mute (mutiran hostname ne generiše alarme ali se i dalje upisuje u stats.db; `__ALL__` mute pokriva i watchdog), sedmični izveštaj (šalje se samo ponedeljkom, samo jednom po nedelji).
