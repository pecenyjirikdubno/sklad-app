# Sklad materiálu

Webová aplikace ve Flasku pro evidenci materiálu, import/export Excelu, role uživatelů, skladové karty, pohyby a QR kódy.

## Spuštění ve Windows

```bat
cd C:\Sklad\sklad_app
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Otevřete v prohlížeči:

```text
http://127.0.0.1:5000
```

Z telefonu ve stejné Wi‑Fi síti použijte adresu z konzole, například:

```text
http://10.10.10.106:5000
```

## Výchozí uživatelé

- Administrátor: `admin` / `admin123`
- Běžný uživatel: `user` / `user123`

Administrátor má import/export Excelu. Běžný uživatel import/export nevidí a nemá k němu přístup.

## Skladové karty a QR

U každé položky je tlačítko **Karta**. Karta zobrazuje:

- aktuální stav,
- QR kód,
- historii příjmů a výdejů,
- zakázku u výdeje,
- uživatele, který pohyb provedl.

QR kód otevře stránku pro příjem/výdej dané položky. Po načtení mobilem je uživatel vyzván k přihlášení, pokud ještě není přihlášen.

## Poznámka k heslům

Výchozí hesla jsou určena pouze pro první spuštění a testování. Pro ostré použití je vhodné doplnit správu uživatelů nebo hesla změnit přímo v databázi.
