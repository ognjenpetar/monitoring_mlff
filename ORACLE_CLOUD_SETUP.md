# Oracle Cloud Free Tier – kreiranje VM-a za MLFF Monitoring

Korak-po-korak uputstvo za kreiranje besplatne Linux VM (Always Free) na Oracle Cloud,
na kojoj će raditi `mlff-monitor` servis 24/7.

Ovaj fajl pokriva **samo kreiranje VM-a**. Kada instanca ima status **Running**,
nastavi na [`DEPLOY.md`](DEPLOY.md) — tamo je sve dalje (rezervacija javnog IP-a,
SSH, instalacija Docker-a, deploy aplikacije, predaja IP-a administratoru).

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
   | Image and shape → Image | **Ubuntu** (22.04 ili 20.04 — obe rade identično za ovu aplikaciju) |
   | Image and shape → Shape | Klikni "Change shape" → **Ampere** → **VM.Standard.A1.Flex** → podesi **1 OCPU / 6 GB RAM** (u okviru Always Free limita od 4 OCPU/24GB ukupno, ovo je i više nego dovoljno za ovu aplikaciju) |

   > Ako Ampere A1 shape nije dostupan u tvom regionu (ponekad piše "Out of capacity"),
   > alternativa je **VM.Standard.E2.1.Micro** (AMD, uvek dostupan, manji resursi — i
   > dalje dovoljno za ovu aplikaciju).

5. **Security**: ostavi default (Shielded instance isključeno, bez security attributes).
6. **Networking**:
   - Ako nemaš još nijedan VCN, izaberi **"Create new virtual cloud network"** (npr. ime
     `mlff-vcn`) i **"Create new public subnet"** (npr. ime `mlff-public-subnet`).
   - Pokušaj da uključiš **"Automatically assign public IPv4 address"**. Ako je toggle
     zaglavljen/neaktivan (poznat glitch u Oracle konzoli kad se VCN/subnet kreiraju
     "inline"), slobodno nastavi dalje bez njega — javni IP dodajemo posle, u
     `DEPLOY.md`, kao **rezervisani (statični)** IP, što je i bolja opcija od
     privremenog (ephemeral) IP-a koji bi ovaj toggle dodelio.
7. **Add SSH keys**: izaberi **"Generate a key pair for me"**, pa klikni **"Save private key"**
   i **"Save public key"** — sačuvaj oba fajla na sigurno mesto na svom računaru
   (npr. `C:\Users\ognjen.petar\.ssh\mlff-monitor-key.key`). Privatni ključ ti treba za
   SSH konekciju, ne deli ga ni sa kim, i **nikad ga ne čuvaj unutar git repo foldera**.
8. **Storage**: ostavi sve na default (boot volume default veličine, in-transit
   encryption uključen, bez custom encryption key-a, bez dodatnih block volumes).
9. Proveri **Review** stranicu, pa klikni **Create**.
10. Sačekaj da status instance postane **Running** (obično 1-2 minuta).

Kada vidiš status **Running** na stranici instance — pređi na [`DEPLOY.md`](DEPLOY.md).

---

## Troškovi

Sve u ovom uputstvu (VM.Standard.A1.Flex 1 OCPU/6GB, Reserved Public IP, default boot
disk) spada u **Always Free** limite Oracle Cloud-a. Dokle god ne dodaš dodatne resurse
preko tih limita, mesečni trošak je **0**. Oracle šalje email upozorenje ako bi bilo koja
akcija generisala trošak, pre nego što se to zaista naplati.
