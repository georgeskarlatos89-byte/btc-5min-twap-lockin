# HLP Sentiment & Analytics

> Source: https://moondev.com/docs#hlp-analytics · captured 2026-09-23

## HLP Sentiment & Analytics

Advanced analytics endpoints for HLP sentiment analysis, liquidator monitoring, market maker tracking, and timing analysis. These endpoints provide institutional-grade signals based on HLP positioning data.

### Available Endpoints

Endpoint| Description  
---|---  
GET /api/hlp/sentiment| Net delta with z-scores and trading signals - THE sentiment indicator  
GET /api/hlp/liquidators/status| Real-time liquidator status (active/idle) with PnL  
GET /api/hlp/market-maker| Strategy B tracker for BTC/ETH/SOL positions  
GET /api/hlp/timing| Hourly and session profitability analysis  
GET /api/hlp/correlation| Delta-price correlation analysis by coin  
  
### Sentiment Analysis (The Big One)

The `/api/hlp/sentiment` endpoint analyzes HLP net delta positions and generates z-score based trading signals. This is the primary sentiment indicator for understanding institutional positioning.

    curl "https://api.moondev.com/api/hlp/sentiment?api_key=YOUR_KEY"

#### Response Example

    {
      "timestamp": "2025-01-12T15:30:00Z",
      "net_delta": {
        "BTC": { "value": -2450000, "z_score": 2.2, "signal": "Retail heavily SHORT - potential short squeeze" },
        "ETH": { "value": 1200000, "z_score": -0.8, "signal": "Neutral positioning" },
        "SOL": { "value": -580000, "z_score": 1.5, "signal": "Moderate retail SHORT bias" }
      },
      "overall_signal": "Market sentiment: Retail skewed SHORT on majors",
      "confidence": "high"
    }

### Liquidator Status

Monitor liquidator wallet activity in real-time. Track which liquidators are active/idle and their recent PnL.

    curl "https://api.moondev.com/api/hlp/liquidators/status?api_key=YOUR_KEY"

### Market Maker (Strategy B)

Track Strategy B market maker positions across BTC, ETH, and SOL. Useful for understanding MM inventory and potential mean reversion.

    curl "https://api.moondev.com/api/hlp/market-maker?api_key=YOUR_KEY"

### Timing Analysis

Analyze HLP profitability by hour of day and trading session. Identify optimal trading windows based on historical HLP performance.

    curl "https://api.moondev.com/api/hlp/timing?api_key=YOUR_KEY"

### Correlation Analysis

Analyze correlation between HLP delta changes and price movements by coin. Higher correlation suggests stronger predictive signal.

    curl "https://api.moondev.com/api/hlp/correlation?api_key=YOUR_KEY"

### Python Example

    import requests
    
    API_KEY = "your_api_key"
    BASE_URL = "https://api.moondev.com"
    
    # Get HLP sentiment signals
    sentiment = requests.get(
        f"{BASE_URL}/api/hlp/sentiment",
        params={"api_key": API_KEY}
    ).json()
    
    print(f"Overall: {sentiment['overall_signal']}")
    print(f"Confidence: {sentiment['confidence']}")
    
    for coin, data in sentiment["net_delta"].items():
        print(f"  {coin}: z={data['z_score']:.1f} - {data['signal']}")
    
    # Check liquidator activity
    liquidators = requests.get(
        f"{BASE_URL}/api/hlp/liquidators/status",
        params={"api_key": API_KEY}
    ).json()
    
    for liq in liquidators.get("liquidators", []):
        status = "ACTIVE" if liq["active"] else "IDLE"
        print(f"  {liq['address'][:10]}...: {status} (PnL: ${liq['pnl']:,.0f})")
