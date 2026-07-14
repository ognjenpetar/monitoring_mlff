# Oracle Cloud Free Tier – kreiranje VM-a za MLFF Monitoring

Korak-po-korak uputstvo za kreiranje besplatne Linux VM (Always Free) na Oracle Cloud,
na kojoj će raditi `mlff-monitor` servis 24/7.

Rezultat na kraju ovog uputstva: VM sa fiksnim (rezervisanim) javnim IP-om, spreman za
Docker, sa tim IP-om predatim mrežnom administratoru na whitelisting.

---

## 1. Registracija naloga

1. Idi na [www.oracle.com/cloud/free](https://www.oracle.com/cloud/free/)
2. Klikni **Start for free**
3. Popuni formu: email, ime, država, itd.
4. **Verifikacija kartice** — Oracle traži broj kartice isključivo radi potvrde identiteta
   (sprečavanje zloupotrebe besplatnih naloga). Resursi u **Always Free** kategoriji se
   ne naplaćuju osim ako eksplicitno ne nadogradiš nalog na plaćeni ("Pay As You Go").
   Ne postoji automatska naplata bez tvoje eksplicitne akcije.
5. Potvrdi broj telefona (SMS kod)
6. Sačekaj da se nalog aktivira (obično par minuta, ponekad do 1h)

> **Napomena:** Oracle povremeno odbija registracije iz određenih zemalja/regiona ili
> zahteva dodatnu verifikaciju. Ako naiđeš na problem, pokušaj ponovo za par sati ili
> kontaktiraj Oracle podršku preko chat-a na stranici.

---

## 2. Kreiranje VM instance (Always Free)

1. Uloguj se na [cloud.oracle.com](https://cloud.oracle.com)
2. Levo gore, hamburger meni → **Compute** → **Instances**
3. Klikni **Create Instance**
4. Podešavanja:

   | Polje | Vrednost |
   |---|---|
   | Name | `mlff-monitor` |
   | Placement | ostavi default (Availability Domain koji ti ponudi) |
   | Image and shape → Image | **Ubuntu 22.04** (klikni "Change image" ako nije već izabrano) |
   | Image and shape → Shape | Klikni "Change shape" → **Ampere** → **VM.Standard.A1.Flex** → podesi **1 OCPU / 6 GB RAM** (u okviru Always Free limita od 4 OCPU/24GB ukupno, ovo je i više nego dovoljno za ovu aplikaciju) |

   > Ako Ampere A1 shape nije dostupan u tvom regionu (ponekad piše "Out of capacity"),
   > alternativa je **VM.Standard.E2.1.Micro** (AMD, uvek dostupan, manji resursi — i
   > dalje dovoljno za ovu aplikaciju).

5. **Networking**: ostavi default VCN (Oracle će ga kreirati automatski ako ne postoji).
   Proveri da je čekirano **"Assign a public IPv4 address"** — mora biti uključeno.
6. **Add SSH keys**: izaberi **"Generate a key pair for me"**, pa klikni **"Save private key"**
   i **"Save public key"** — sačuvaj oba fajla na sigurno mesto na svom računaru
   (npr. `C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key`). Privatni ključ ti treba za
   SSH konekciju, ne deli ga ni sa kim.
7. Ostalo ostavi na default vrednostima.
8. Klikni **Create**.
9. Sačekaj da status instance postane **Running** (obično 1-2 minuta).

---

## 3. Rezervacija statičnog (Reserved) javnog IP-a

Ovo je **ključan korak** — bez njega bi se javni IP promenio pri svakom restartu VM-a,
što bi pokvarilo whitelisting na Orion strani.

1. Na stranici instance (Compute → Instances → `mlff-monitor`), otvori tab
   **"Instance details"** i pronađi trenutni **Public IP Address** — zapamti ga, treba
   ga zameniti rezervisanim.
2. Levo hamburger meni → **Networking** → **IP Management** → **Reserved Public IPs**
3. Klikni **Create Reserved Public IP**
4. Ime: `mlff-monitor-ip`, Compartment: isti kao za VM
5. Klikni **Create Reserved Public IP**
6. Kada je kreiran, klikni na njega → **Assign** → izaberi **Private IP** koji pripada
   tvojoj `mlff-monitor` instanci
7. Potvrdi — sada instanca ima fiksni javni IP koji se neće promeniti ni posle restarta.
8. Zapiši taj IP — **ovo je adresa koju daješ mrežnom administratoru za whitelisting.**

---

## 4. Otvaranje potrebnih portova (Security List / Network Security Group)

Servis ne prima dolazne konekcije spolja (nema web dashboard u ovoj verziji), pa
**ne treba** otvarati inbound portove osim SSH-a za tvoje potrebe održavanja.

1. Networking → Virtual Cloud Networks → tvoj VCN → Security Lists → Default Security List
2. Proveri da postoji **Ingress Rule** za port **22 (SSH)** sa izvora `0.0.0.0/0` (obično
   je već tu po defaultu pri kreiranju instance). Po želji, radi bezbednosti, možeš
   ograničiti izvor na tvoj kućni/kancelarijski IP umesto `0.0.0.0/0`.
3. Nema potrebe za dodatnim ingress pravilima — servis samo inicira odlazne (outbound)
   konekcije ka `mlff.sdn.rs`, Telegram API-ju i Gmail SMTP-u, što je po defaultu
   dozvoljeno (egress je otvoren po defaultu).

---

## 5. Povezivanje na VM preko SSH

Windows (PowerShell), koristeći ključ sačuvan u koraku 2.6:

```powershell
ssh -i "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" ubuntu@<REZERVISANI_JAVNI_IP>
```

Ako dobiješ grešku o permisijama fajla ključa na Windows-u, otvori PowerShell kao admin i:

```powershell
icacls "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" /inheritance:r
icacls "C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key" /grant:r "$($env:USERNAME):(R)"
```

---

## 6. Instalacija Docker-a na VM

Nakon uspešnog SSH konektovanja, na VM-u (Ubuntu) pokreni:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Izloguj se i ponovo uloguj (ili pokreni `newgrp docker`) da grupa `docker` bude aktivna
za tvog korisnika bez potrebe za `sudo` pri svakom docker komandi.

Proveri instalaciju:

```bash
docker --version
docker compose version
```

---

## 7. Deploy MLFF Monitoring servisa

1. Sa svog računara, kopiraj `cloud verzija/` folder na VM (npr. preko `scp`), ili
   kloniraj git repo direktno na VM-u:

   ```bash
   git clone https://github.com/ognjenpetar/monitoring_mlff.git
   cd monitoring_mlff/cloud\ verzija
   ```

2. Napravi `.env` fajl na osnovu `.env.example`:

   ```bash
   cp .env.example .env
   nano .env
   ```

   Popuni SMTP i Telegram vrednosti (iste kao u desktop aplikaciji).

3. Pokreni:

   ```bash
   docker compose up -d --build
   ```

4. Proveri logove:

   ```bash
   docker compose logs -f
   ```

   Trebalo bi da vidiš periodičnu proveru statusa uređaja. Ako piše greška o
   nedostupnosti `mlff.sdn.rs` — to je očekivano **dok admin ne whitelistuje IP** iz
   koraka 3. Nakon whitelistinga, restartuj servis (`docker compose restart`) i proveri
   ponovo.

---

## 8. Šta predati mrežnom administratoru

Pošalji mu **samo rezervisani javni IP** iz koraka 3 (ne treba mu ništa drugo — ni
kredencijali, ni VPN nalog, ni SSH pristup) sa napomenom:

> Treba da whitelistuješ javni IP `<REZERVISANI_JAVNI_IP>` na firewall-u ispred
> `mlff.sdn.rs`, dozvoljavajući HTTPS (port 443) pristup sa tog IP-a. Ovaj IP je
> statičan/rezervisan i neće se menjati.

---

## 9. Održavanje

- **Update aplikacije** (kad se kod promeni):
  ```bash
  cd monitoring_mlff && git pull
  cd "cloud verzija" && docker compose down && docker compose up -d --build
  ```
- **Provera da li servis radi**: `docker compose ps`
- **Restart**: `docker compose restart`
- **Logovi**: `docker compose logs --tail=100 -f`

---

## Troškovi

Sve u ovom uputstvu (VM.Standard.A1.Flex 1 OCPU/6GB, Reserved Public IP, 50GB boot disk)
spada u **Always Free** limite Oracle Cloud-a. Dokle god ne dodaš dodatne resurse preko
tih limita, mesečni trošak je **0**. Oracle šalje email upozorenje ako bi bilo koja
akcija generisala trošak, pre nego što se to zaista naplati.
