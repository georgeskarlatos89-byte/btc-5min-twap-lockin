# Moon Dev AI

> Source: https://moondev.com/docs#moondev-ai · captured 2026-09-23

🤖

## Moon Dev AI

NEW!

Proprietary Trading AI - 5 Years of Quant Knowledge

Black box. Proprietary model trained on 5 years of quantitative trading research, backtesting strategies, and real trading experience. If you're an algorithmic trader using any other AI, you're cooked.

🔐 Black Box

Proprietary model. No details. Just results.

📈 5 Years of Alpha

RBI system, backtesting, bot building, real trading edge.

⚡ Built for Traders

Not a generic chatbot. Built by a trader, for traders.

### 🔗 Endpoints

Endpoint| Auth| Description  
---|---|---  
GET /api/ai/health| No| Health check  
POST /api/ai/v1/chat/completions| Yes| Chat completions  
POST /api/ai/chat| Yes| Simple chat  
  
### 🎯 Quick Test

Verify the AI is live:

    curl https://api.moondev.com/api/ai/health

🔑 Key Details (Read This First!)

  * Base URL: `https://api.moondev.com/api/ai/v1`
  * Model: `moondev-ai` (always use this exact model name)
  * Auth Header: `Authorization: Bearer YOUR_API_KEY`

### 💻 cURL

    curl -X POST "https://api.moondev.com/api/ai/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer YOUR_API_KEY" \
      -d '{
        "model": "moondev-ai",
        "messages": [{"role": "user", "content": "How do I build a trading bot?"}],
        "max_tokens": 500
      }'

### 🐍 Python (requests)

    import requests
    
    response = requests.post(
        "https://api.moondev.com/api/ai/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer YOUR_API_KEY"
        },
        json={
            "model": "moondev-ai",
            "messages": [{"role": "user", "content": "What is the RBI system?"}],
            "max_tokens": 500
        }
    )
    
    print(response.json()["choices"][0]["message"]["content"])

### 🐍 Python (OpenAI SDK)

If you already use the OpenAI SDK, just change the base_url and api_key:

    from openai import OpenAI
    
    client = OpenAI(
        api_key="YOUR_API_KEY",
        base_url="https://api.moondev.com/api/ai/v1"
    )
    
    response = client.chat.completions.create(
        model="moondev-ai",
        messages=[{"role": "user", "content": "What is the RBI system?"}],
        max_tokens=500
    )
    
    print(response.choices[0].message.content)

### 📤 Response

    {
      "id": "gen-xxx",
      "choices": [
        {
          "message": {
            "role": "assistant",
            "content": "..."
          },
          "finish_reason": "stop"
        }
      ],
      "usage": {
        "prompt_tokens": 1289,
        "completion_tokens": 500,
        "total_tokens": 1789
      }
    }

⚙ Specs

  * Context Length: 200,000 tokens
  * Model Name: `moondev-ai` (required in request body)
  * Auth: `Authorization: Bearer YOUR_API_KEY` header

⚠ This Is War

If you're trading with ChatGPT or any generic AI, you're bringing a butter knife to a sword fight. Moon Dev AI was built from the trenches - 5 years of live trading, thousands of backtests, hundreds of bots. No other AI has this edge.
