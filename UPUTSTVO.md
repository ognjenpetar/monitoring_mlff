# MLFF Monitoring – Uputstvo za pokretanje

## Pokretanje

Dvaput klikni na `run.bat` ili pokreni:
```
python app.py
```

---

## Prvo podešavanje (jednom)

### 1. Email notifikacije

Otvori **Podešavanja** u aplikaciji i popuni:

| Polje | Vrednost |
|---|---|
| SMTP server | `smtp.gmail.com` |
| SMTP port | `587` |
| Email pošiljalac | tvoja Gmail adresa npr. `monitoring.mlff@gmail.com` |
| Lozinka | **App Password** (ne obična lozinka) |
| Email primalac | `ognjen.petar.todorovic@oriontelekom.rs` |

**Kako napraviti Gmail App Password:**
1. Idi na [myaccount.google.com/security](https://myaccount.google.com/security)
2. Uključi 2-Step Verification
3. Traži "App passwords" → odaberi "Mail" + "Windows Computer"
4. Google generiše 16-karakternu lozinku – upiši je u polje Lozinka

---

### 2. WhatsApp notifikacije (CallMeBot – besplatno)

**Kako dobiti API ključ:**
1. Na telefonu dodaj kontakt: **+34 644 65 21 91** (ime: CallMeBot)
2. Pošalji mu WhatsApp poruku tačno ovako:
   ```
   I allow callmebot to send me messages
   ```
3. Za par minuta dobiješ odgovor sa API ključem (npr. `123456`)
4. Upiši taj ključ u aplikaciji u polje **CallMeBot API ključ**

---

## Isključeni uređaji

Sledeći uređaji su uvek DOWN jer nisu montirani i **neće aktivirati alarm**:
- `SCPA1046-L-UPS`
- `SCPA1046-L-IOL`

Prikazani su u tabeli sivom bojom.

---

## Zavisnosti

```
pip install requests beautifulsoup4
```
(automatski se instaliraju pri prvom pokretanju `run.bat`)
