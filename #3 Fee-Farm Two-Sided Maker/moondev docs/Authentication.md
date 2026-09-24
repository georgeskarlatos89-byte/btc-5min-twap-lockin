# Authentication

> Source: https://moondev.com/docs#auth · captured 2026-09-23

## Authentication

All `/api/*` endpoints require authentication via API key.[Need an API key?](<https://moondev.com/t/docs>)

### Method 1: Query Parameter

    ?api_key=YOUR_API_KEY

### Method 2: Header (Recommended)

    X-API-Key: YOUR_API_KEY

### Error Response (401)

    {
      "error": "Unauthorized",
      "message": "Valid API key required. Use ?api_key=YOUR_KEY or X-API-Key header"
    }
