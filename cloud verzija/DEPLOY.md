# MLFF Monitor – Cloud verzija

Kompletno, korak-po-korak uputstvo za postavljanje ovog servisa (kreiranje VM-a,
rezervacija javnog IP-a, instalacija Docker-a, deploy, predaja IP-a mrežnom
administratoru na whitelisting) nalazi se u:

1. [`../ORACLE_CLOUD_SETUP.md`](../ORACLE_CLOUD_SETUP.md) — kreiranje besplatne VM na Oracle Cloud
2. [`../DEPLOY.md`](../DEPLOY.md) — sve od trenutka kad je VM spremna do pokrenutog servisa

## Brzi podsetnik komandi (kad je već sve podešeno)

```bash
# Update aplikacije
git pull
docker compose down
docker compose up -d --build

# Status / logovi
docker compose ps
docker compose logs --tail=100 -f

# Restart / stop
docker compose restart
docker compose down
```
