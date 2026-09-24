# Code Examples

> Source: https://moondev.com/docs#examples · captured 2026-09-23

## Code Examples

More examples on GitHub

Every new API endpoint gets a working example added to the repo. Clone it and start building!

[View Repo](<https://github.com/moondevonyt/Hyperliquid-Data-Layer-API>)

Replace `your_api_key` with your actual key.[Need an API key?](<https://moondev.com/t/docs>)

### Python - Get Smart Money Signals

    import requests
    
    API_KEY = "your_api_key"
    BASE_URL = "https://api.moondev.com"
    
    # Get smart money signals from last hour
    signals = requests.get(
        f"{BASE_URL}/api/smart_money/signals_1h.json",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    for signal in signals["signals"][:10]:
        print(f"{signal['action']} {signal['coin']} @ ${signal['price']:,.0f}")

### Python - Monitor Order Flow (130 Symbols)

    import requests
    
    API_KEY = "your_api_key"
    
    imbalance = requests.get(
        "https://api.moondev.com/api/imbalance/1h.json",
        headers={"X-API-Key": API_KEY}
    ).json()
    
    # Overall market pressure
    overall = imbalance["overall"]
    print(f"Market: {overall['dominant_side']} pressure (ratio: {overall['imbalance_ratio']:.4f})")
    
    # Top coins by sell pressure
    for coin, data in sorted(imbalance["by_coin"].items(), key=lambda x: x[1]["imbalance_ratio"]):
        print(f"{coin}: {data['dominant_side']} ({data['imbalance_ratio']:.4f})")

### JavaScript - Real-time Ticks

    const API_KEY = "your_api_key";
    
    fetch("https://api.moondev.com/api/ticks/latest.json", {
      headers: { "X-API-Key": API_KEY }
    })
      .then(res => res.json())
      .then(data => {
        console.log("Current prices:", data);
      });

### cURL

    # Get smart money rankings
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/smart_money/rankings.json
    
    # Get BTC ticks for last hour
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/ticks/btc_1h.json
    
    # Get large trades
    curl -H "X-API-Key: YOUR_KEY" https://api.moondev.com/api/large_trades.json

Want more? Check out the full collection of examples on [GitHub](<https://github.com/moondevonyt/Hyperliquid-Data-Layer-API>).
