# Sklad dielov

Webová aplikácia na evidenciu dielov pre plánované projekty (Python / Flask + PostgreSQL, Docker Compose).

## Funkcie

- **Projekty** – názov, číslo zakázky, zákazník, termín, kusovník (diely + potrebné množstvo) a malý obrázok projektu (bez obrázka sa zobrazí ikonka výrobnej linky).
- **Obrázky ťahaním** – obrázok projektu alebo dielu stačí pretiahnuť myšou priamo na miniatúru, prípadne na ňu kliknúť alebo na detaile vložiť zo schránky (Ctrl+V).
- **Skladové karty** – unikátne označenie (alebo automaticky `D-00001`), objednávacie číslo, názov, výrobca, dodávateľ, kategória, umiestnenie, technické parametre a obrázok. V zoznamoch sa obrázok zobrazuje ako malá ikona a po kliknutí sa zväčší.
- **Lokácie a regály** – umiestnenie dielu má tri úrovne: lokácia (napr. Hlavný sklad, Dielňa) › regál (R1, R2…, s vlastným názvom alebo priradeným projektom) › polica (výber podľa počtu políc regálu). Navyše sa dá zadať vlastné pomenovanie miesta (napr. „skriňa pri lise“). Umiestnenie sa zobrazuje v kusovníku projektu, pri príjme aj v exporte do Excelu.
- **Príjem tovaru** – technik zadá alebo naskenuje objednávacie číslo. Aplikácia ukáže, do ktorých projektov je diel naplánovaný a koľko kusov tam ešte chýba, napr. *Projekt 1 – 2 ks, Projekt 2 – 1 ks*. Prijaté kusy sa rozdelia automaticky podľa termínu projektu a dajú sa upraviť ručne. Prebytok ide do **voľného skladu**.
- **Hľadanie** – ak sa objednávacie číslo nenájde presne, aplikácia prehľadá všetky údaje karty (názov, výrobca, dodávateľ, kategória, umiestnenie, parametre, poznámka). Nerozlišuje diakritiku ani pomlčky a medzery v číslach a pri viacerých slovách hľadá každé zvlášť.
- **Voľný sklad** – prehľad nepriradených kusov a projektov, v ktorých tieto diely chýbajú. Priradenie jedným klikom.
- **Dashboard** – celková kompletnosť, naskladnené a chýbajúce kusy, priebeh po projektoch, najviac chýbajúce diely a posledné príjmy.
- **Detail projektu** – naskladnené položky sú zelené, čiastočne naskladnené žlté a chýbajúce červené. Obsahuje filter, priradenie z voľného skladu a export do Excelu.
- **Import kusovníka** z Excelu alebo CSV (šablóna sa dá stiahnuť priamo v aplikácii).
- **Prihlásenie a role:**
  - *Administrátor* – projekty, kusovníky, používatelia, storno príjmov.
  - *Technik* – príjem tovaru a skladové karty.

## Spustenie

Potrebujete Docker Desktop (Windows) alebo Docker Engine (Linux).

```bash
cp .env.example .env        # na Windows: copy .env.example .env
# v .env nastavte heslá a SECRET_KEY
docker compose up -d --build
```

Aplikácia beží na **http://localhost:8000**. Pri prvom spustení sa vytvorí účet `admin` s heslom `admin`, prípadne s hodnotami z `.env`. **Heslo si hneď zmeňte.**

Z iných počítačov v sieti sa pripojíte cez `http://<IP-servera>:8000`.

### Ukážkové dáta (nepovinné)

```bash
docker compose exec web python seed_demo.py
```

Pridá 3 projekty, 14 dielov a účet `technik` / `technik`. Ukážkové dáta sa vložia iba do prázdnej databázy.

## Nasadenie na server na internete

Postup krok za krokom (VPS, HTTPS, automatické zálohy) je v súbore **[NASADENIE.md](NASADENIE.md)**.

## Zálohovanie

Dáta sú v dvoch Docker volumes: `db_data` (databáza) a `uploads` (obrázky).

```bash
# záloha databázy (funguje aj vo Windows PowerShell)
docker compose exec db pg_dump -U sklad -f /tmp/zaloha.sql sklad
docker compose cp db:/tmp/zaloha.sql ./zaloha.sql
# obnova
docker compose exec -T db psql -U sklad sklad < zaloha.sql
# záloha obrázkov
docker compose cp web:/data/uploads ./zaloha_obrazkov
```

## Aktualizácia

```bash
docker compose up -d --build
```

Dáta zostanú zachované. Tabuľky sa vytvárajú automaticky pri štarte.

## Vývoj bez Dockeru

Bez nastavenej `DATABASE_URL` sa použije SQLite (`data/sklad.db`).

```bash
pip install -r requirements.txt
python wsgi.py
```

## Štruktúra

```
app/
  __init__.py      – vytvorenie aplikácie, CSRF ochrana, filtre
  db.py            – databázová vrstva (PostgreSQL / SQLite), schéma
  auth.py          – prihlásenie, používatelia, role
  routes.py        – dashboard, príjem, projekty, skladové karty, import/export
  services.py      – výpočty stavu, obrázky, import kusovníka
  templates/       – HTML šablóny
  static/          – CSS a JS (bez externých závislostí, funguje aj offline)
docker-compose.yml, Dockerfile, entrypoint.sh, seed_demo.py
```
