# Dodavanje novih primalaca (email i Telegram)

Procedura za dodavanje kolege da prima MLFF Monitoring obaveštenja — email
alarme i/ili Telegram poruke sa cloud servisa (`cloud verzija/`, radi na VM-u).

> Primaoci se podešavaju u `.env` fajlu na VM-u, ne u desktop aplikaciji —
> to su dva odvojena podešavanja (vidi napomenu na dnu).

---

## Email primalac

1. Poveži se na VM preko SSH:
   ```powershell
   ssh -i "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" opc@<JAVNI_IP>
   ```

2. Otvori `.env` fajl za uređivanje:
   ```bash
   cd ~/monitoring_mlff/"cloud verzija"
   nano .env
   ```

3. Pronađi red `EMAIL_RECIPIENTS=...` i dodaj novu email adresu, odvojenu
   zarezom od postojećih (bez razmaka posle zareza):
   ```
   EMAIL_RECIPIENTS=postojeci1@example.com,postojeci2@example.com,novi.kolega@example.com
   ```

4. Snimi i izađi: `Ctrl + O`, **Enter**, pa `Ctrl + X`.

5. Restartuj servis da učita novi `.env`:
   ```bash
   docker compose restart
   ```

---

## Telegram primalac

Telegram zahteva da kolega prvo sâm pošalje poruku botu (bot ne može sam da
"doda" nekoga — Telegram to ne dozvoljava iz privatnosnih razloga), pa se tek
onda njegov chat ID upisuje u podešavanja.

### Korak 1 — kolega piše botu

Zamoli kolegu da u Telegramu pronađe bota (pretraga po imenu bota ili linku
koji mu pošalješ) i pošalje mu bilo koju poruku, npr. `/start`. Bez ovog
koraka njegov chat ID se ne može saznati.

### Korak 2 — pronađi njegov chat ID

Dve opcije, obe rade — izaberi šta ti je lakše:

**Opcija A — preko SSH-a (najbrže):**

Na VM-u (ili bilo gde sa internetom), pozovi Telegram API direktno:

```bash
curl -s "https://api.telegram.org/bot<BOT_TOKEN>/getUpdates"
```

U odgovoru traži deo `"chat":{"id":XXXXXXXXX,"first_name":"..."}` — taj broj
(`XXXXXXXXX`) je chat ID kolege. Prepoznaćeš ga po imenu (`first_name`/`last_name`)
u istom bloku.

**Opcija B — preko desktop aplikacije:**

Otvori `app.py` na svom računaru → **Podešavanja** → sekcija Telegram Bot →
klikni **"Dohvati moj Chat ID"**. Ovo automatski pronalazi sve chat ID-jeve
koji su nedavno pisali botu (uključujući kolegu, ne samo tebe) i ispisuje ih.

> Napomena: ovo dugme upisuje pronađene ID-jeve u desktop aplikacijin
> `config.json`, **ne** i u VM `.env` — chat ID i dalje moraš ručno preneti
> na VM (Korak 3).

### Korak 3 — dodaj chat ID na VM

1. SSH na VM:
   ```powershell
   ssh -i "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" opc@<JAVNI_IP>
   ```

2. Otvori `.env`:
   ```bash
   cd ~/monitoring_mlff/"cloud verzija"
   nano .env
   ```

3. Pronađi red `TELEGRAM_CHAT_IDS=...` i dodaj novi chat ID, odvojen zarezom:
   ```
   TELEGRAM_CHAT_IDS=postojeci_id,novi_chat_id
   ```

4. Snimi (`Ctrl+O`, Enter, `Ctrl+X`) i restartuj:
   ```bash
   docker compose restart
   ```

### Korak 4 — proveri da radi

Zamoli kolegu da botu pošalje `/live` — ako dobije odgovor sa trenutnim
statusom uređaja, uspešno je dodat na listu.

---

## Uklanjanje primaoca

Isti postupak, obrnuto — otvori `.env` na VM-u, ukloni email adresu ili
chat ID iz odgovarajućeg reda (`EMAIL_RECIPIENTS` ili `TELEGRAM_CHAT_IDS`),
snimi, pa `docker compose restart`.

---

## Napomena — desktop aplikacija i cloud servis su odvojeni

`app.py` (desktop, lokalno na tvom računaru) čita primaoce iz `config.json`.
Cloud servis na VM-u čita primaoce iz `.env`. Ovo su **dva nezavisna
podešavanja** — dodavanje nekoga u jedno ne dodaje ga automatski u drugo.
Za redovna obaveštenja (koja rade 24/7) bitan je samo VM `.env`, pošto
desktop aplikacija šalje obaveštenja samo dok je neko ručno drži otvorenu.
