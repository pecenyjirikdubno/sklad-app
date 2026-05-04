Inventura - přidaná funkcionalita

Přidáno:
- nové DB modely InventorySession a InventoryItem
- admin přehled Inventury
- admin spuštění inventury pro vybraného usera
- user vidí tlačítko Inventura pouze pokud má aktivní inventuru
- user skenuje QR / ID materiálu a zadává reálný stav
- dokončení inventury uživatelem
- admin detail inventury
- export inventury do XLSX
- PDF inventury s Unicode fontem pro českou interpunkci
- zvýraznění rozdílů mezi systémovým a reálným stavem

Nasazení:
git add .
git commit -m "Add inventory module"
git push
