# Warframe Market Prices

A small Windows desktop app that shows live sell prices from [warframe.market](https://warframe.market)
for prime parts, mods, arcanes and relics, with item pictures.

*Unofficial fan project. Not affiliated with or endorsed by Digital Extremes or warframe.market.
Warframe is a trademark of Digital Extremes.*

## Download

Get **Warframe Market Prices Setup.exe** from the [Releases](../../releases/latest) page and run it.
No Python needed. It installs for your user only (no admin prompt), adds a Start menu entry and shows up in
Settings > Apps. Windows 64-bit only.

> **Windows SmartScreen** may say "Windows protected your PC" because the installer is not code-signed.
> Click **More info > Run anyway**. Some antivirus programs also flag PyInstaller apps by mistake;
> the full source is in this repository if you want to check or build it yourself.

## Features

- Listings, Min, Median and Max sell price per item (extreme troll listings are ignored for Max)
- Ducat values for prime parts
- Two suggested prices:
  - **Sell Fast**: 1 platinum under the cheapest realistic listing (lowballs ignored, never above Best Price)
  - **Best Price (Price Clusters)**: median of the busiest price cluster among the sell listings
- **Sellers** filter: all sellers, online / in-game only, or offline only (applies to every column)
- Rank filter for mods and arcanes, Regular / Atragraph versions of mods
- Typo-tolerant search, platform choice (PC, Xbox, PS4, Switch)
- Auto-update on a timer, CSV export, dark and light theme
- Remembers your last session

## Run from source

```
pip install -r requirements.txt
pip install curl_cffi        # optional: helps download item pictures
python wfm_app.py
```

`wfm_prices.py` also works on its own as a command-line tool:

```
python wfm_prices.py -c mods --search serration
python wfm_prices.py -c prime --limit 25 --csv prime.csv
python wfm_prices.py -c relics --online-only --platform pc
```

## Build the installer yourself

1. Install Python 3.10+ (64-bit) and [Inno Setup 6 or 7](https://jrsoftware.org/isdl.php).
2. Double-click `build_exe.bat`.
3. The installer appears in `installer\`.

## Releasing a new version (maintainers)

1. Change the version in `installer.iss` (`MyAppVersion`) and in `wfm_version.txt` (both places).
2. Commit, then tag and push:
   ```
   git tag v1.3.1
   git push origin v1.3.1
   ```
3. The GitHub Action in `.github/workflows/release.yml` builds the installer on a Windows runner and
   attaches it to a new GitHub Release.

## License

[MIT](LICENSE)
