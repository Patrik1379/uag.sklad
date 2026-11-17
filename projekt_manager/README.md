# Prehľad projektov

Webová aplikácia v Pythone (Flask + SQLite) na správu projektov používateľov.

**Čo vie**

- **Dashboard** – štatistiky (otvorené, po termíne, blížiaci sa deadline, priemerné plnenie), **časová os (Gantt)** s čiarou „dnes“ a farbami podľa stavu, tabuľka projektov, filtre podľa riešiteľa a stavu.
- **Projekty** – názov, popis, **jeden alebo viac riešiteľov**, stav (Nový / Prebieha / Pozastavený / Hotový), plnenie v %, začiatok, deadline, história zmien.
- **Úlohy** – v projekte sa dajú zadávať úlohy konkrétnym riešiteľom (termín, popis, stav Na riešenie / Rieši sa / Hotová). Každý vidí na dashboarde „Moje úlohy“ a odškrtne ich jedným klikom.
- **Podúlohy** – každá úloha môže mať zoznam podúloh, ktoré sa odškrtávajú samostatne. Keď sú splnené všetky, úloha sa sama označí ako hotová; keď sa niektorá odškrtne späť alebo pribudne nová, úloha sa znovu otvorí.
- **Komentáre k úlohám** – krátka diskusia pod každou úlohou; riešiteľ, zadávateľ a ostatní diskutujúci dostanú e-mail.
- **Plnenie z úloh a podúloh** – pri projekte sa dá zapnúť automatický výpočet % (pri nových projektoch je zapnutý). Každá úloha má rovnakú váhu: hotová úloha = 100 %, rozpracovaná podľa podielu splnených podúloh, bez podúloh 0 %. Projekt = priemer úloh.
- **Kalendár** – mesačný prehľad deadlinov projektov a termínov úloh (na mobile ako zoznam). Termíny úloh sú aj ako bodky na časovej osi dashboardu.
- **Plánovaná inštalácia** – pri projekte sa dá naplánovať inštalácia: dátum, čas od–do, miesto, technici (z riešiteľov), stav Naplánovaná / Presunutá / Hotová a poznámka. Zobrazí sa v detaile projektu, na dashboarde (zoznam + 🔧 na časovej osi a na kartách deadlinov) aj v kalendári. Technici dostanú e-mail s termínom a deň vopred pripomienku (v piatok aj na víkend a pondelok).
- **Dokumenty** – každý projekt má vlastné dokumenty s **priečinkami** (aj vnorenými, napr. Výkresy / Elektro). Súbory sa nahrávajú do otvoreného priečinka (aj viac naraz, potiahnutím myšou), dajú sa presúvať medzi priečinkami, priečinky premenovať a mazať (priečinok so súbormi maže len administrátor). Uložené sú v `data/dokumenty/<id projektu>/`.
- **Nastavenia (admin)** – v menu „Nastavenia“ sa dá bez úpravy `.env` a bez reštartu nastaviť SMTP server (rýchle voľby Gmail / Microsoft 365 / Websupport, port, zabezpečenie, meno, heslo, odosielateľ), zapnúť/vypnúť e-maily, čas denného reportu, víkendy, názov a adresu aplikácie a počet dní pre „blíži sa termín“. Tlačidlo „Poslať testovací e-mail“ ukáže presnú chybu, ak SMTP nefunguje. Uložené hodnoty majú prednosť pred `.env` (ten zostáva ako predvolený); „Obnoviť z .env“ ich vráti. Heslo sa ukladá do databázy v `data/` – chráň tento priečinok rovnako ako `.env`.
- **Prehľad bez prihlásenia (TV / vedenie)** – admin na dashboarde klikne „📺 Prehľad na TV“ a zapne tajný odkaz `/tabula/<kód>`. Kto má odkaz, vidí bez prihlásenia (len na čítanie) súhrnné čísla, karty otvorených projektov s plnením, časovú os a plánované inštalácie. Pridaním `?tv=1` sa zapne režim TV: tmavý, na celú obrazovku bez posúvania, projekty a časová os sa striedajú každých 15 s, stránka sa obnovuje každú minútu. Odkaz sa dá kedykoľvek zmeniť (starý prestane fungovať) alebo vypnúť.
- **Zabudnuté heslo** – odkaz na prihlasovacej stránke; na e-mail príde odkaz na nastavenie nového hesla (platí 1 hodinu, dá sa použiť len raz). Vyžaduje nastavený SMTP server.
- **Obrázok projektu** – malý ilustračný obrázok (bez neho ikonka výrobnej linky) v detaile, na kartách deadlinov aj v zozname projektov. Administrátor ho zmení pretiahnutím súboru na miniatúru, kliknutím na „+“ alebo Ctrl+V v detaile projektu; kliknutím sa obrázok zväčší. Pri prepojení so skladom sa obrázok zosúlaďuje oboma smermi.
- **Prepojenie so Skladom dielov** – v Nastaveniach (adresa skladu + API kľúč zo skladu). Nový alebo upravený projekt sa automaticky založí / aktualizuje v sklade; v detaile projektu je karta „Materiál v sklade“ s percentom naskladnených dielov a odkazom do skladu. Projekt má aj polia **Číslo zákazky** a **Zákazník**.
- **Veľkosť textu** – tlačidlá A− / A+ v hornej lište (aj na prihlasovacej stránke) zmenšia alebo zväčšia celé rozhranie po 5 % (70 % – 150 %); kliknutím na hodnotu sa vráti 100 %. Nastavenie sa uloží ku každému používateľovi (dá sa zmeniť aj v Profile).
- **Role** – *admin* zakladá a upravuje projekty a spravuje používateľov (pridať, deaktivovať, zmazať); *používateľ* vidí len svoje projekty, aktualizuje stav a %, pridáva poznámky a dokumenty.
- **E-maily (SMTP)**
  - pri pridelení projektu → každému novému riešiteľovi (aj so zoznamom kolegov v tíme),
  - keď niekto zmení stav → zakladateľovi projektu a ostatným riešiteľom,
  - pri pridelení úlohy → riešiteľovi úlohy (úlohy si môže pridávať aj riešiteľ projektu, sebe ich pridelí predvolene),
  - keď je úloha alebo podúloha splnená → zadávateľovi projektu a autorovi úlohy (nie tomu, kto ju splnil); pri poslednej podúlohe príde jeden e-mail „úloha splnená“,
  - **denný report** (predvolene 7:30, cez víkend nie) → používateľ dostane svoje otvorené projekty, admin prehľad všetkých. Každý si ho môže vypnúť v profile.
- Hodnotenie projektu: *Podľa plánu* (modrá; zelená je až *Hotový*), *Bez úloh*, *Termín o X d* (do 7 dní), *Ohrozený* (plnenie výrazne zaostáva za uplynutým časom), *Po termíne*.

Závislosti sú len **Flask** a **python-dotenv** (databáza je vstavané SQLite).

---

## Inštalácia

```bash
cd projekt_manager
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# uprav .env – hlavne SECRET_KEY, APP_URL a SMTP

flask --app run create-admin      # vytvorí prvého admina
python run.py                     # http://localhost:5000
```

Na vyskúšanie s ukážkovými dátami: `flask --app run demo-data` (prihlásenie `admin@demo.sk` / `demo1234`).

## Nastavenie e-mailu

V `.env` nastav `MAIL_ENABLED=true` a SMTP údaje:

| Služba | MAIL_SERVER | MAIL_PORT | Poznámka |
|---|---|---|---|
| Gmail | smtp.gmail.com | 587 | potrebné 2FA a **heslo aplikácie** (myaccount.google.com → Bezpečnosť → Heslá aplikácií) |
| Microsoft 365 | smtp.office365.com | 587 | na účte musí byť povolené „Authenticated SMTP“ |
| Firemný server | podľa IT | 587 / 465 | pri 465 nastav `MAIL_USE_SSL=true`, `MAIL_USE_TLS=false` |

Overenie: `flask --app run test-mail niekto@firma.sk`

## Denný report

Plánovač beží priamo v aplikácii a o `DAILY_REPORT_TIME` odošle report. Deň odoslania si zapisuje do databázy, takže aj pri reštarte alebo viacerých procesoch servera odíde **len raz denne**. Ak bol server v čase reportu vypnutý, report sa pošle hneď po štarte (v ten istý deň).

Alternatíva – vypni `SCHEDULER_ENABLED=false` a spúšťaj externe:

- Linux cron: `30 7 * * 1-5 cd /srv/projekt_manager && .venv/bin/flask --app run send-daily-report`
- Windows Plánovač úloh: `C:\cesta\.venv\Scripts\flask.exe --app run send-daily-report` (pracovný priečinok = priečinok aplikácie)

Admin môže report kedykoľvek poslať aj tlačidlom na dashboarde.

## Spustenie v Dockeri

Potrebuješ Docker s Compose (Docker Desktop na Windows/Mac, na Linuxe `docker` + plugin `docker compose`).

```bash
cd projekt_manager
cp .env.example .env            # Windows: copy .env.example .env
# uprav .env: SECRET_KEY, APP_URL (napr. http://server:8000), SMTP

docker compose up -d --build    # zostaví obraz a spustí aplikáciu na pozadí
docker compose exec -u app app flask --app run create-admin
```

Aplikácia beží na `http://localhost:8000` (port zmeníš v `.env` cez `APP_PORT`).

- **Dáta** (databáza + dokumenty) sú v priečinku `./data` vedľa `docker-compose.yml` – zálohuj ho.
- **Denný report** beží v kontajneri automaticky, čas je v pásme Europe/Bratislava.
- Kontajner sa po reštarte servera spustí sám (`restart: unless-stopped`).
- Príkazy aplikácie spúšťaj vždy s `-u app`, aby súbory v `data/` patrili správnemu používateľovi:

| Čo | Príkaz |
|---|---|
| logy | `docker compose logs -f` |
| test e-mailu | `docker compose exec -u app app flask --app run test-mail niekto@firma.sk` |
| pripomienky inštalácií hneď | `docker compose exec -u app app flask --app run send-install-reminders` |
| denný report hneď | `docker compose exec -u app app flask --app run send-daily-report` |
| demo dáta | `docker compose exec -u app app flask --app run demo-data` |
| zastavenie | `docker compose down` |
| aktualizácia | prepíš súbory aplikácie (okrem `data/` a `.env`) a `docker compose up -d --build` |

Pre HTTPS daj pred kontajner reverzný proxy (nginx, Caddy, Traefik), nastav `SESSION_COOKIE_SECURE=true` a `APP_URL=https://...`.

## Nasadenie na server bez Dockeru

**Linux**

```bash
gunicorn -w 2 -b 0.0.0.0:8000 wsgi:app
```

Pred to daj nginx/Caddy s HTTPS a nastav `SESSION_COOKIE_SECURE=true`, `APP_URL=https://projekty.firma.sk`.
Pri nginx zvýš `client_max_body_size` (napr. `50M`) kvôli nahrávaniu dokumentov.

**Windows server**

```bash
waitress-serve --listen=0.0.0.0:8000 wsgi:app
```

(ako službu ho môžeš zaregistrovať napr. cez NSSM).

## Aktualizácia zo staršej verzie

Stačí prepísať súbory aplikácie (priečinok `data/` nechaj). Pri štarte sa databáza automaticky prevedie – pôvodný riešiteľ každého projektu sa stane jeho prvým riešiteľom.

## Záloha

Všetko je v priečinku `data/` – databáza `projekty.db` a priečinok `dokumenty/`. Stačí ho pravidelne zálohovať.

## Štruktúra

```
Dockerfile, docker-compose.yml, docker-entrypoint.sh   beh v Dockeri
config.py            nastavenia (.env)
run.py / wsgi.py     spustenie (vývoj / produkcia)
app/
  __init__.py        vytvorenie aplikácie
  db.py              SQLite schéma a prístup
  models.py          projekty, stavy, výpočet časovej osi
  auth.py            prihlásenie, role, CSRF ochrana
  views.py           stránky (dashboard, projekty, dokumenty, používatelia)
  mailer.py          SMTP a notifikácie
  scheduler.py       denný report
  cli.py             príkazy create-admin, demo-data, test-mail, send-daily-report
  templates/         HTML šablóny (+ email/)
  static/style.css
```

## Nápady na rozšírenie

- hlavný zodpovedný riešiteľ, prílohy ku komentárom, export kalendára do Outlooku (iCal)
- pripomienka X dní pred deadlinom, týždenný report pre vedenie
- export do Excelu, komentáre s @zmienkou
- prihlásenie cez Microsoft / Google účet (SSO)
- PostgreSQL namiesto SQLite pri väčšom počte používateľov
