Opravený projekt Sklad3

Kontrola a opravy:
- opraven templates/base.html: kompletní HTML struktura, uzavřené Jinja bloky, správný <head>/<body>
- doplněny PWA ikony icon-192.png a icon-512.png + favicon.ico
- opraven manifest.webmanifest, aby neodkazoval na chybějící ikony
- zkontrolována syntaxe app.py
- zkontrolována Jinja syntaxe všech šablon
- balíček neobsahuje .git, __pycache__ ani virtuální prostředí

Nasazení:
1) Rozbal obsah ZIPu do C:\Sklad3 a přepiš soubory.
2) Spusť:
   git add .
   git commit -m "Fix project templates and PWA"
   git push
