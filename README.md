# Strand Funding Desk (CLI)

Skaner spreadow funding rate na perpetualach **OKX** i **Kraken Futures** (opcjonalnie **Bybit**).

Stawki są sprowadzane do **stawki godzinowej**, potem do APR:

`APR = hourly × 24 × 365`

APR to wartość **warunkowa** — tylko jeśli ten snapshot się utrzyma. To nie jest prognoza ani porada inwestycyjna.

## Wymagania

- Python 3.10+
- Internet (publiczne API giełd, bez kluczy API)

## Uruchomienie

```bash
python strand_scan.py
python strand_scan.py --min-apr 5 --top 15
python strand_scan.py --bybit
python strand_scan.py --x-post 1
python strand_scan.py --json
```

### Opcje

| Flaga | Opis | Domyślnie |
|---|---|---|
| `--min-apr` | Minimalny spread APR w % | 8 |
| `--min-oi` | Min. open interest USD na nogę | 50000 |
| `--min-vol` | Min. wolumen 24h USD na nogę | 100000 |
| `--bybit` | Dołącz Bybit | wyłączone |
| `--show-unstable` | Pokaż pary, które mogą odwrócić się na następną godzinę Kraken | ukryte |
| `--x-post N` | Wypisz gotowy tekst pod post na X dla pozycji N | — |
| `--json` | Wynik w JSON | — |

Opłaty taker (bps na fill) przez env:

```bash
export FEE_BPS_OKX=5
export FEE_BPS_KRAKEN=5
export FEE_BPS_BYBIT=5.5
```

Payback liczy **4 fille** (otwarcie i zamknięcie obu nóg).

## Bybit a EEA

Bybit jest **wyłączony domyślnie**. Konta EEA są przenoszone na Bybit EU, często **bez perpetuali**. Włączaj `--bybit` tylko jeśli realnie możesz otworzyć obie nogi.

## Ryzyka (skrót)

- Funding się odwraca; snapshot ≠ zysk
- Dwie osobne marże — likwidacja jednej nogi
- Baza cenowa między markami
- Slippage i fee zjadają cienkie spready

## Licencja

MIT — używasz na własne ryzyko.
