from app.core.enums import ExchangeType

EXCHANGE_METADATA = {
    ExchangeType.OKX: {
        "name": "OKX Exchange",
        "requires_passphrase": True,
        "supports_testnet": True,
    },
    ExchangeType.BITGET: {
        "name": "Bitget",
        "requires_passphrase": True,
        "supports_testnet": True,
    },
    ExchangeType.BYBIT: {
        "name": "Bybit",
        "requires_passphrase": False,
        "supports_testnet": True,
    }
}

def requires_passphrase(exchange: ExchangeType) -> bool:
    """Check if an exchange requires a passphrase."""
    return EXCHANGE_METADATA[exchange]["requires_passphrase"]

def get_exchange_info(exchange: ExchangeType) -> dict:
    """Get complete metadata for an exchange."""
    return EXCHANGE_METADATA[exchange]

def get_all_exchanges() -> list:
    """Get information about all supported exchanges."""
    return [
        {"id": ex.value, "name": meta["name"], **meta}
        for ex, meta in EXCHANGE_METADATA.items()
    ]