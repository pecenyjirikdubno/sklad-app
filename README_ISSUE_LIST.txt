Upravené soubory:
- app.py: přidána route /issues pro admin přehled výdejek s filtrem podle zakázky
- templates/issue_list.html: nová šablona přehledu výdejek

Doporučená úprava menu v templates/base.html:
do admin části menu přidej:
<a href="{{ url_for('issue_slip_list') }}">Výdejky</a>
