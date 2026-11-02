# Warframe Market Prices

A small Windows app that shows live sell prices from [warframe.market](https://warframe.market)
for prime parts, mods, arcanes and relics, with item pictures.

*Unofficial fan project. Not affiliated with or endorsed by Digital Extremes or warframe.market.
Warframe is a trademark of Digital Extremes.*

## How to download

1. Open the **[Releases page](../../releases/latest)** (or click **Releases** on the right side of this page).
2. Under **Assets**, click **Warframe Market Prices Setup.exe** to download it.
3. Run it and follow the installer. Done.

No Python and nothing else needed. It installs for your user only (no admin prompt), adds a Start menu
entry and shows up in Settings > Apps. Windows 64-bit only.

> **Windows SmartScreen** may say "Windows protected your PC" because the installer is not code-signed.
> Click **More info > Run anyway**.

## Features

- Listings, Min, Median and Max sell price per item (extreme troll listings are ignored for Max)
- Ducat values for prime parts
- Two suggested prices:
  - **Sell Fast**: 1 platinum under the cheapest realistic listing
  - **Best Price (Price Clusters)**: median of the busiest price cluster among the sell listings
- **Sellers** filter: all sellers, online / in-game only, or offline only
- Rank filter for mods and arcanes, Regular / Atragraph versions of mods
- Typo-tolerant search, platform choice (PC, Xbox, PS4, Switch)
- Auto-update on a timer, CSV export, dark and light theme
- Remembers your last session

## Uninstall

Settings > Apps > Installed apps > Warframe Market Prices > Uninstall.

## License

[MIT](LICENSE)
