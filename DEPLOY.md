# Deploy MLFF Monitoring na Oracle Cloud VM — korak po korak

Ovo uputstvo nastavlja odmah nakon što je VM kreiran i status mu je **Running**
(vidi [`ORACLE_CLOUD_SETUP.md`](ORACLE_CLOUD_SETUP.md) za kreiranje same VM).
Pisano je detaljno, kao za potpunog početnika — svaki korak, svaka komanda.

---

## Korak 1 — Rezervacija statičnog (fiksnog) javnog IP-a

Bez ovog koraka, javni IP tvoje VM bi se promenio pri svakom restartu — a to bi
pokvarilo whitelisting kod mrežnog administratora. Zato prvo rezervišemo IP koji
se **nikad ne menja**.

1. U levom meniju Oracle konzole (hamburger ikonica ☰ gore levo) klikni **Networking**
   → **IP Management** → **Reserved Public IPs**

   (Ako ne vidiš "IP Management" direktno, klikni prvo "Networking" u glavnom meniju,
   pa potraži "IP Management" u pod-meniju levo)

2. Klikni dugme **Create Reserved Public IP** (gore desno)

3. Popuni formu:
   - **Name**: `mlff-monitor-ip`
   - **Compartment**: ostavi default (`ognjenpetar (root)`)

4. Klikni **Create Reserved Public IP**

5. Sačekaj par sekundi da se kreira (status će pokazati "Available")

6. Klikni na novo-kreiran IP (na listi Reserved Public IPs) da otvoriš njegove detalje

7. Klikni dugme **Assign** (ili "Assign to a private IP")

8. Izabraćeš:
   - **Instance**: tvoja instanca (`instance-20260714-1622` ili kako god si je nazvao)
   - **Private IP**: automatski će ponuditi private IP te instance — potvrdi

9. Klikni **Assign**

10. Sačekaj par sekundi, osveži stranicu — sada bi trebalo da vidiš dodeljen javni
    IP pored imena tvoje rezervacije. **Zapiši taj IP** — to je adresa koju:
    - koristiš za SSH konekciju (Korak 2)
    - daješ mrežnom administratoru na whitelisting (Korak 6)

> **Napomena:** Ako je VM tokom kreiranja već dobila neki privremeni (ephemeral)
> javni IP, dodeljivanjem rezervisanog IP-a on ga zamenjuje — stara ephemeral
> adresa (ako je uopšte postojala) se oslobađa. To je normalno i očekivano.

---

## Korak 2 — Poveži se na VM preko SSH

Ključ koji si preuzeo tokom kreiranja VM-a treba da bude sačuvan lokalno, npr:
`C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key`

Otvori **PowerShell** na svom računaru i pokreni (zameni `<JAVNI_IP>` sa IP-om iz
Koraka 1):

```powershell
ssh -i "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" ubuntu@<JAVNI_IP>
```

- Prvi put će te pitati da potvrdiš "fingerprint" konekcije — ukucaj `yes` i Enter.
- Ako dobiješ grešku o dozvolama fajla ključa, pokreni ovo pa probaj ponovo:
  ```powershell
  icacls "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" /inheritance:r
  icacls "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" /grant:r "$($env:USERNAME):(R)"
  ```

Kada uspešno uđeš, videćeš prompt nalik na `ubuntu@instance-...:~$` — to znači da
si sada "unutar" VM-a i sve dalje komande kucaš tu (na VM-u), ne na svom računaru.

---

## Korak 3 — Instalacija Docker-a na VM

Kucaj redom (svaku liniju posebno, Enter posle svake), **na VM-u** (u SSH sesiji):

```bash
sudo apt update && sudo apt upgrade -y
```
(sačekaj da završi, može potrajati minut-dva)

```bash
sudo apt install -y docker.io docker-compose-plugin git
```

```bash
sudo systemctl enable --now docker
```

```bash
sudo usermod -aG docker $USER
```

Zatim se izloguj i uloguj ponovo da nova dozvola (grupa `docker`) počne da važi:

```bash
exit
```

pa se ponovo poveži preko SSH (ista komanda kao u Koraku 2).

Proveri da je sve instalirano:

```bash
docker --version
docker compose version
```

Trebalo bi da vidiš verzije bez greške.

---

## Korak 4 — Preuzmi kod aplikacije na VM

I dalje u SSH sesiji, na VM-u:

```bash
git clone https://github.com/ognjenpetar/monitoring_mlff.git
```

```bash
cd monitoring_mlff/"cloud verzija"
```

---

## Korak 5 — Podesi kredencijale (.env fajl)

```bash
cp .env.example .env
```

```bash
nano .env
```

Otvoriće se tekst editor unutar terminala. Uredi vrednosti (koristi strelice na
tastaturi da se pomeraš, ne miš):

```
MONITOR_URL=https://mlff.sdn.rs
CHECK_INTERVAL_SEC=60

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tvoj.email@gmail.com
SMTP_PASSWORD=tvoj_app_password
EMAIL_RECIPIENTS=ognjen.petar.todorovic@oriontelekom.rs
NOTIFY_EMAIL=true

TELEGRAM_BOT_TOKEN=tvoj_telegram_bot_token
TELEGRAM_CHAT_IDS=tvoj_chat_id
NOTIFY_TELEGRAM=true
```

(iste vrednosti kao u desktop aplikaciji — SMTP App Password i Telegram bot token
iz `UPUTSTVO.md`)

Kad završiš uređivanje:
- `Ctrl + O` pa **Enter** → snima fajl
- `Ctrl + X` → izlazi iz editora

---

## Korak 6 — Pokreni aplikaciju

```bash
docker compose up -d --build
```

Prva izgradnja slike traje minut-dva. Kada završi, proveri da li servis radi:

```bash
docker compose ps
```

Trebalo bi da vidiš `mlff-monitor` sa statusom `Up`.

Pogledaj logove uživo:

```bash
docker compose logs -f
```

(`Ctrl + C` da izađeš iz praćenja logova — servis nastavlja da radi u pozadini)

**Dok admin ne whitelistuje IP (Korak 7), u logovima ćeš videti grešku tipa
"Greska pri dohvatanju" za `mlff.sdn.rs` — to je normalno i očekivano za sada.**

---

## Korak 7 — Predaj IP administratoru na whitelisting

Pošalji kolegi koji administrira mrežu **samo javni IP** iz Koraka 1, sa porukom
otprilike:

> Treba da whitelistuješ javni IP `<JAVNI_IP>` na firewall-u ispred `mlff.sdn.rs`,
> da dozvoli HTTPS (port 443) pristup sa tog IP-a. IP je statičan/rezervisan i
> neće se menjati.

Kad ti javi da je whitelistovao, restartuj servis da odmah proveri konekciju:

```bash
docker compose restart
docker compose logs -f
```

Ako sad vidiš normalne provere statusa uređaja bez greške — sve radi.

---

## Održavanje (za kasnije)

**Update aplikacije** (kad se kod na GitHub-u promeni):
```bash
cd ~/monitoring_mlff
git pull
cd "cloud verzija"
docker compose down
docker compose up -d --build
```

**Provera statusa**: `docker compose ps`

**Restart**: `docker compose restart`

**Logovi (poslednjih 100 linija, uživo)**: `docker compose logs --tail=100 -f`

**Zaustavljanje**: `docker compose down`

---

## Troubleshooting

| Problem | Rešenje |
|---|---|
| SSH ne radi, "Connection timed out" | Proveri da li je instanca zaista Running, i da li rezervisani IP zaista dodeljen (Korak 1) |
| SSH javlja grešku o dozvolama ključa | Pokreni `icacls` komande iz Koraka 2 |
| `docker: command not found` | Ponovo pokreni Korak 3, proveri da nisi preskočio neki red |
| `permission denied` pri `docker compose` komandama | Nisi se izlogovao/ulogovao ponovo posle `usermod -aG docker` — uradi `exit` pa se ponovo poveži |
| I dalje "Table not found" / greška u logovima posle whitelistinga | Sačekaj par minuta (propagacija firewall pravila), zatim `docker compose restart` |
