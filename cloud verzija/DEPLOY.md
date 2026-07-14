# MLFF Monitor – Cloud verzija (lokalni server)

Servis mora biti na **istoj lokalnoj mreži** kao i `ot.sdn.rs/portali/`
jer je taj URL dostupan samo interno.

## Pokretanje

### Preduslovi
- Docker i Docker Compose instalirani na serveru
- Server je na istoj LAN/VPN mreži kao monitoring URL

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

| Opcija | Šta treba |
|--------|-----------|
| Synology / QNAP NAS | Container Manager (Docker) |
| Raspberry Pi 4 | `apt install docker.io docker-compose` |
| Stari PC/laptop (Linux) | `apt install docker.io docker-compose` |
| Proxmox VM | Linux VM + Docker |
| Windows Server / PC | Docker Desktop |

## Logovi

Logovi se čuvaju u Docker JSON logu (max 10 MB × 3 fajla).
Pregledaj ih sa:
```bash
docker compose logs --tail=100 -f
```
