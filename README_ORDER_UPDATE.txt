Upravené soubory:
- app.py
- templates/orders.html
- templates/order_form.html

Změny:
- admin může založit zakázku
- admin může ukončit zakázku
- admin může smazat pouze nepoužitou zakázku
- výdejky a pohyby používají pouze aktivní zakázky
- přidána migrace sloupců status a closed_at pro existující DB
