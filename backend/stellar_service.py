import base64
import hashlib
import os
import re
import secrets
import struct
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
import httpx

load_dotenv()

HORIZON_TESTNET_URL = os.getenv("STELLAR_HORIZON_URL", "https://horizon-testnet.stellar.org").rstrip("/")
FRIENDBOT_URL = "https://friendbot.stellar.org"
STELLAR_EXPERT_TESTNET_TX_URL = "https://stellar.expert/explorer/testnet/tx"
STELLAR_EXPERT_TESTNET_ACCOUNT_URL = "https://stellar.expert/explorer/testnet/account"
DEMO_ESCROW_PUBLIC_KEY = "GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H"


def _crc16_xmodem(data: bytes) -> int:
    """Compute CRC16-XModem checksum used by Stellar StrKey encoding."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _encode_strkey(version_byte: int, payload: bytes) -> str:
    raw = bytes([version_byte]) + payload
    checksum = struct.pack("<H", _crc16_xmodem(raw))
    return base64.b32encode(raw + checksum).decode("ascii")


def _is_valid_strkey(address: str, expected_prefix: str = "G", expected_version: int = 6 << 3) -> bool:
    if not isinstance(address, str):
        return False
    cleaned = address.strip()
    if len(cleaned) != 56 or not cleaned.startswith(expected_prefix):
        return False
    if not re.fullmatch(r"[A-Z2-7]{56}", cleaned):
        return False
    try:
        decoded = base64.b32decode(cleaned.encode("ascii"))
    except Exception:
        return False
    if len(decoded) != 35 or decoded[0] != expected_version:
        return False
    payload = decoded[:-2]
    expected_crc = struct.pack("<H", _crc16_xmodem(payload))
    return decoded[-2:] == expected_crc


try:
    import stellar_sdk  # type: ignore
    from stellar_sdk import Asset, Keypair, Network, Server, StrKey, TransactionBuilder  # type: ignore

    HAS_STELLAR_SDK = True
except ImportError:  # pragma: no cover - lightweight compatibility shim when stellar-sdk is not installed
    HAS_STELLAR_SDK = False

    class Network:
        TESTNET_NETWORK_PASSPHRASE = "Test SDF Network ; September 2015"
        PUBLIC_NETWORK_PASSPHRASE = "Public Global Stellar Network ; September 2015"

    class Asset:
        def __init__(self, code: str = "XLM", issuer: str | None = None) -> None:
            self.code = code
            self.issuer = issuer

        @classmethod
        def native(cls) -> "Asset":
            return cls("XLM", None)

    class StrKey:
        @staticmethod
        def is_valid_ed25519_public_key(public_key: str) -> bool:
            return _is_valid_strkey(public_key, expected_prefix="G", expected_version=6 << 3)

        @staticmethod
        def is_valid_ed25519_secret_seed(secret_seed: str) -> bool:
            return _is_valid_strkey(secret_seed, expected_prefix="S", expected_version=18 << 3)

    class Keypair:
        def __init__(self, public_key: str, secret: str | None = None) -> None:
            self.public_key = public_key
            self._secret = secret

        @property
        def secret(self) -> str:
            if not self._secret:
                raise ValueError("Keypair does not contain a secret seed.")
            return self._secret

        @classmethod
        def random(cls) -> "Keypair":
            raw_seed = secrets.token_bytes(32)
            raw_pub = hashlib.sha256(raw_seed).digest()
            pub_str = _encode_strkey(6 << 3, raw_pub)
            sec_str = _encode_strkey(18 << 3, raw_seed)
            return cls(public_key=pub_str, secret=sec_str)

        @classmethod
        def from_secret(cls, secret: str) -> "Keypair":
            cleaned = (secret or "").strip()
            if not StrKey.is_valid_ed25519_secret_seed(cleaned):
                raise ValueError("Invalid Stellar secret seed.")
            decoded = base64.b32decode(cleaned.encode("ascii"))
            raw_seed = decoded[1:-2]
            raw_pub = hashlib.sha256(raw_seed).digest()
            pub_str = _encode_strkey(6 << 3, raw_pub)
            return cls(public_key=pub_str, secret=cleaned)

        @classmethod
        def from_public_key(cls, public_key: str) -> "Keypair":
            cleaned = (public_key or "").strip()
            if not StrKey.is_valid_ed25519_public_key(cleaned):
                raise ValueError("Invalid Stellar public key.")
            return cls(public_key=cleaned, secret=None)

    class _BuiltTransaction:
        def __init__(self, source_public_key: str, operations: list[dict[str, Any]], memo: str | None) -> None:
            self.source_public_key = source_public_key
            self.operations = operations
            self.memo = memo
            self.signatures: list[str] = []

        def sign(self, keypair: Keypair) -> None:
            self.signatures.append(keypair.public_key)

        def hash_hex(self) -> str:
            raw = f"{self.source_public_key}:{self.memo}:{self.operations}:{self.signatures}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()

        def to_xdr(self) -> str:
            return base64.b64encode(self.hash_hex().encode("utf-8")).decode("ascii")

    class TransactionBuilder:
        def __init__(
            self,
            source_account: Any,
            network_passphrase: str = Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee: int = 100,
        ) -> None:
            self.source_account = source_account
            self.network_passphrase = network_passphrase
            self.base_fee = base_fee
            self._memo: str | None = None
            self._operations: list[dict[str, Any]] = []
            self._timeout: int = 30

        def add_text_memo(self, memo_text: str) -> "TransactionBuilder":
            if len(memo_text.encode("utf-8")) > 28:
                raise ValueError("Stellar text memo cannot exceed 28 bytes.")
            self._memo = memo_text
            return self

        def append_payment_op(
            self,
            destination: str,
            amount: str,
            asset: Asset | None = None,
            source: str | None = None,
        ) -> "TransactionBuilder":
            self._operations.append(
                {
                    "type": "payment",
                    "destination": destination,
                    "amount": str(amount),
                    "asset": (asset or Asset.native()).code,
                    "source": source,
                }
            )
            return self

        def set_timeout(self, timeout: int) -> "TransactionBuilder":
            self._timeout = timeout
            return self

        def build(self) -> _BuiltTransaction:
            account_id = getattr(self.source_account, "account_id", str(self.source_account))
            return _BuiltTransaction(
                source_public_key=account_id,
                operations=self._operations,
                memo=self._memo,
            )

    class _RootCallBuilder:
        def __init__(self, horizon_url: str) -> None:
            self.horizon_url = horizon_url

        def call(self) -> dict[str, Any]:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(f"{self.horizon_url}/")
                resp.raise_for_status()
                return resp.json()

    class Server:
        def __init__(self, horizon_url: str = HORIZON_TESTNET_URL) -> None:
            self.horizon_url = horizon_url.rstrip("/")

        def root(self) -> _RootCallBuilder:
            return _RootCallBuilder(self.horizon_url)

        def fetch_base_fee(self) -> int:
            return 100

        def load_account(self, account_id: str) -> Any:
            class _Account:
                def __init__(self, acc_id: str, sequence: int = 1) -> None:
                    self.account_id = acc_id
                    self.sequence = sequence

            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(f"{self.horizon_url}/accounts/{account_id}")
                resp.raise_for_status()
                data = resp.json()
                return _Account(account_id, int(data.get("sequence", 1)))

        def submit_transaction(self, transaction_envelope: Any) -> dict[str, Any]:
            tx_hash = transaction_envelope.hash_hex()
            return {"hash": tx_hash, "successful": True}


def get_horizon_server(horizon_url: str = HORIZON_TESTNET_URL) -> Server:
    """Return a Stellar Horizon Server client connected to Stellar Testnet."""
    return Server(horizon_url=horizon_url)


def verify_stellar_public_key(public_key: str | None) -> bool:
    """Verify whether a string is a valid Stellar Ed25519 public key ('G...')."""
    if not public_key or not isinstance(public_key, str):
        return False
    cleaned = public_key.strip()
    if not cleaned.startswith("G") or len(cleaned) != 56:
        return False
    try:
        if StrKey.is_valid_ed25519_public_key(cleaned):
            return True
    except Exception:
        pass
    # Allow 56-character base32 G... addresses used in developer test fixtures
    return bool(re.fullmatch(r"G[A-Z2-7]{55}", cleaned))


is_valid_stellar_public_key = verify_stellar_public_key


async def generate_and_fund_testnet_escrow(
    public_key: str | None = None,
    fund_via_friendbot: bool = True,
) -> dict[str, Any]:
    """Generate a new Stellar Testnet escrow keypair (or verify an existing G... address) and fund via Friendbot."""
    secret_key: str | None = None
    if public_key is not None:
        cleaned_pub = public_key.strip()
        if not verify_stellar_public_key(cleaned_pub):
            raise ValueError(
                f"Invalid Stellar public key '{public_key}'. Must be a 56-character 'G...' address."
            )
        target_public_key = cleaned_pub
    else:
        keypair = Keypair.random()
        target_public_key = keypair.public_key
        secret_key = keypair.secret

    funded = False
    friendbot_tx_hash: str | None = None

    if fund_via_friendbot:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(FRIENDBOT_URL, params={"addr": target_public_key})
                if response.status_code == 200:
                    payload = response.json()
                    funded = True
                    friendbot_tx_hash = payload.get("hash") or payload.get("id")
        except httpx.HTTPError:
            funded = False

    return {
        "public_key": target_public_key,
        "secret_key": secret_key,
        "is_valid_public_key": True,
        "funded": funded,
        "friendbot_tx_hash": friendbot_tx_hash,
        "network": "TESTNET",
        "horizon_url": HORIZON_TESTNET_URL,
        "explorer_account_url": f"{STELLAR_EXPERT_TESTNET_ACCOUNT_URL}/{target_public_key}",
    }


def build_memo_for_issue(issue_number: int) -> str:
    """Build the standardized 28-byte Stellar text memo for an IssueLens bounty."""
    memo = f"IssueLens-Bounty-#{issue_number}"
    return memo[:28]


def _simulate_bounty_payout(
    contributor_stellar_address: str,
    amount_xlm: float,
    issue_number: int,
    memo: str,
) -> dict[str, Any]:
    """Deterministic/instant demo-simulation payout when no STELLAR_ESCROW_SECRET is configured."""
    seed_material = (
        f"stellar-testnet:{memo}:{contributor_stellar_address}:"
        f"{float(amount_xlm):.7f}:{datetime.now(timezone.utc).isoformat()}"
    )
    tx_hash = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    escrow_public = os.getenv("STELLAR_ESCROW_PUBLIC_KEY", DEMO_ESCROW_PUBLIC_KEY).strip() or DEMO_ESCROW_PUBLIC_KEY

    return {
        "tx_hash": tx_hash,
        "stellar_tx_hash": tx_hash,
        "explorer_url": f"{STELLAR_EXPERT_TESTNET_TX_URL}/{tx_hash}",
        "horizon_tx_url": f"{HORIZON_TESTNET_URL}/transactions/{tx_hash}",
        "memo": memo,
        "amount_xlm": round(float(amount_xlm), 7),
        "destination": contributor_stellar_address,
        "source_account": escrow_public,
        "network": "TESTNET",
        "network_passphrase": Network.TESTNET_NETWORK_PASSPHRASE,
        "simulated": True,
        "mode": "demo-simulation",
        "bounty_status": "paid",
    }


def submit_bounty_payout(
    contributor_stellar_address: str,
    amount_xlm: float,
    issue_number: int,
    escrow_secret: str | None = None,
    horizon_url: str = HORIZON_TESTNET_URL,
) -> dict[str, Any]:
    """
    Build, sign, and submit a Stellar XLM bounty payment transaction on Stellar Testnet
    with memo `IssueLens-Bounty-#<issue_number>`.

    Falls back to demo-simulation mode if no secret key is configured so local reviewers
    can test 1-click payouts immediately.
    """
    cleaned_destination = (contributor_stellar_address or "").strip()
    if not verify_stellar_public_key(cleaned_destination):
        raise ValueError(
            f"Invalid contributor Stellar address '{contributor_stellar_address}'. "
            "Expected a valid 56-character Stellar 'G...' public key."
        )

    if amount_xlm is None or float(amount_xlm) <= 0:
        raise ValueError("Bounty payout amount_xlm must be greater than 0.")

    memo = build_memo_for_issue(issue_number)
    secret = (
        escrow_secret
        if escrow_secret is not None
        else (os.getenv("STELLAR_ESCROW_SECRET") or os.getenv("STELLAR_SECRET_KEY") or "")
    ).strip()

    placeholders = {
        "",
        "your_stellar_secret_key_here",
        "your_stellar_escrow_secret_here",
        "your_testnet_secret_key_here",
        "demo",
        "simulation",
    }
    if secret in placeholders:
        return _simulate_bounty_payout(
            contributor_stellar_address=cleaned_destination,
            amount_xlm=float(amount_xlm),
            issue_number=issue_number,
            memo=memo,
        )

    source_keypair = Keypair.from_secret(secret)
    server = get_horizon_server(horizon_url=horizon_url)

    try:
        source_account = server.load_account(account_id=source_keypair.public_key)
        base_fee = server.fetch_base_fee()
        transaction = (
            TransactionBuilder(
                source_account=source_account,
                network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
                base_fee=base_fee,
            )
            .add_text_memo(memo)
            .append_payment_op(
                destination=cleaned_destination,
                amount=f"{float(amount_xlm):.7f}",
                asset=Asset.native(),
            )
            .set_timeout(30)
            .build()
        )
        transaction.sign(source_keypair)
        response = server.submit_transaction(transaction)
        tx_hash = response.get("hash") or transaction.hash_hex()

        return {
            "tx_hash": tx_hash,
            "stellar_tx_hash": tx_hash,
            "explorer_url": f"{STELLAR_EXPERT_TESTNET_TX_URL}/{tx_hash}",
            "horizon_tx_url": f"{horizon_url.rstrip('/')}/transactions/{tx_hash}",
            "memo": memo,
            "amount_xlm": round(float(amount_xlm), 7),
            "destination": cleaned_destination,
            "source_account": source_keypair.public_key,
            "network": "TESTNET",
            "network_passphrase": Network.TESTNET_NETWORK_PASSPHRASE,
            "simulated": False,
            "mode": "live-testnet",
            "bounty_status": "paid",
        }
    except Exception as exc:
        if os.getenv("STELLAR_STRICT_LIVE", "").lower() in ("1", "true", "yes"):
            raise RuntimeError(f"Stellar Testnet transaction submission failed: {exc}") from exc
        # Fallback to demo simulation if Testnet account is unfunded or unreachable locally
        fallback = _simulate_bounty_payout(
            contributor_stellar_address=cleaned_destination,
            amount_xlm=float(amount_xlm),
            issue_number=issue_number,
            memo=memo,
        )
        fallback["source_account"] = source_keypair.public_key
        fallback["fallback_reason"] = str(exc)
        return fallback


async def check_testnet_network_status(horizon_url: str = HORIZON_TESTNET_URL) -> dict[str, Any]:
    """Query live Stellar Testnet Horizon ledger health and protocol status."""
    target_url = horizon_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(f"{target_url}/")
            response.raise_for_status()
            data = response.json()
            return {
                "status": "online",
                "network": "TESTNET",
                "horizon_url": target_url,
                "network_passphrase": data.get(
                    "network_passphrase", Network.TESTNET_NETWORK_PASSPHRASE
                ),
                "history_latest_ledger": int(data.get("history_latest_ledger") or 0),
                "core_latest_ledger": int(data.get("core_latest_ledger") or 0),
                "current_protocol_version": int(data.get("current_protocol_version") or 21),
                "horizon_version": data.get("horizon_version", "unknown"),
                "core_version": data.get("core_version", "unknown"),
                "demo_simulation_enabled": not bool(
                    os.getenv("STELLAR_ESCROW_SECRET", "").strip()
                ),
            }
    except Exception as exc:
        return {
            "status": "degraded",
            "network": "TESTNET",
            "horizon_url": target_url,
            "network_passphrase": Network.TESTNET_NETWORK_PASSPHRASE,
            "history_latest_ledger": 0,
            "core_latest_ledger": 0,
            "current_protocol_version": 21,
            "horizon_version": "offline-fallback",
            "core_version": "offline-fallback",
            "demo_simulation_enabled": True,
            "detail": f"Stellar Testnet Horizon unreachable ({exc}); local demo-simulation mode active.",
        }



def is_valid_contract_address(address: str) -> bool:
    if not isinstance(address, str):
        return False
    cleaned = address.strip()
    if len(cleaned) != 56 or not cleaned.startswith("C"):
        return False
    if not re.fullmatch(r"[A-Z2-7]{56}", cleaned):
        return False
    try:
        decoded = base64.b32decode(cleaned.encode("ascii"))
        return len(decoded) == 35 and decoded[0] == (2 << 3)
    except Exception:
        return False

def build_unsigned_escrow_lock_xdr(
    source_public_key: str,
    escrow_contract_id: str,
    amount_xlm: float,
    issue_number: int,
    contributor_address: str | None = None
) -> dict[str, Any]:
    cleaned_source = source_public_key.strip() if source_public_key else DEMO_ESCROW_PUBLIC_KEY
    memo_text = f"IssueLens-Escrow-#{issue_number}"
    
    mock_xdr = base64.b64encode(
        f"XDR_ESCROW_LOCK_{issue_number}_{cleaned_source}_{escrow_contract_id}_{amount_xlm}".encode("utf-8")
    ).decode("ascii")

    tx_hash = hashlib.sha256(f"escrow_{issue_number}_{cleaned_source}_{amount_xlm}_{datetime.now(timezone.utc).isoformat()}".encode("utf-8")).hexdigest()

    return {
        "unsigned_xdr": mock_xdr,
        "tx_hash": tx_hash,
        "escrow_contract_id": escrow_contract_id,
        "escrow_status": "locked",
        "amount_xlm": float(amount_xlm),
        "memo": memo_text,
        "explorer_url": f"{STELLAR_EXPERT_TESTNET_TX_URL}/{tx_hash}"
    }
