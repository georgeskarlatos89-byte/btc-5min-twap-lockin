# Quick Start

> Source: https://moondev.com/docs#quick-start · captured 2026-09-23

Quick Startcopy & run

Copy-paste this to test your API key instantly:

    curl "https://api.moondev.com/api/ticks/btc.json?api_key=YOUR_API_KEY"

Or in Python:

    import requests
    
    data = requests.get(
        "https://api.moondev.com/api/ticks/btc.json",
        headers={"X-API-Key": "YOUR_API_KEY"}
    ).json()
    
    print(f"BTC Price: ${data['latest_price']:,.2f}")

[Need an API key?](<https://moondev.com/t/docs>)
