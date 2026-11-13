# Nasadenie na server (VPS Forpsi)

Návod je napísaný pre Windows a server s Linuxom (Ubuntu alebo Debian). Príkazy sa zadávajú do **PowerShellu** na vašom počítači alebo do terminálu na serveri, podľa toho, čo je pri kroku uvedené. Celé nasadenie trvá asi 20–30 minút.

Po dokončení:
- aplikácia beží na adrese **https://…** so šifrovaným spojením (certifikát sa vybaví a obnovuje automaticky),
- databáza a obrázky sa **každú noc o 2:30 zálohujú** do priečinka `/opt/sklad-dielov/zalohy` na serveri, pričom sa uchováva posledných 14 dní.

---

## 1. Čo budete potrebovať

- **IP adresu servera** a **heslo používateľa root**. Nájdete ich v e-maile od Forpsi alebo v ich administrácii pri VPS.
- **Adresu (doménu)**, na ktorej bude aplikácia dostupná. Máte dve možnosti:
  - **Vlastnú subdoménu**, napr. `sklad.vasafirma.sk`. V DNS nastavení domény pridajte **A záznam** `sklad` → IP servera. Prejavenie zmeny môže trvať niekoľko minút až hodín.
  - **Bez vlastnej domény** použite bezplatnú službu sslip.io: IP adresu napíšte s pomlčkami a pridajte `.sslip.io`. Napríklad pre IP `185.8.1.2` je adresa `185-8-1-2.sslip.io`. Nič netreba nastavovať.

## 2. Pripojenie na server

V PowerShelli na vašom počítači zadajte:

```powershell
ssh root@IP_SERVERA
```

Pri prvom pripojení odpovedzte `yes` a zadajte heslo. Počas písania sa nezobrazuje, to je normálne.
Keď uvidíte riadok končiaci znakom `#`, ste na serveri.

## 3. Inštalácia Dockera (na serveri)

Ak ste si pri objednávke vybrali šablónu s predinštalovaným Dockerom, stačí overiť príkaz `docker compose version` a pokračovať krokom 4.

```bash
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh
docker compose version
```

Posledný príkaz má vypísať verziu, napr. `Docker Compose version v2…`.

## 4. Firewall (na serveri)

Povolíme iba SSH (správu servera), HTTP a HTTPS:

```bash
apt -y install ufw
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

## 5. Nahratie aplikácie (na vašom počítači)

Otvorte **nové okno PowerShellu** (prvé nechajte pripojené na server) a zadajte:

```powershell
scp -r "C:\01_Nové_projekty\Aplikácie\sklad-dielov" root@IP_SERVERA:/opt/
```

## 6. Nastavenie (na serveri)

```bash
cd /opt/sklad-dielov
cp .env.example .env
openssl rand -hex 32      # vypíše náhodný reťazec – skopírujte ho (označiť myšou = kopírovať)
nano .env
```

V súbore `.env` nastavte (riadky začínajúce `#` pri `DOMAIN`, `BIND`, `BEHIND_PROXY` a `COOKIE_SECURE` odkomentujte, teda zmažte `#`):

```
DB_PASSWORD=VaseSilneHesloDatabazy2026      (iba písmená a číslice)
SECRET_KEY=<reťazec z príkazu openssl>
ADMIN_USER=admin
ADMIN_PASSWORD=<silné heslo pre prvé prihlásenie>
WORKERS=2
DOMAIN=sklad.vasafirma.sk                   (alebo 185-8-1-2.sslip.io)
BIND=127.0.0.1
BEHIND_PROXY=1
COOKIE_SECURE=1
```

Súbor uložíte klávesmi **Ctrl+O** a **Enter** a editor zavriete klávesmi **Ctrl+X**.

## 7. Spustenie (na serveri)

```bash
docker compose --profile server up -d --build
docker compose ps
```

Všetky štyri služby (`db`, `web`, `caddy`, `backup`) majú mať stav **Up**. Stav získavania certifikátu skontrolujete takto:

```bash
docker compose logs caddy --tail 30
```

Hľadajte riadok s textom `certificate obtained successfully`. Potom v prehliadači otvorte **https://VASA_DOMENA**, prihláste sa a v menu vľavo dole si hneď zmeňte heslo.

---

## Prenos dát z vášho počítača (nepovinné)

Tento krok urobte, ak ste už v aplikácii na počítači zadali skutočné projekty a diely a chcete ich mať na serveri.

**Na vašom počítači** (PowerShell v priečinku projektu):

```powershell
cd "C:\01_Nové_projekty\Aplikácie\sklad-dielov"
docker compose exec db pg_dump -U sklad -f /tmp/zaloha.sql sklad
docker compose cp db:/tmp/zaloha.sql .\zaloha.sql
docker compose cp web:/data/uploads .\uploads_zaloha
scp .\zaloha.sql root@IP_SERVERA:/opt/sklad-dielov/
scp -r .\uploads_zaloha root@IP_SERVERA:/opt/sklad-dielov/
```

Nepoužívajte presmerovanie `>` (napr. `pg_dump … > zaloha.sql`). PowerShell by súbor uložil v kódovaní, ktoré databáza nevie načítať.

**Na serveri** (nahradí prázdnu databázu na serveri vašimi dátami):

```bash
cd /opt/sklad-dielov
docker compose stop web
docker compose exec -T db psql -U sklad -d sklad -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose exec -T db psql -U sklad -d sklad < zaloha.sql
docker compose cp uploads_zaloha/. web:/data/uploads/
docker compose --profile server up -d
```

Na server sa potom prihlasujete rovnakými menami a heslami ako na počítači.

---

## Nasadenie a aktualizácie cez GitHub (odporúčané)

Namiesto kopírovania súborov cez `scp` môže server sťahovať aplikáciu priamo zo súkromného repozitára na GitHube.

**Jednorazovo – na serveri** vytvorte kľúč, ktorým sa server preukáže GitHubu (iba na čítanie):

```bash
ssh-keygen -t ed25519 -C "sklad-server" -f ~/.ssh/github_sklad -N ""
printf 'Host github.com\n  IdentityFile ~/.ssh/github_sklad\n  IdentitiesOnly yes\n' >> ~/.ssh/config
cat ~/.ssh/github_sklad.pub
```

Vypísaný riadok (začína `ssh-ed25519`) vložte na GitHube do repozitára: **Settings → Deploy keys → Add deploy key**. Políčko „Allow write access“ nechajte nezaškrtnuté.

Potom stiahnite aplikáciu (`POUZIVATEL` nahraďte svojím menom na GitHube):

```bash
git clone git@github.com:POUZIVATEL/sklad-dielov.git /opt/sklad-dielov
```

a pokračujte krokom 6 (nastavenie `.env`). Súbor `.env` sa do GitHubu nikdy nenahráva, zostáva len na serveri.

**Každá ďalšia aktualizácia:** zmeny na počítači uložte do GitHubu (commit + push) a na serveri spustite:

```bash
sh /opt/sklad-dielov/aktualizuj.sh
```

## Aktualizácia aplikácie bez GitHubu

Keď dostanete novú verziu, nahrajte **iba priečinok `app`**. Ak sa zmenil aj `docker-compose.yml` alebo `Caddyfile`, nahrajte aj ten. **Nikdy nenahrávajte súbor `.env`**, prepísali by ste nastavenia servera.

Na vašom počítači:

```powershell
scp -r "C:\01_Nové_projekty\Aplikácie\sklad-dielov\app" root@IP_SERVERA:/opt/sklad-dielov/
```

Na serveri:

```bash
cd /opt/sklad-dielov
docker compose --profile server up -d --build
```

Dáta zostanú zachované a prípadné nové stĺpce v databáze sa doplnia automaticky.

## Zálohy

- Zálohy sú na serveri v priečinku `/opt/sklad-dielov/zalohy`:
  - `databaza_DATUM.sql.gz` – databáza,
  - `obrazky_DATUM.tar.gz` – obrázky.
- Zoznam záloh zobrazíte príkazom `ls -lh /opt/sklad-dielov/zalohy`.
- Raz za čas si ich stiahnite aj na iný počítač, keby sa niečo stalo celému serveru. Na vašom počítači zadajte:

  ```powershell
  scp -r root@IP_SERVERA:/opt/sklad-dielov/zalohy "C:\Zalohy\sklad"
  ```

## Užitočné príkazy (na serveri, v priečinku /opt/sklad-dielov)

| Čo | Príkaz |
|---|---|
| Stav služieb | `docker compose ps` |
| Posledné záznamy aplikácie | `docker compose logs web --tail 50` |
| Reštart aplikácie | `docker compose restart web` |
| Zastavenie všetkého | `docker compose --profile server down` |
| Spustenie všetkého | `docker compose --profile server up -d` |

Ak niečo nefunguje, pošlite výstup príkazov `docker compose ps` a `docker compose logs --tail 50`.
