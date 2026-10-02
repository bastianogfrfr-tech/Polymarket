# polybot – Polymarket Wetter-Bot

Handelt automatisch die Märkte „Highest temperature in <Stadt> on <Datum>“ auf Polymarket.

**Strategie:** Erst spät am Tag (Ortszeit der Wetterstation) ist der Tageshöchstwert schon
fast vollständig gemessen. Der Bot holt sich die echten Messwerte der Station (METAR,
dieselbe Station, die Polymarket für die Auflösung nimmt) und dazu die Wettermodelle
(Open-Meteo, Ersatz: MET Norway). Daraus berechnet er für jeden Temperatur-Bucket eine
Wahrscheinlichkeit. Er kauft nur, wenn diese nach Gebühren deutlich über dem Marktpreis liegt.

Ehrlich gesagt: Meistens ist der Markt schon richtig bepreist, und der Bot meldet dann
`NO TRADE`. Das ist gewollt.

## Sicherheit zuerst
- Eine **neue Wallet**, auf der nur das Geld liegt, das du komplett verlieren kannst.
- Den Private Key nur in die lokale `.env` schreiben. Nie in einen Chat, nie in Git.
- Der Bot startet **immer im Testmodus** (`DRY_RUN=true`), solange du das nicht selbst änderst.
- Harte Limits: `MAX_STAKE` pro Trade, `STOP_BALANCE`, `MAX_TRADES_PER_DAY`, nie zweimal
  dasselbe Event.
- Kein VPN, um die Polymarket-Ländersperre zu umgehen. Vorher prüfen:
  https://polymarket.com/api/geoblock muss `"blocked":false` zeigen.

## Setup auf dem MiniPC
```bash
git clone https://github.com/bastianogfrfr-tech/Polymarket.git
cd Polymarket
git checkout claude/wizardly-pascal-pnzkau
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # dann .env bearbeiten
```

## Starten
```bash
python -m polybot.main             # ein Scan
python -m polybot.main --loop 600  # alle 10 Minuten, läuft dauerhaft
```

Dauerhaft im Hintergrund per Cron (alle 10 Minuten):
```
*/10 * * * * cd /pfad/zu/Polymarket && .venv/bin/python -m polybot.main >> bot.log 2>&1
```

Jeder (Test-)Trade landet in `trades.jsonl`.

## Echtgeld einschalten
1. Ein paar Tage im Testmodus laufen lassen und `trades.jsonl` anschauen.
2. Dann in `.env` `PRIVATE_KEY`, `FUNDER_ADDRESS` und `SIGNATURE_TYPE` eintragen und
   `DRY_RUN=false` setzen.

Hinweis: Polymarket hat eine Mindestgröße von meist 5 Shares pro Order. Bei einem Preis
von 0,50 sind das 2,50 USDC, also mehr als `MAX_STAKE=2`. Solche Trades überspringt der
Bot. Wer das will, muss `MAX_STAKE` erhöhen.
