# Contract Registry

> Source: https://moondev.com/docs#contracts · captured 2026-09-23

## Contract Registry

Endpoint| Description  
---|---  
GET /api/contracts.json| Complete registry of Hyperliquid EVM smart contracts  
  
    {
      "generated_at": "2026-01-06T18:54:45Z",
      "chain": "hyperliquid_evm",
      "chain_id": 999,
      "total_contracts": 27,
      "high_value_count": 12,
      "key_contracts": {
        "system_treasury": "0x2222222222222222222222222222222222222222",
        "whype": "0x5555555555555555555555555555555555555555",
        "core_trading": "0x8549fd7ffc092f8366e416e129a622ec060104ea",
        "deposit_bridge": "0x5ed8551f90acc6395810d8274b019bf13ef1f696",
        "oracle": "0x200302c99d93ae1a024ecf9475df7d71b125efed"
      },
      "liquidation_contracts": ["0x8549...", "0x2003..."],
      "trading_alpha_contracts": ["0x8549...", "0x2180..."],
      "contracts": [
        {
          "address": "0x2222222222222222222222222222222222222222",
          "name": "Hyperliquid System Treasury",
          "type": "system",
          "description": "System treasury holding 954M+ HYPE",
          "is_high_value": true,
          "monitoring_priority": 10
        }
      ]
    }
