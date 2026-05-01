Upravené soubory pro příjemku na sklad:

- app.py
- templates/base.html
- templates/receipt_new.html
- templates/receipt_edit.html
- templates/receipt_detail.html
- templates/receipt_list.html
- static/mobile.css

Co je přidáno:
- Příjemka na sklad ve stylu výdejky
- QR scan bez refresh stránky
- Automatické sčítání stejného materiálu v příjemce
- Typ příjmu: nově pořízený materiál / vratka ze zakázky
- U vratky výběr existující zakázky
- Potvrzení příjemky přičte množství na sklad
- Zápis skladových pohybů typu příjem
- Detail příjemky a PDF příjemky
- Admin přehled příjemek s filtrem podle typu a zakázky

Nasazení:
git add .
git commit -m "Add warehouse receipt slips"
git push
