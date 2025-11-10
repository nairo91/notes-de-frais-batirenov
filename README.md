# App Notes de Frais BATI RENOV

Petite application Flask pour saisir des notes de frais, avec :

- Login via `users.csv`
- Upload photo de ticket / facture
- Champs obligatoires : Montant, Date, Libellé, Chantier
- Tableau récapitulatif triable côté client
- Envoi automatique d'un CSV récap à `compta@batirenov.info` (via cron Render) le 20 de chaque mois (mois précédent).

## Lancement en local

```bash
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt

export SECRET_KEY=dev
export DATABASE_URL=postgres://user:password@localhost:5432/notes_frais

python app.py
```

## Commande cron

```bash
python app.py send_report_cron
```

