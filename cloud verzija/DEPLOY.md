# MLFF Monitor – Cloud verzija

`mlff.sdn.rs` je dostupan samo sa whitelistovanih javnih IP adresa (interni Orion
firewall). Server na kom radi ovaj servis mora imati **statičan javni IP koji je
whitelistovan** od strane Orion mrežnog administratora — detaljno uputstvo za
kreiranje takvog servera na Oracle Cloud Free Tier: [`../ORACLE_CLOUD_SETUP.md`](../ORACLE_CLOUD_SETUP.md).

## Pokretanje

### Preduslovi
- Docker i Docker Compose instalirani na serveru
- Server ima statičan javni IP, whitelistovan za pristup `mlff.sdn.rs`

### Koraci

```bash
# 1. Kopiraj .env.example u .env i popuni vrednosti
cp .env.example .env
nano .env

# 2. Pokreni
docker compose up -d

# 3. Provjeri logove
docker compose logs -f
```

### Zaustavljanje / restart
```bash
docker compose down
docker compose restart
```

### Update (kada promeniš kod)
```bash
docker compose down
docker compose build
docker compose up -d
```

## Gde može da radi

Bilo koji server sa **statičnim javnim IP-om** koji Orion whitelistuje. Preporučeno:

| Opcija | Šta treba |
|--------|-----------|
| **Oracle Cloud Free Tier VM** (preporučeno) | Besplatno zauvek, statičan IP — vidi [`ORACLE_CLOUD_SETUP.md`](../ORACLE_CLOUD_SETUP.md) |
| Bilo koja druga cloud VM sa javnim IP-om | Docker + statičan/rezervisan javni IP |
| Server već u Orion internoj mreži (NAS, Raspberry Pi, Proxmox VM) | Docker — nije potreban whitelisting jer je već interno, ali zahteva da neko drži tu mašinu upaljenu 24/7 |

## Logovi

Logovi se čuvaju u Docker JSON logu (max 10 MB × 3 fajla).
Pregledaj ih sa:
```bash
docker compose logs --tail=100 -f
```
